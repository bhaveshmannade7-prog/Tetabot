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
# Ensure Owner ID is integer
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

# --- DOWNLOAD ENGINE (UPDATED) ---
def is_youtube(url):
    return "youtube.com" in url or "youtu.be" in url

def run_download_sync(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # Check if URL is YouTube
    is_yt = is_youtube(url)
    
    # --- Format Logic ---
    # Agar YouTube hai to Advanced format use karo, nahi to simple 'best' use karo (Insta/Terabox ke liye)
    if is_yt:
        if quality == 'audio':
            fmt = 'bestaudio/best'
        elif quality == '360':
            fmt = 'bestvideo[height<=360]+bestaudio/best[height<=360]'
        elif quality == '720':
            fmt = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
        elif quality == '1080':
            fmt = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
        else:
            fmt = 'bestvideo+bestaudio/best'
    else:
        # Instagram/Terabox/Facebook Fix:
        # Ye platforms alag-alag streams nahi dete, isliye 'merge' logic fail hota hai.
        # Simple 'best' use karne se error nahi ayega.
        if quality == 'audio':
             fmt = 'bestaudio/best'
        else:
             fmt = 'best' 

    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': fmt,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        # Browser masquerade help karta hai insta/terabox ke liye
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }
    
    # --- Cookie Logic ---
    # Sirf tab cookie use karo jab link YouTube ka ho
    if is_yt and os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE

    if quality == 'audio':
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    elif is_yt:
        # Merge sirf YouTube ke liye zaroori hai
        opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, _ = os.path.splitext(filename)
            final_path = base + (".mp3" if quality == 'audio' else ".mp4")
            
            # Agar file format rename nahi hua (Insta cases)
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
    
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    # Debugging log
    logger.info(f"Start Command by: {user_id} (Owner: {OWNER_ID})")
    
    welcome_text = (
        f"👋 **Namaste, {first_name}!**\n\n"
        f"🤖 **Bot Status:** Online\n"
        f"⚡ **Server:** {SERVER_TAG}\n"
        "🔗 **Instagram, YouTube, Terabox Link Bhejo!**"
    )
    
    # Admin Panel Check
    if user_id == OWNER_ID:
        welcome_text += (
            "\n\n👮‍♂️ **Admin Controls:**\n"
            "• `/add <id>` - User Access dein\n"
            "• `/remove <id>` - Access hatayein\n"
            "• `/users` - User list dekhein\n"
            "• `/logs` - Check Logs (Console)"
        )
        
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_id = int(context.args[0])
        AUTHORIZED_USERS.add(new_id)
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(f"✅ **User {new_id} Added!**")
    except: await update.message.reply_text("Usage: `/add 123456`")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target_id = int(context.args[0])
        if target_id == OWNER_ID:
            await update.message.reply_text("❌ Aap khud ko remove nahi kar sakte.")
            return
        if target_id in AUTHORIZED_USERS:
            AUTHORIZED_USERS.remove(target_id)
            save_users(AUTHORIZED_USERS)
            await update.message.reply_text(f"🗑️ **User {target_id} Removed!**")
    except: await update.message.reply_text("Usage: `/remove 123456`")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    txt = "👥 **Authorized Users:**\n"
    for uid in AUTHORIZED_USERS:
        txt += f"`{uid}`\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    
    # Basic URL validation
    if not url.startswith(("http", "www")): 
        return

    context.user_data['url'] = url
    
    # UI Customization based on link type
    if "youtube.com" in url or "youtu.be" in url:
        # YouTube: Full options
        keyboard = [
            [InlineKeyboardButton("🎵 MP3", callback_data="audio")],
            [InlineKeyboardButton("360p", callback_data="360"), InlineKeyboardButton("720p", callback_data="720")],
            [InlineKeyboardButton("1080p", callback_data="1080"), InlineKeyboardButton("Best Video", callback_data="best")]
        ]
        msg_text = "📺 **YouTube Link Detected**\nSelect Quality:"
    else:
        # Insta/Terabox: Simple options (avoid complex formats)
        keyboard = [
            [InlineKeyboardButton("🎵 Audio Only", callback_data="audio")],
            [InlineKeyboardButton("⬇️ Download Video", callback_data="best")]
        ]
        msg_text = "📸 **Social Media Link Detected**\n(Instagram/Terabox/Other)\nSelect Option:"

    await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try: await query.answer()
    except: pass
    
    quality = query.data
    url = context.user_data.get('url')
    
    # Edit message to show processing
    await query.edit_message_text(f"⚡ **Downloading...**\n⏳ Please wait...", parse_mode=ParseMode.MARKDOWN)
    
    # Run in thread
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, run_download_sync, url, quality)
    
    if not result['status']:
        # Agar error aaya
        error_msg = result.get('error')
        if "Too Many Requests" in error_msg:
            friendly_err = "❌ Rate Limit Hit (Try again later)"
        elif "Sign in" in error_msg:
            friendly_err = "❌ Content is Private or Requires Login"
        else:
            friendly_err = f"❌ Error: {error_msg[:100]}..." # Show short error
            
        await query.edit_message_text(friendly_err)
        return

    path = result['path']
    size = os.path.getsize(path)
    
    if size > MAX_FILE_SIZE:
        await query.edit_message_text(f"❌ **File Too Big!**\nSize: {get_readable_size(size)}\nLimit: {get_readable_size(MAX_FILE_SIZE)}")
        os.remove(path)
        return

    await query.edit_message_text(f"📤 **Uploading...**\n📦 Size: {get_readable_size(size)}")
    
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id, 
                    audio=f, 
                    title=result['title'],
                    caption="🤖 Via Bot",
                    read_timeout=120, write_timeout=120, connect_timeout=60
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id, 
                    video=f, 
                    caption=f"🎬 {result['title']}",
                    width=result.get('width'), 
                    height=result.get('height'), 
                    duration=result.get('duration'),
                    supports_streaming=True, 
                    read_timeout=120, write_timeout=120, connect_timeout=60
                )
        await query.delete_message()
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        await query.edit_message_text("❌ Upload Failed (Telegram Error).")
    finally:
        if os.path.exists(path): os.remove(path)

# --- ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- MAIN ---
async def main():
    request_params = HTTPXRequest(
        connection_pool_size=20,
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=30.0
    )
    
    builder = Application.builder().token(BOT_TOKEN).request(request_params)
    if BASE_URL: builder.base_url(BASE_URL)
    app_bot = builder.build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("add", add_user))
    app_bot.add_handler(CommandHandler("remove", remove_user))
    app_bot.add_handler(CommandHandler("users", list_users))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    app_bot.add_error_handler(error_handler)

    await app_bot.initialize()
    if WEBHOOK_URL:
        await app_bot.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook", allowed_updates=Update.ALL_TYPES)
    
    return app_bot

# Entry point
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
