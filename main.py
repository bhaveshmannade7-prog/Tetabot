import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import re
import cloudscraper
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
import yt_dlp

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
try:
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
except:
    OWNER_ID = 0

TELEGRAM_GROUP_ID = os.getenv("GROUP_ID") 
COOKIES_ENV = os.getenv("COOKIES_CONTENT")
TARGET_DOMAIN = os.getenv("WEBSITE_URL", "https://hdhub4u.rehab").rstrip("/")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("StreamBot")

# --- DATA ---
def load_users():
    if not os.path.exists(DATA_FILE): return {OWNER_ID}
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            users = set(data.get("users", []))
            users.add(OWNER_ID)
            return users
    except: return {OWNER_ID}

def save_users(users_set):
    with open(DATA_FILE, 'w') as f:
        json.dump({"users": list(users_set)}, f)

AUTHORIZED_USERS = load_users()

# --- COOKIES ---
def setup_cookies():
    valid_lines = ["# Netscape HTTP Cookie File"]
    if COOKIES_ENV and len(COOKIES_ENV) > 10:
        lines = COOKIES_ENV.split('\n')
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                valid_lines.append("\t".join(parts))
    with open(COOKIE_FILE, 'w') as f:
        f.write("\n".join(valid_lines))
        f.write("\n")

setup_cookies()

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=4)

# --- UTILS ---
def get_readable_size(size_in_bytes):
    if not size_in_bytes: return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0: return f"{size_in_bytes:.1f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.1f} TB"

async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 **Access Denied!**")
        except: pass
        return False
    return True

# --- SCRAPER INSTANCE ---
def get_scraper():
    return cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

def get_cookies_dict():
    cookies = {}
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r') as f:
                for line in f:
                    if not line.startswith("#") and line.strip():
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookies[parts[5]] = parts[6]
        except: pass
    return cookies

