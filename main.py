import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import re
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

# Cookie Setup
COOKIES_ENV = os.getenv("COOKIES_CONTENT")
TERABOX_COOKIE_VAL = os.getenv("TERABOX_COOKIE") 
API_MODE = os.getenv("API_MODE", "standard")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024
    SERVER_TAG = "🚀 Local Server (2GB)"
else:
    BASE_URL = None
    MAX_FILE_SIZE = 49 * 1024 * 1024
    SERVER_TAG = "☁️ Cloud Server (50MB)"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BotEngine")

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

# --- COOKIE FILE GENERATOR ---
def setup_cookies():
    valid_lines = ["# Netscape HTTP Cookie File"]
    if COOKIES_ENV and len(COOKIES_ENV) > 10:
        lines = COOKIES_ENV.split('\n')
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                valid_lines.append("\t".join(parts))
    
    # Cookie File sirf YT ke liye rakhenge, Terabox manual handle karenge
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

# --- TERABOX DIRECT SCRAPER ENGINE ---

def resolve_terabox(url):
    """
    Follows redirects to get the real 'surl' link.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}
        r = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
        return r.url
    except:
        return url

def get_terabox_download_link(url, cookie_value):
    """
    Manual HTML Parsing Method (Bypasses API Checks)
    """
    session = requests.Session()
    
    # 1. Critical Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    
    # Add Cookie manually to header
    if cookie_value:
        headers["Cookie"] = f"ndus={cookie_value}"
        logger.info("🍪 Using provided 'ndus' cookie for request.")
    else:
        logger.warning("⚠️ No Terabox Cookie Found! Download might fail.")

    try:
        # 2. Get the Page HTML
        logger.info(f"Scraping Page: {url}")
        resp = session.get(url, headers=headers, timeout=15)
        
        # 3. Search for 'dlink' inside the HTML scripts
        # Terabox aksar JSON format me dlink chupata hai page ke andar
        html = resp.text
        
        # Pattern 1: Direct dlink match
        matches = re.findall(r'"dlink":"(.*?)"', html)
        if matches:
            dlink = matches[0].replace("\\/", "/")
            logger.info("✅ Found dlink via Pattern 1")
            return dlink, "Terabox_Video.mp4"
            
        # Pattern 2: URL decoding match
        matches_2 = re.findall(r'dlink%22%3A%22(.*?)%22', html)
        if matches_2:
            dlink = requests.utils.unquote(matches_2[0])
            logger.info("✅ Found dlink via Pattern 2")
            return dlink, "Terabox_Video.mp4"
            
        # Pattern 3: Look for 'list' JSON
        if "list" in html:
            try:
                # Extract the JS object usually containing file list
                json_part = re.search(r'\[\{.*?"dlink":".*?\}\]', html)
                if json_part:
                    data = json.loads(json_part.group(0))
                    if data and "dlink" in data[0]:
                        logger.info("✅ Found dlink via JSON Pattern")
                        return data[0]["dlink"], data[0].get("server_filename", "Terabox_Video.mp4")
            except: pass

        logger.warning("❌ Scraping failed. HTML didn't contain simple dlink.")
        return None, None
        
    except Exception as e:
        logger.error(f"Scraper Error: {e}")
        return None, None

def download_video_engine(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    is_terabox = any(d in url for d in ["terabox", "1024tera", "teraboxurl", "4funbox"])
    is_yt = "youtube.com" in url or "youtu.be" in url

    # --- TERABOX LOGIC (NEW) ---
    if is_terabox:
        final_url = resolve_terabox(url)
        
        # Try 1: Manual Scraper with Cookie
        dlink, title = get_terabox_download_link(final_url, TERABOX_COOKIE_VAL)
        
        # Try 2: Fallback API (NepCoder) if Scraper fails
        if not dlink:
            logger.info("🔄 Scraper failed, trying fallback API...")
            try:
                api_url = f"https://teraboxvideodownloader.nepcoderdevs.workers.dev/?url={final_url}"
                r = requests.get(api_url, timeout=15)
                data = r.json()
                dlink = data.get("response", [{}])[0].get("resolutions", {}).get("Fast Download")
                if dlink: title = data.get("response", [{}])[0].get("title", "Terabox_Video")
            except Exception as e:
                logger.error(f"Fallback API Error: {e}")

        if dlink:
            filename = f"{DOWNLOAD_DIR}/tb_{timestamp}.mp4"
            headers_dl = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Cookie": f"ndus={TERABOX_COOKIE_VAL}" if TERABOX_COOKIE_VAL else ""
            }
            
            try:
                with requests.get(dlink, stream=True, headers=headers_dl, timeout=30) as r:
                    r.raise_for_status()
                    with open(filename, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk: f.write(chunk)
                
                return {"status": True, "path": filename, "title": title, "duration": 0, "width": 0, "height": 0}
            except Exception as e:
                return {"status": False, "error": f"Download Interrupted: {str(e)}"}
        else:
            return {"status": False, "error": "All methods (Scraper & API) failed. Cookie might be invalid for Cloud IP."}

    # --- YOUTUBE LOGIC (STANDARD) ---
    fmt = 'bestvideo+bestaudio/best' if quality == 'best' else f'bestvideo[height<={quality}]+bestaudio/best'
    if quality == 'audio': fmt = 'bestaudio/best'

    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': fmt,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'noplaylist': True,
    }
    
    if os.path.exists(COOKIE_FILE): opts['cookiefile'] = COOKIE_FILE
    
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
            
            if not os.path.exists(final_path) and os.path.exists(filename): final_path = filename
                
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
    
    cookie_state = "✅ Loaded" if TERABOX_COOKIE_VAL else "⚠️ Not Set"
    txt = f"👋 **Bot Active!**\n🍪 Terabox Cookie: {cookie_state}\n⚡ Server: {SERVER_TAG}\n\nLink bhejo!"
    
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
        await update.message.reply_text("✅ Done")
    except: await update.message.reply_text("Usage: `/add 12345`")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if not url.startswith("http"): return

    context.user_data['url'] = url
    
    if any(d in url for d in ["terabox", "1024tera", "teraboxurl", "4funbox"]):
        keyboard = [[InlineKeyboardButton("⬇️ Fast Download", callback_data="terabox")]]
        txt = "📦 **Terabox Link Detected!**"
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
    
    await query.edit_message_text(f"⚡ **Processing...**\n🔍 Scraping Link...")
    
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
    except Exception:
        await query.edit_message_text("❌ Upload Error.")
    finally:
        if os.path.exists(path): os.remove(path)

# --- STARTUP ---
async def main():
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
