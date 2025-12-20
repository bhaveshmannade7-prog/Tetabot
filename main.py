import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import random
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

# Cookie Configuration
COOKIES_ENV = os.getenv("COOKIES_CONTENT")
TERABOX_COOKIE_VAL = os.getenv("TERABOX_COOKIE")  # Sirf 'ndus' value yahan layein
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
    SERVER_TAG = "☁️ Cloud Server (50MB)"

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

# --- COOKIE GENERATOR ---
def setup_cookies():
    valid_lines = ["# Netscape HTTP Cookie File"]
    
    # 1. Standard Cookies
    if COOKIES_ENV and len(COOKIES_ENV) > 10:
        lines = COOKIES_ENV.split('\n')
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                valid_lines.append("\t".join(parts))
    
    # 2. Terabox Specific (ndus)
    # Ye bohot zaroori hai agar aapne alag se variable set kiya hai
    if TERABOX_COOKIE_VAL and len(TERABOX_COOKIE_VAL) > 5:
        # Domain fix for Terabox
        tb_line = f".terabox.com\tTRUE\t/\tFALSE\t2147483647\tndus\t{TERABOX_COOKIE_VAL.strip()}"
        valid_lines.append(tb_line)
        # 1024tera domain ke liye bhi same cookie register karein
        tb_line_2 = f".1024tera.com\tTRUE\t/\tFALSE\t2147483647\tndus\t{TERABOX_COOKIE_VAL.strip()}"
        valid_lines.append(tb_line_2)
        logger.info("✅ Terabox Cookies Injected!")

    with open(COOKIE_FILE, 'w') as f:
        f.write("\n".join(valid_lines))
        f.write("\n")

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
        try: await update.message.reply_text("🔒 **Access Denied!**")
        except: pass
        return False
    return True

# --- DOWNLOAD ENGINES ---

def resolve_url(url):
    """Short links (teraboxurl.com) ko full links me badalta hai"""
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"})
        resp = session.get(url, allow_redirects=True, timeout=10)
        return resp.url
    except:
        return url

def download_video_engine(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # 1. URL Analysis
    is_terabox = any(d in url for d in ["terabox", "1024tera", "teraboxurl", "4funbox", "nephobox"])
    is_yt = "youtube.com" in url or "youtu.be" in url
    
    # 2. Resolve Terabox Redirects
    if is_terabox:
        url = resolve_url(url)
        # Force 1024tera domain (Better API success rate)
        if "terabox.com" in url: url = url.replace("terabox.com", "1024tera.com")
        logger.info(f"Processing URL: {url}")

    # 3. User-Agent Strategy
    # Terabox mobile traffic ko kam block karta hai, isliye hum Mobile UA use karenge
    if is_terabox:
        user_agent = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    else:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    # 4. yt-dlp Configuration
    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'user_agent': user_agent,
        'noplaylist': True,
        # Network Fixes
        'socket_timeout': 30,
        'retries': 10,
    }

    # Format Logic
    if is_yt:
        fmt = 'bestvideo+bestaudio/best' if quality == 'best' else f'bestvideo[height<={quality}]+bestaudio/best'
        if quality == 'audio': fmt = 'bestaudio/best'
    else:
        fmt = 'best'
    opts['format'] = fmt

    # Cookie Attachment
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE

    # YouTube Specifics
    if quality == 'audio':
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    elif is_yt:
        opts['merge_output_format'] = 'mp4'

    # --- EXECUTION ---
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
                "title": info.get('title', 'Media File'),
                "duration": info.get('duration'),
                "width": info.get('width'),
                "height": info.get('height')
            }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Engine Failed: {error_msg}")
        
        # --- FALLBACK SYSTEM ---
        # Agar yt-dlp cookie ke bawajood fail ho jaye, to hum Direct Request method try karenge
        if is_terabox:
             logger.info("Triggering Fallback for Terabox...")
             return fallback_terabox_direct(url)
             
        return {"status": False, "error": f"Failed: {error_msg[:100]}"}