# --- 1. SEARCH ---
def search_website(query):
    search_url = f"{TARGET_DOMAIN}/?s={query.replace(' ', '+')}"
    logger.info(f"🔎 Searching: {search_url}")
    try:
        scraper = get_scraper()
        resp = scraper.get(search_url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        results = []
        candidates = soup.select('ul.recent-movies li, article.post, div.result-item')
        
        query_words = query.lower().split()
        for item in candidates:
            a_tag = item.find('a')
            if not a_tag: continue
            url = a_tag.get('href')
            title = ""
            if item.find('figcaption'): title = item.find('figcaption').text.strip()
            elif a_tag.get('title'): title = a_tag.get('title')
            else: title = a_tag.text.strip()
            
            if not url or not title: continue
            
            if any(w in title.lower() for w in query_words):
                clean_title = title.replace("Download", "").replace("Full Movie", "").strip()
                results.append({"title": clean_title, "url": url})
                if len(results) >= 8: break
        return results
    except Exception as e:
        logger.error(f"Search Error: {e}")
        return []

# --- 2. FIND STREAMS ---
def find_streaming_link(url):
    logger.info(f"📂 Parsing: {url}")
    try:
        scraper = get_scraper()
        resp = scraper.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        stream_targets = []
        
        all_links = soup.find_all('a', href=True)
        for a in all_links:
            text = a.text.lower()
            href = a['href']
            
            # Smart Keyword Match
            if any(x in href for x in ['hubcdn', 'hubcloud', 'hdstream', 'drive', 'file', 'fans', 'wish']):
                if "http" in href:
                    label = f"▶️ {a.text.strip()[:30]}"
                    if not label.strip() or "Click" in label: label = "▶️ Stream Link"
                    if not any(s['url'] == href for s in stream_targets):
                        stream_targets.append({"label": label, "url": href})
        
        if not stream_targets:
            for a in all_links:
                if "watch" in a.text.lower() or "stream" in a.text.lower():
                     label = f"▶️ {a.text.strip()[:30]}"
                     stream_targets.append({"label": label, "url": a['href']})

        return stream_targets
    except Exception as e:
        logger.error(f"Parse Error: {e}")
        return []

# --- 3. HUB-CDN DECRYPTER (Strict Validator) ---
def resolve_hubcdn_logic(url):
    """
    Decodes HubCDN links with Strict Validation to avoid '${link}' errors.
    """
    logger.info(f"🕵️ Decrypting HubCDN: {url}")
    scraper = get_scraper()
    
    try:
        resp = scraper.get(url, timeout=15, allow_redirects=True)
        html = resp.text
        
        # --- STRATEGY 1: Strict Regex for M3U8/MP4 ---
        # Look for http/https followed by .m3u8 or .mp4
        # We ignore anything that doesn't start with http
        
        regex_patterns = [
            r'file:\s*["\'](https?://[^"\']+\.m3u8)["\']',
            r'source:\s*["\'](https?://[^"\']+\.mp4)["\']',
            r'src:\s*["\'](https?://[^"\']+\.m3u8)["\']',
            r'["\'](https?://[^"\']+\.m3u8)["\']'
        ]
        
        for p in regex_patterns:
            matches = re.findall(p, html)
            for m in matches:
                # CRITICAL CHECK: Is it a valid URL?
                if "http" in m and "${" not in m and "{" not in m:
                    logger.info(f"✅ Found Clean Match: {m}")
                    return m, False # False = Not Generic Mode

        # --- STRATEGY 2: Check for packed or hidden variables ---
        # Some players use var url = "https://...";
        
        var_matches = re.findall(r'var\s+\w+\s*=\s*["\'](https?://[^"\']+)["\']', html)
        for m in var_matches:
            if (".m3u8" in m or ".mp4" in m) and "${" not in m:
                return m, False

        # --- STRATEGY 3: Fallback to Generic ---
        # If we can't find the clean link, we pass the original URL to yt-dlp
        # BUT we must handle the case where yt-dlp might fail on the original URL.
        
        logger.warning("⚠️ No direct regex match. Using Generic Mode on Original URL.")
        return url, True # True = Force Generic Mode

    except Exception as e:
        logger.error(f"Decryption Error: {e}")
        return url, True

# --- 4. DOWNLOAD ENGINE ---
def process_media_task(url, quality_setting):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # 1. ATTEMPT DECRYPTION
    final_url, use_generic = resolve_hubcdn_logic(url)
    
    # SAFETY CHECK: Ensure final_url is actually a string and looks like a URL
    if not final_url or not isinstance(final_url, str) or not final_url.startswith("http"):
        logger.error(f"❌ Invalid URL detected: {final_url}")
        return {"status": False, "error": "Invalid URL found during decryption."}

    logger.info(f"⬇️ Downloading: {final_url} (Generic: {use_generic})")

    # Configure yt-dlp
    if quality_setting == '480':
        fmt = 'bestvideo[height<=480]+bestaudio/best[height<=480]/best'
    elif quality_setting == '720':
        fmt = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
    else:
        fmt = 'best'

    filename_template = f'{DOWNLOAD_DIR}/movie_{timestamp}.%(ext)s'

    opts = {
        'outtmpl': filename_template,
        'format': fmt,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'noplaylist': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0 Safari/537.36',
        'hls_prefer_native': True,
        'http_headers': {
            'Referer': url,
            'Origin': '/'.join(url.split('/')[:3])
        }
    }
    
    if use_generic:
        opts['force_generic_extractor'] = True
        opts['nocheckcertificate'] = True

    if os.path.exists(COOKIE_FILE): opts['cookiefile'] = COOKIE_FILE

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(final_url, download=True)
            fpath = ydl.prepare_filename(info)
            
            base, _ = os.path.splitext(fpath)
            for ext in ['.mp4', '.mkv', '.webm', '.ts']:
                if os.path.exists(base + ext):
                    fpath = base + ext
                    break

            if not os.path.exists(fpath):
                return {"status": False, "error": "File not found after download."}

            fsize = os.path.getsize(fpath)
            
            return {
                "status": True,
                "path": fpath,
                "size": fsize,
                "title": info.get('title', 'Movie'),
                "duration": info.get('duration')
            }

    except Exception as e:
        return {"status": False, "error": str(e)}

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    grp = "✅ Set" if TELEGRAM_GROUP_ID else "⚠️ Missing"
    txt = f"👋 **Stream Bot (Strict Mode)**\n📂 Group: {grp}\n\n`/search MovieName`"
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ Admin: `/add`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        cmd, target = update.message.text.split()
        target = int(target)
        if cmd == "/add": AUTHORIZED_USERS.add(target)
        elif cmd == "/remove": AUTHORIZED_USERS.discard(target)
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text("✅ Done")
    except: await update.message.reply_text("Usage: `/add 12345`")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Pathaan`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_website, query)
    
    if not results:
        await update.message.reply_text("❌ No results found.")
        return
    
    context.user_data['search_res'] = results
    kb = []
    for idx, m in enumerate(results):
        kb.append([InlineKeyboardButton(f"🎬 {m['title']}", callback_data=f"sel_{idx}")])
        
    await update.message.reply_text(f"✅ Found {len(results)}:", reply_markup=InlineKeyboardMarkup(kb))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("sel_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): return
        
        movie = results[idx]
        await query.edit_message_text(f"🔄 Scanning Streams for:\n**{movie['title']}**...")
        
        loop = asyncio.get_running_loop()
        streams = await loop.run_in_executor(executor, find_streaming_link, movie['url'])
        
        if not streams:
            await query.edit_message_text("❌ No streams found.")
            return
        
        context.user_data['streams'] = streams
        context.user_data['m_title'] = movie['title']
        
        kb = []
        for i, s in enumerate(streams):
            kb.append([InlineKeyboardButton(s['label'], callback_data=f"stm_{i}")])
        await query.edit_message_text("👇 Select Source:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("stm_"):
        idx = int(data.split("_")[1])
        streams = context.user_data.get('streams', [])
        if idx >= len(streams): return
        
        context.user_data['t_url'] = streams[idx]['url']
        
        kb = [
            [InlineKeyboardButton("📱 480p", callback_data="q_480")],
            [InlineKeyboardButton("🎥 720p", callback_data="q_720")],
            [InlineKeyboardButton("💎 Best", callback_data="q_best")]
        ]
        await query.edit_message_text(f"⚙️ Select Quality for:\n{streams[idx]['label']}", reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("q_"):
        qual = data.split("_")[1]
        url = context.user_data.get('t_url')
        title = context.user_data.get('m_title', 'Movie')
        
        if not TELEGRAM_GROUP_ID:
            await query.edit_message_text("❌ ENV Error: GROUP_ID missing.")
            return

        await query.edit_message_text(f"⬇️ **Downloading...**\nQuality: {qual}p\n\n(Wait ~2-5 mins...)")
        
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(executor, process_media_task, url, qual)
        
        if not res['status']:
            await query.edit_message_text(f"❌ Error:\n`{res.get('error')}`", parse_mode=ParseMode.MARKDOWN)
            return
            
        fpath = res['path']
        fsize = res['size']
        
        if fsize > 49 * 1024 * 1024:
            await query.edit_message_text(f"❌ File too big ({get_readable_size(fsize)}).\nTelegram Limit 50MB.\nTry 480p.")
            os.remove(fpath)
            return

        await query.edit_message_text(f"📤 **Uploading to Group...**\nSize: {get_readable_size(fsize)}")
        
        try:
            with open(fpath, 'rb') as f:
                await context.bot.send_video(
                    chat_id=TELEGRAM_GROUP_ID,
                    video=f,
                    caption=f"🎬 **{title}**\n💿 Quality: {qual}p",
                    supports_streaming=True,
                    read_timeout=300, write_timeout=300
                )
            await query.edit_message_text("✅ Sent to Group!")
        except Exception as e:
            logger.error(f"Upload Error: {e}")
            await query.edit_message_text("❌ Upload Failed.")
        finally:
            if os.path.exists(fpath): os.remove(fpath)

# --- STARTUP ---
async def main():
    req = HTTPXRequest(connection_pool_size=10, read_timeout=300, write_timeout=300, connect_timeout=60)
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("search", search_command))
    app_bot.add_handler(CommandHandler(["add", "remove"], admin_ops))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    
    await app_bot.initialize()
    if WEBHOOK_URL:
        await app_bot.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", allowed_updates=Update.ALL_TYPES)
    return app_bot

try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
bot_app = loop.run_until_complete(main())

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        loop.run_until_complete(bot_app.process_update(update))
        return "OK"
    return "Invalid"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
    
