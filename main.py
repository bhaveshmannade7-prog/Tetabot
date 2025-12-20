import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import re
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

COOKIES_ENV = os.getenv("COOKIES_CONTENT")
API_MODE = os.getenv("API_MODE", "standard")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

# Limit Settings
if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024  # 2GB
    SERVER_TAG = "🚀 Local Server (2GB)"
else:
    BASE_URL = None
    MAX_FILE_SIZE = 49 * 1024 * 1024    # 50MB
    SERVER_TAG = "☁️ Standard Cloud (50MB)"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BotEngine")

# --- DATA MANAGEMENT ---
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

# --- COOKIE SETUP (YouTube Only) ---
def setup_cookies():
    if not COOKIES_ENV or len(COOKIES_ENV) < 10: return
    try:
        lines = COOKIES_ENV.split('\n')
        valid_lines = ["# Netscape HTTP Cookie File"]
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                valid_lines.append("\t".join(parts))
        with open(COOKIE_FILE, 'w') as f:
            f.write("\n".join(valid_lines))
    except Exception: pass

setup_cookies()

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=4)

# --- UTILS ---
def get_readable_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0: return f"{size_in_bytes:.1f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.1f} TB"

async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 Access Denied!")
        except: pass
        return False
    return True

# --- TERABOX SPECIAL ENGINE ---
def resolve_terabox_url(url):
    """
    Advanced Redirect Follower for Short Links
    """
    session = requests.Session()
    # Fake Browser Headers are CRITICAL here
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    })
    try:
        resp = session.head(url, allow_redirects=True, timeout=10)
        return resp.url
    except:
        return url

def download_terabox(url):
    try:
        if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
        timestamp = int(time.time())
        filename = f"{DOWNLOAD_DIR}/terabox_{timestamp}.mp4"
        
        # 1. Clean & Resolve URL
        final_url = resolve_terabox_url(url)
        
        # URL Fix: terabox.app -> terabox.com (APIs often fail on .app)
        if "terabox.app" in final_url:
            final_url = final_url.replace("terabox.app", "terabox.com")
            
        logger.info(f"Processing Terabox: {final_url}")

        # 2. API Selection (New Working Endpoints)
        # We try the most robust workers first
        api_endpoints = [
            f"https://teraboxvideodownloader.nepcoderdevs.workers.dev/?url={final_url}",
            f"https://terabox-dl.qtcloud.workers.dev/api/get-download?url={final_url}",
        ]

        direct_link = None
        file_title = f"Terabox_{timestamp}.mp4"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }

        for api in api_endpoints:
            try:
                logger.info(f"Hitting API: {api}")
                r = requests.get(api, headers=headers, timeout=20)
                
                if r.status_code != 200: continue
                
                try:
                    data = r.json()
                except: continue # Not JSON

                # Extract Link Strategy
                link_candidates = [
                    data.get("response", {}).get("0", {}).get("resolutions", {}).get("Fast Download", ""), # NepCoder structure
                    data.get("response", {}).get("0", {}).get("resolutions", {}).get("HD Video", ""),
                    data.get("downloadLink"), # QTCloud structure
                    data.get("url"),
                    data.get("dlink")
                ]
                
                # Check valid link
                for link in link_candidates:
                    if link and link.startswith("http"):
                        direct_link = link
                        break
                
                # Extract Title
                if "filename" in data: 
                    file_title = data["filename"]
                elif "response" in data:
                    file_title = data.get("response", [{}])[0].get("title", file_title)

                if direct_link: break # Found it

            except Exception as e:
                logger.error(f"API Attempt Failed: {e}")

        if not direct_link:
            return {"status": False, "error": "Terabox servers are blocking requests. Try again in 5 mins."}

        # 3. Download Content
        # Using Session to handle redirects/cookies passed by the API
        with requests.Session() as s:
            s.headers.update(headers)
            r = s.get(direct_link, stream=True, timeout=30)
            r.raise_for_status()
            
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024): # 1MB chunks
                    if chunk: f.write(chunk)
                    
        return {
            "status": True,
            "path": filename,
            "title": file_title,
            "duration": 0,
            "width": 1280,
            "height": 720
        }

    except Exception as e:
        logger.error(f"TB Download Critical: {e}")
        return {"status": False, "error": f"Failed: {str(e)[:50]}"}

