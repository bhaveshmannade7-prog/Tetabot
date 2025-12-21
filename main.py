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

# New: Group ID where movie will be uploaded
# Example: -1001234567890
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
# High workers for downloading
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

# --- NETWORK ENGINE ---
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

# --- 1. SMART SEARCH ---
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
        
        # Select all potential movie containers
        candidates = soup.select('ul.recent-movies li, article.post, div.result-item')
        
        query_words = query.lower().split()
        
        for item in candidates:
            a_tag = item.find('a')
            if not a_tag: continue
            
            url = a_tag.get('href')
            
            # Title extraction
            title = ""
            if item.find('figcaption'): title = item.find('figcaption').text.strip()
            elif a_tag.get('title'): title = a_tag.get('title')
            else: title = a_tag.text.strip()
            
            if not url or not title: continue
            
            # Relevance Filter
            if any(w in title.lower() for w in query_words):
                clean_title = title.replace("Download", "").replace("Full Movie", "").strip()
                results.append({"title": clean_title, "url": url})
                if len(results) >= 8: break
        
        return results
    except Exception as e:
        logger.error(f"Search Error: {e}")
        return []

# --- 2. FIND STREAMING LINKS ---
def find_streaming_link(url):
    """
    Parses the movie page to find 'Watch Online' or 'Stream' section.
    """
    logger.info(f"📂 Parsing for Stream: {url}")
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # Logic: Find links that say "Watch Online", "Stream", "Instant"
        # usually below download links
        stream_targets = []
        
        all_links = soup.find_all('a', href=True)
        for a in all_links:
            text = a.text.lower()
            href = a['href']
            
            # Keywords for streaming
            if "watch" in text or "online" in text or "stream" in text or "hubcloud" in href or "embed" in href:
                if "http" in href and "category" not in href:
                    label = f"▶️ {a.text.strip()[:30]}"
                    if not any(s['url'] == href for s in stream_targets):
                        stream_targets.append({"label": label, "url": href})
        
        # If no explicit stream links, grab the standard download links too
        # because yt-dlp can stream from download links often
        if not stream_targets:
            for a in all_links:
                if "720p" in a.text or "1080p" in a.text:
                     label = f"📥 {a.text.strip()[:30]}"
                     stream_targets.append({"label": label, "url": a['href']})

        return stream_targets
    except Exception as e:
        logger.error(f"Stream Parse Error: {e}")
        return []

# --- 3. DOWNLOAD & UPLOAD ENGINE ---
def process_media_task(url, quality_setting):
    """
    Downloads media and Uploads to Group.
    quality_setting: 'best', '720', '480'
    """
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # 1. Resolve Landing Page (Bypass)
    # Most stream links are wrappers. We need the final URL.
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        # Follow redirects to get real URL
        final_url = session.get(url, allow_redirects=True, timeout=15).url
    except:
        final_url = url

    logger.info(f"⬇️ Downloading from: {final_url} | Quality: {quality_setting}")

    # 2. Configure yt-dlp for Size Control
    # We use format selection to control size/quality
    if quality_setting == '480':
        fmt = 'bestvideo[height<=480]+bestaudio/best[height<=480]/best'
    elif quality_setting == '720':
        fmt = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
    else:
        fmt = 'best' # Max quality

    filename_template = f'{DOWNLOAD_DIR}/movie_{timestamp}.%(ext)s'

    opts = {
        'outtmpl': filename_template,
        'format': fmt,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'noplaylist': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    if os.path.exists(COOKIE_FILE): opts['cookiefile'] = COOKIE_FILE

    try:
        # DOWNLOAD
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(final_url, download=True)
            fpath = ydl.prepare_filename(info)
            
            # Correction if merged
            base, _ = os.path.splitext(fpath)
            if not os.path.exists(fpath) and os.path.exists(base+".mp4"):
                fpath = base+".mp4"
            elif not os.path.exists(fpath) and os.path.exists(base+".mkv"):
                fpath = base+".mkv"

            if not os.path.exists(fpath):
                return {"status": False, "error": "Download failed (File not found)"}

            # Size Check (Telegram Limit)
            fsize = os.path.getsize(fpath)
            
            # NOTE: Render Free cannot re-encode. We just check size.
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
    
    grp_status = "✅ Connected" if TELEGRAM_GROUP_ID else "⚠️ Not Set (Check ENV)"
    
    txt = (
        f"👋 **Stream-to-Group Bot!**\n"
        f"📂 Group: {grp_status}\n\n"
        "🎬 **How to use:**\n"
        "1. `/search MovieName`\n"
        "2. Select Movie\n"
        "3. Select Stream Source\n"
        "4. Choose Quality (Low = Small Size)\n"
        "5. Bot uploads to Group!"
    )
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ `/add`, `/remove`"
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

# 1. SEARCH
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Kalki`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching: `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_website, query)
    
    if not results:
        await update.message.reply_text("❌ No results found.")
        return
    
    context.user_data['search_res'] = results
    keyboard = []
    for idx, movie in enumerate(results):
        keyboard.append([InlineKeyboardButton(f"🎬 {movie['title']}", callback_data=f"sel_{idx}")])
        
    await update.message.reply_text(f"✅ Found {len(results)} movies:", reply_markup=InlineKeyboardMarkup(keyboard))

