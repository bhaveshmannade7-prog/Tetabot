import os
import logging
import asyncio
import time
import sys
import ujson as json
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
from telegram.error import TimedOut, NetworkError
import yt_dlp

# --- CONFIGURATION & SECRETS ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
COOKIES_ENV = os.getenv("COOKIES_CONTENT")
API_MODE = os.getenv("API_MODE", "standard")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

# Limit Settings
if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024  # ~2GB
    SERVER_TAG = "🚀 Local Server (2GB)"
else:
    BASE_URL = None
    MAX_FILE_SIZE = 49 * 1024 * 1024    # ~50MB
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
    if not os.path.exists(DATA_FILE):
        return {OWNER_ID}
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            users = set(data.get("users", []))
            users.add(OWNER_ID)
            return users
    except:
        return {OWNER_ID}

def save_users(users_set):
    with open(DATA_FILE, 'w') as f:
        json.dump({"users": list(users_set)}, f)

AUTHORIZED_USERS = load_users()

# --- COOKIE SETUP ---
def setup_cookies():
    if not COOKIES_ENV or len(COOKIES_ENV) < 10:
        logger.warning("⚠️ No Cookies Found!")
        return
    try:
        lines = COOKIES_ENV.split('\n')
        valid_lines = ["# Netscape HTTP Cookie File"]
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                valid_lines.append("\t".join(parts))
        
        with open(COOKIE_FILE, 'w') as f:
            f.write("\n".join(valid_lines))
        logger.info("✅ Cookies Loaded Successfully")
    except Exception as e:
        logger.error(f"❌ Cookie Error: {e}")

setup_cookies()

# --- FLASK SERVER ---
app = Flask(__name__)

# --- UTILS & THREADING ---
executor = ThreadPoolExecutor(max_workers=4)

def get_readable_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.1f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.1f} TB"

async def check_auth(update: Update):
    if not update.effective_user: return False
    user_id = update.effective_user.id
    if user_id not in AUTHORIZED_USERS:
        try:
            await update.message.reply_text(
                f"🔒 **Access Denied!**\nID: `{user_id}`\nAdmin se contact karein.",
                parse_mode=ParseMode.MARKDOWN
            )
        except: pass
        return False
    return True

# --- DOWNLOAD ENGINE ---
def run_download_sync(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    format_map = {
        'audio': 'bestaudio/best',
        '360': 'bestvideo[height<=360]+bestaudio/best[height<=360]',
        '720': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        '1080': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'best': 'bestvideo+bestaudio/best'
    }
    
    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': format_map.get(quality, 'best'),
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
    }
    
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE

    if quality == 'audio':
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    else:
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
                "title": info.get('title', 'Media File'),
                "duration": info.get('duration'),
                "width": info.get('width'),
                "height": info.get('height')
            }
    except Exception as e:
        logger.error(f"Download Error: {e}")
        return {"status": False, "error": str(e)}

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    await update.message.reply_text(
        f"👋 **Online!**\nServer: {SERVER_TAG}\nLink bhejo!", 
        parse_mode=ParseMode.MARKDOWN
    )

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_id = int(context.args[0])
        AUTHORIZED_USERS.add(new_id)
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(f"✅ User {new_id} Added.")
    except: await update.message.reply_text("Usage: /add <id>")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target_id = int(context.args[0])
        if target_id in AUTHORIZED_USERS and target_id != OWNER_ID:
            AUTHORIZED_USERS.remove(target_id)
            save_users(AUTHORIZED_USERS)
            await update.message.reply_text(f"🗑️ User {target_id} Removed.")
    except: await update.message.reply_text("Usage: /remove <id>")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if not url.startswith("http"): return

    context.user_data['url'] = url
    keyboard = [
        [InlineKeyboardButton("🎵 MP3", callback_data="audio")],
        [InlineKeyboardButton("🎥 360p", callback_data="360"), InlineKeyboardButton("🎥 720p", callback_data="720")],
        [InlineKeyboardButton("💎 1080p", callback_data="1080"), InlineKeyboardButton("🔥 Best", callback_data="best")]
    ]
    await update.message.reply_text(f"🔗 Link Received. Select Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    quality = query.data
    url = context.user_data.get('url')
    
    await query.edit_message_text(f"⚡ Downloading `{quality}`...", parse_mode=ParseMode.MARKDOWN)
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, run_download_sync, url, quality)
    
    if not result['status']:
        await query.edit_message_text(f"❌ Error: {result.get('error')}")
        return

    path = result['path']
    size = os.path.getsize(path)
    
    if size > MAX_FILE_SIZE:
        await query.edit_message_text(f"❌ File too big ({get_readable_size(size)}). Limit: {get_readable_size(MAX_FILE_SIZE)}")
        os.remove(path)
        return

    await query.edit_message_text(f"📤 Uploading ({get_readable_size(size)})...")
    
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id, audio=f, title=result['title'],
                    read_timeout=120, write_timeout=120, connect_timeout=60
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id, video=f, caption=result['title'],
                    width=result.get('width'), height=result.get('height'), duration=result.get('duration'),
                    supports_streaming=True, read_timeout=120, write_timeout=120, connect_timeout=60
                )
        await query.delete_message()
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        await query.edit_message_text("❌ Upload Failed.")
    finally:
        if os.path.exists(path): os.remove(path)

# --- ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # Ignore network errors to prevent crash loops
    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning("⚠️ Network Timeout detected. Ignoring.")
        return

# --- INITIALIZATION ---
async def main():
    # Fix: Increased timeouts to prevent "ConnectTimeout"
    request_params = HTTPXRequest(
        connection_pool_size=20,
        read_timeout=30.0,   # Increased from default
        write_timeout=30.0,  # Increased from default
        connect_timeout=30.0 # Critical fix for Render
    )
    
    builder = Application.builder().token(BOT_TOKEN).request(request_params)
    if BASE_URL: builder.base_url(BASE_URL)
    app_bot = builder.build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("add", add_user))
    app_bot.add_handler(CommandHandler("remove", remove_user))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    app_bot.add_error_handler(error_handler)

    await app_bot.initialize()
    if WEBHOOK_URL:
        await app_bot.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", allowed_updates=Update.ALL_TYPES)
    
    return app_bot

# --- ENTRY POINT ---
# Global Loop setup for Gunicorn
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
        # Use existing loop to process update
        loop.run_until_complete(bot_app.process_update(update))
        return "OK"
    return "Invalid"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
