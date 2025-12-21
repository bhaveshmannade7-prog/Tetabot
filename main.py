import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import re
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

# --- NETWORK HEADER ENGINE ---
def get_headers(referer=None):
    head = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer: head["Referer"] = referer
    return head

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
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(search_url, timeout=15)
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
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        stream_targets = []
        
        all_links = soup.find_all('a', href=True)
        for a in all_links:
            text = a.text.lower()
            href = a['href']
            
            # Keywords: hubcdn, hubcloud, hdstream, etc.
            if any(x in href for x in ['hubcdn', 'hubcloud', 'hdstream', 'drive', 'file', 'fans']):
                if "http" in href:
                    label = f"▶️ {a.text.strip()[:30]}"
                    if not label.strip() or "Click" in label: label = "▶️ Stream Link"
                    if not any(s['url'] == href for s in stream_targets):
                        stream_targets.append({"label": label, "url": href})
        
        # Also grab "Watch Online" buttons
        if not stream_targets:
            for a in all_links:
                if "watch" in a.text.lower() or "stream" in a.text.lower():
                     label = f"▶️ {a.text.strip()[:30]}"
                     stream_targets.append({"label": label, "url": a['href']})

        return stream_targets
    except Exception as e:
        logger.error(f"Parse Error: {e}")
        return []

# --- 3. UNIVERSAL STREAM SNIFFER (The "Jad Se Khatam" Fix) ---
def resolve_universal_stream(url):
    """
    Brute-force searches for .m3u8 or .mp4 links inside the target page.
    This bypasses the need for yt-dlp to support the specific site.
    """
    logger.info(f"🕵️ Sniffing URL: {url}")
    
    session = requests.Session()
    # Use generic headers, pass Referer as the streaming site itself
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": TARGET_DOMAIN
    })
    
    try:
        # 1. Fetch the HubCDN/Stream Page
        resp = session.get(url, timeout=15, allow_redirects=True)
        html = resp.text
        
        # 2. BRUTE FORCE REGEX: Look for ANY http link ending in .m3u8 or .mp4
        # This catches "file:", "src:", "source:", or even hidden variables.
        
        # Priority 1: HLS Stream (.m3u8) - Best for streaming
        m3u8_matches = re.findall(r'(https?://[^"\s\']+\.m3u8)', html)
        if m3u8_matches:
            # Take the master/index one if possible, otherwise first one
            for link in m3u8_matches:
                if "master" in link or "index" in link:
                    logger.info(f"✅ Found Master M3U8: {link}")
                    return link
            logger.info(f"✅ Found M3U8: {m3u8_matches[0]}")
            return m3u8_matches[0]

        # Priority 2: Direct Video (.mp4)
        mp4_matches = re.findall(r'(https?://[^"\s\']+\.mp4)', html)
        if mp4_matches:
            logger.info(f"✅ Found MP4: {mp4_matches[0]}")
            return mp4_matches[0]

        # Priority 3: Fallback API (If regex fails completely)
        # This uses an external service if our local sniffer fails
        logger.warning("❌ Sniffer failed. Trying external API fallback...")
        api_url = f"https://teraboxvideodownloader.nepcoderdevs.workers.dev/?url={url}"
        api_resp = requests.get(api_url, timeout=10).json()
        
        # Check standard JSON location for links
        if "response" in api_resp:
             res = api_resp["response"]
             if isinstance(res, list) and res:
                 link = res[0].get("resolutions", {}).get("Fast Download")
                 if link: return link

        # If all fails, return None (So we don't pass bad URL to yt-dlp)
        return None
        
    except Exception as e:
        logger.error(f"Sniffer Error: {e}")
        return None

# --- 4. DOWNLOADER ENGINE ---
def process_media_task(url, quality_setting):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # STEP 1: SNIFF THE REAL LINK
    # We NEVER pass the hubcdn URL to yt-dlp directly anymore.
    final_url = resolve_universal_stream(url)
    
    if not final_url:
        return {"status": False, "error": "Could not extract video stream. Site might be protected."}

    logger.info(f"⬇️ Downloading Real Link: {final_url}")

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
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
        'hls_prefer_native': True,
        # Important: Pass the original page as referer, some m3u8 links need it
        'http_headers': {
            'Referer': url 
        }
    }
    
    if os.path.exists(COOKIE_FILE): opts['cookiefile'] = COOKIE_FILE

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(final_url, download=True)
            fpath = ydl.prepare_filename(info)
            
            # Handle extension fix
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
    txt = f"👋 **Stream Bot!**\n📂 Group: {grp}\n\n`/search MovieName`"
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
    
    # 1. MOVIE SELECTED
    if data.startswith("sel_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): return
        
        movie = results[idx]
        await query.edit_message_text(f"🔄 Finding Streams for:\n**{movie['title']}**...")
        
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

    # 2. SOURCE SELECTED
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

    # 3. DOWNLOAD & UPLOAD
    if data.startswith("q_"):
        qual = data.split("_")[1]
        url = context.user_data.get('t_url')
        title = context.user_data.get('m_title', 'Movie')
        
        if not TELEGRAM_GROUP_ID:
            await query.edit_message_text("❌ ENV Error: GROUP_ID missing.")
            return

        await query.edit_message_text(f"⬇️ **Downloading...**\nQuality: {qual}p\n\n(Scanning for hidden video file...)")
        
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(executor, process_media_task, url, qual)
        
        if not res['status']:
            await query.edit_message_text(f"❌ Error:\n`{res.get('error')}`", parse_mode=ParseMode.MARKDOWN)
            return
            
        fpath = res['path']
        fsize = res['size']
        
        # 50MB Limit Check
        if fsize > 49 * 1024 * 1024:
            await query.edit_message_text(f"❌ File too big ({get_readable_size(fsize)}).\nTelegram Bot limit is 50MB.\nTry 480p.")
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
    