# 2. SELECT MOVIE -> FIND STREAMS
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # A. Movie Selected
    if data.startswith("sel_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): return
        
        movie = results[idx]
        await query.edit_message_text(f"🔄 Fetching Stream Links for:\n**{movie['title']}**...")
        
        loop = asyncio.get_running_loop()
        streams = await loop.run_in_executor(executor, find_streaming_link, movie['url'])
        
        if not streams:
            await query.edit_message_text("❌ No Streaming/Watch Online links found.")
            return
        
        context.user_data['streams'] = streams
        context.user_data['movie_title'] = movie['title']
        
        keyboard = []
        for i, s in enumerate(streams):
            keyboard.append([InlineKeyboardButton(s['label'], callback_data=f"stm_{i}")])
            
        await query.edit_message_text("👇 Select Source:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # B. Source Selected -> Choose Quality (Size Control)
    if data.startswith("stm_"):
        idx = int(data.split("_")[1])
        streams = context.user_data.get('streams', [])
        if idx >= len(streams): return
        
        selected_stream = streams[idx]
        context.user_data['target_url'] = selected_stream['url']
        
        # Quality Selection Menu
        kb = [
            [InlineKeyboardButton("📱 480p (Small Size)", callback_data="q_480")],
            [InlineKeyboardButton("🎥 720p (Medium)", callback_data="q_720")],
            [InlineKeyboardButton("💎 Best Quality (Large)", callback_data="q_best")]
        ]
        await query.edit_message_text(f"⚙️ Select Quality for:\n{selected_stream['label']}\n\n(Lower quality = Faster Upload)", reply_markup=InlineKeyboardMarkup(kb))
        return

    # C. Quality Selected -> Download & Upload
    if data.startswith("q_"):
        quality = data.split("_")[1]
        url = context.user_data.get('target_url')
        title = context.user_data.get('movie_title', 'Movie')
        
        if not TELEGRAM_GROUP_ID:
            await query.edit_message_text("❌ Error: Group ID not set in Environment Variables.")
            return

        await query.edit_message_text(f"⬇️ **Downloading...**\nQuality: {quality}p\nTitle: {title}\n\n(This may take time, please wait...)")
        
        loop = asyncio.get_running_loop()
        # Run heavy task
        result = await loop.run_in_executor(executor, process_media_task, url, quality)
        
        if not result['status']:
            await query.edit_message_text(f"❌ Download Failed:\n`{result.get('error')}`", parse_mode=ParseMode.MARKDOWN)
            return
            
        fpath = result['path']
        fsize = result['size']
        
        # TELEGRAM LIMIT CHECK
        # Standard Bot: 50MB (approx 52428800 bytes)
        # Local API: 2GB
        LIMIT = 49 * 1024 * 1024 # Safe limit for standard bot
        
        if fsize > LIMIT:
            await query.edit_message_text(
                f"❌ **Upload Failed!**\n"
                f"📁 File Size: `{get_readable_size(fsize)}`\n"
                f"⛔ Telegram Limit: 50MB\n\n"
                "Cloud server cannot upload large files without Local API.\n"
                "Try selecting '480p' quality."
            )
            os.remove(fpath)
            return

        await query.edit_message_text(f"📤 **Uploading to Group...**\nSize: {get_readable_size(fsize)}")
        
        try:
            # Upload to Group
            with open(fpath, 'rb') as f:
                await context.bot.send_video(
                    chat_id=TELEGRAM_GROUP_ID,
                    video=f,
                    caption=f"🎬 **{title}**\n💿 Quality: {quality}p\n🤖 Uploaded by Bot",
                    width=1280 if quality=='720' else 854,
                    height=720 if quality=='720' else 480,
                    duration=result.get('duration'),
                    supports_streaming=True,
                    write_timeout=300, # 5 min timeout for upload
                    read_timeout=300
                )
            
            await query.edit_message_text("✅ **Successfully Uploaded to Group!**")
            
        except Exception as e:
            logger.error(f"Upload Error: {e}")
            await query.edit_message_text("❌ Error sending to group. (Timeout or Permission issue)")
        finally:
            if os.path.exists(fpath): os.remove(fpath)

# --- STARTUP ---
async def main():
    # High timeouts for large file uploads
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
            