# --- MAIN ENGINE ---
def run_download_sync(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    
    # 1. Detect Terabox (All variants)
    terabox_domains = ["terabox", "1024tera", "teraboxurl", "4funbox", "momerybox", "nephobox", "freeterabox"]
    if any(domain in url for domain in terabox_domains):
        return download_terabox(url)

    # 2. YT/Insta Logic (yt-dlp)
    timestamp = int(time.time())
    is_yt = "youtube.com" in url or "youtu.be" in url
    
    if is_yt:
        fmt = 'bestvideo+bestaudio/best' if quality == 'best' else f'bestvideo[height<={quality}]+bestaudio/best'
        if quality == 'audio': fmt = 'bestaudio/best'
    else:
        fmt = 'best'

    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': fmt,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    }
    
    if is_yt and os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE

    if quality == 'audio':
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    elif is_yt:
        opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            final_path = base + (".mp3" if quality == 'audio' else ".mp4")
            
            if not os.path.exists(final_path) and os.path.exists(filename):
                final_path = filename

            return {
                "status": True,
                "path": final_path,
                "title": info.get('title', 'Media'),
                "duration": info.get('duration'),
                "width": info.get('width'),
                "height": info.get('height')
            }
    except Exception as e:
        return {"status": False, "error": str(e)}

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    uid = update.effective_user.id
    txt = f"👋 **Bot Active!**\nServer: {SERVER_TAG}\n\nSend links to download."
    if uid == OWNER_ID:
        txt += "\n\n👮‍♂️ **Admin Mode Enabled**"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    cmd = update.message.text.split()[0]
    try:
        target = int(context.args[0])
        if cmd == "/add":
            AUTHORIZED_USERS.add(target)
            msg = "✅ Added"
        elif cmd == "/remove":
            if target != OWNER_ID: 
                AUTHORIZED_USERS.discard(target)
                msg = "🗑️ Removed"
            else: msg = "❌ Cannot remove owner"
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(msg)
    except: await update.message.reply_text("Usage: /add <id>")

async def show_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(f"Users: {list(AUTHORIZED_USERS)}")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if not url.startswith("http"): return

    context.user_data['url'] = url
    
    terabox_domains = ["terabox", "1024tera", "teraboxurl", "4funbox", "momerybox", "nephobox"]
    
    if any(d in url for d in terabox_domains):
        keyboard = [[InlineKeyboardButton("⬇️ Download Terabox File", callback_data="terabox")]]
        txt = "📦 **Terabox Detected!**\n(Using Advanced API)"
    elif "youtube" in url or "youtu.be" in url:
        keyboard = [
            [InlineKeyboardButton("🎵 MP3", callback_data="audio")],
            [InlineKeyboardButton("🎥 720p", callback_data="720"), InlineKeyboardButton("💎 Best", callback_data="best")]
        ]
        txt = "📺 **YouTube Detected**"
    else:
        keyboard = [[InlineKeyboardButton("⬇️ Download Media", callback_data="best")]]
        txt = "📸 **Link Detected**"

    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    data = query.data
    url = context.user_data.get('url')
    quality = 'best' if data == 'terabox' else data
    
    await query.edit_message_text(f"⚡ **Downloading...**\n⏳ Please wait...")
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, run_download_sync, url, quality)
    
    if not result['status']:
        await query.edit_message_text(f"❌ Error: {result.get('error')}")
        return

    path = result['path']
    size = os.path.getsize(path)
    
    if size > MAX_FILE_SIZE:
        await query.edit_message_text(f"❌ File too big: {get_readable_size(size)}")
        os.remove(path)
        return

    await query.edit_message_text(f"📤 **Uploading...**\n📦 {get_readable_size(size)}")
    
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id, audio=f, title=result['title'],
                    read_timeout=60, write_timeout=60, connect_timeout=30
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id, video=f, caption=result['title'],
                    supports_streaming=True, read_timeout=120, write_timeout=120, connect_timeout=30
                )
        await query.delete_message()
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await query.edit_message_text("❌ Upload Error.")
    finally:
        if os.path.exists(path): os.remove(path)

async def main():
    req = HTTPXRequest(connection_pool_size=10, read_timeout=30, write_timeout=30, connect_timeout=30)
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler(["add", "remove"], admin_ops))
    app_bot.add_handler(CommandHandler("users", show_users))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
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