def fallback_terabox_direct(url):
    """
    Direct Download using Headers + Cookies (Bypassing yt-dlp)
    """
    try:
        # 1. API se Link nikalo (NepCoder usually works)
        api = f"https://teraboxvideodownloader.nepcoderdevs.workers.dev/?url={url}"
        r = requests.get(api, timeout=20)
        data = r.json()
        
        # Try to find a valid link
        link = data.get("response", [{}])[0].get("resolutions", {}).get("Fast Download")
        title = data.get("response", [{}])[0].get("title", "Terabox_Video")
        
        if not link:
            return {"status": False, "error": "All methods failed. Cookies might be expired."}
        
        # 2. Download Stream
        filename = f"{DOWNLOAD_DIR}/tb_fallback_{int(time.time())}.mp4"
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        }
        
        with requests.get(link, stream=True, headers=headers, timeout=30) as dl:
            dl.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in dl.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
                    
        return {"status": True, "path": filename, "title": title, "duration": 0, "width": 0, "height": 0}
        
    except Exception as e:
        return {"status": False, "error": str(e)}

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    uid = update.effective_user.id
    
    c_stat = "✅ Active" if os.path.exists(COOKIE_FILE) else "⚠️ Missing"
    txt = f"👋 **Bot Ready!**\n🍪 Cookies: {c_stat}\n⚡ Server: {SERVER_TAG}\n\nLink bhejo!"
    
    if uid == OWNER_ID: txt += "\n\n👮‍♂️ **Admin:** `/add`, `/remove`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        cmd, target = update.message.text.split()
        target = int(target)
        if cmd == "/add": AUTHORIZED_USERS.add(target)
        elif cmd == "/remove": 
            if target != OWNER_ID: AUTHORIZED_USERS.discard(target)
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(f"✅ Action {cmd} complete.")
    except: await update.message.reply_text("Usage: `/add 12345`")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if not url.startswith("http"): return

    context.user_data['url'] = url
    
    # Smart Detection
    tb_domains = ["terabox", "1024tera", "teraboxurl", "4funbox"]
    
    if any(d in url for d in tb_domains):
        keyboard = [[InlineKeyboardButton("⬇️ Fast Download", callback_data="terabox")]]
        txt = "📦 **Terabox Detected!**\n(Using Cookie + Mobile Agent)"
    elif "youtube" in url or "youtu.be" in url:
        keyboard = [[InlineKeyboardButton("🎵 MP3", callback_data="audio")],
                    [InlineKeyboardButton("720p", callback_data="720"), InlineKeyboardButton("Best", callback_data="best")]]
        txt = "📺 **YouTube Detected**"
    else:
        keyboard = [[InlineKeyboardButton("⬇️ Download", callback_data="best")]]
        txt = "📸 **Link Detected**"

    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    data = query.data
    url = context.user_data.get('url')
    quality = 'best' if data == 'terabox' else data
    
    await query.edit_message_text(f"⚡ **Processing...**\n(Modes: Cookie > API > Fallback)")
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, download_video_engine, url, quality)
    
    if not result['status']:
        await query.edit_message_text(f"❌ Error: {result.get('error')}")
        return

    path = result['path']
    size = os.path.getsize(path)
    
    if size > MAX_FILE_SIZE:
        await query.edit_message_text(f"❌ Too Big: {get_readable_size(size)}")
        os.remove(path)
        return

    await query.edit_message_text(f"📤 **Uploading...**\n📦 {get_readable_size(size)}")
    
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(chat_id=update.effective_chat.id, audio=f, title=result['title'], read_timeout=120, write_timeout=120)
            else:
                await context.bot.send_video(chat_id=update.effective_chat.id, video=f, caption=result['title'], supports_streaming=True, read_timeout=120, write_timeout=120)
        await query.delete_message()
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        await query.edit_message_text("❌ Upload Error (Timeout).")
    finally:
        if os.path.exists(path): os.remove(path)

# --- STARTUP WITH FIXES ---
async def main():
    # TIMEOUT FIX: Connection timeouts increased to 120s to prevent crash logs
    req = HTTPXRequest(connection_pool_size=10, read_timeout=120, write_timeout=120, connect_timeout=120)
    
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler(["add", "remove"], admin_ops))
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
