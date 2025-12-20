import os
import logging
import asyncio
import time
import signal
import sys
import ujson as json  # Faster JSON processing
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
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

# --- DATA MANAGEMENT (Database) ---
def load_users():
    """Authorized users ko load karta hai"""
    if not os.path.exists(DATA_FILE):
        return {OWNER_ID}
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
            users = set(data.get("users", []))
            users.add(OWNER_ID) # Owner hamesha rahega
            return users
    except:
        return {OWNER_ID}

def save_users(users_set):
    """Users ko save karta hai"""
    with open(DATA_FILE, 'w') as f:
        json.dump({"users": list(users_set)}, f)

AUTHORIZED_USERS = load_users()

# --- COOKIE SETUP ---
def setup_cookies():
    if not COOKIES_ENV or len(COOKIES_ENV) < 10:
        logger.warning("⚠️ No Cookies Found! YouTube speed might be slow.")
        return
    try:
        lines = COOKIES_ENV.split('\n')
        valid_lines = ["# Netscape HTTP Cookie File"]
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                # Tab separated format fix
                valid_lines.append("\t".join(parts))
        
        with open(COOKIE_FILE, 'w') as f:
            f.write("\n".join(valid_lines))
        logger.info("✅ Cookies Loaded Successfully")
    except Exception as e:
        logger.error(f"❌ Cookie Error: {e}")

setup_cookies()

# --- FLASK SERVER ---
app = Flask(__name__)

# --- UTILS ---
def get_readable_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.1f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.1f} TB"

async def check_auth(update: Update):
    user = update.effective_user
    if user.id not in AUTHORIZED_USERS:
        await update.message.reply_text(
            f"🔒 **Access Denied!**\n\nApka ID: `{user.id}`\nAdmin se contact karein.",
            parse_mode=ParseMode.MARKDOWN
        )
        return False
    return True

# --- CORE ENGINE (THREADED) ---
# Executor banaya taki bot hang na ho jab download chal raha ho
executor = ThreadPoolExecutor(max_workers=4)

def run_download_sync(url, quality):
    """Background Thread me chalne wala download function"""
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # Quality Presets
    format_map = {
        'audio': 'bestaudio/best',
        '360': 'bestvideo[height<=360]+bestaudio/best[height<=360]',
        '720': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        '1080': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
        'best': 'bestvideo+bestaudio/best'
    }
    
    fmt = format_map.get(quality, 'best')
    
    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': fmt,
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
            
            # Extension Fix
            base, ext = os.path.splitext(filename)
            final_path = base + (".mp3" if quality == 'audio' else ".mp4")
            
            # Agar merge hone ke baad naam badal gaya ho
            if not os.path.exists(final_path) and os.path.exists(filename):
                final_path = filename

            return {
                "status": True,
                "path": final_path,
                "title": info.get('title', 'Media File'),
                "duration": info.get('duration'),
                "width": info.get('width'),
                "height": info.get('height'),
                "thumb": info.get('thumbnail')
            }
    except Exception as e:
        logger.error(f"Download Fail: {e}")
        return {"status": False, "error": str(e)}

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    user_first = update.effective_user.first_name
    txt = (
        f"👋 **Namaste, {user_first}!**\n\n"
        f"🤖 **Bot Status:** Online\n"
        f"⚙️ **Server:** {SERVER_TAG}\n"
        f"👥 **Users:** {len(AUTHORIZED_USERS)}\n\n"
        "🔗 **Koi bhi link bhejo (Insta, YouTube, Twitter/X), main download kar dunga!**"
    )
    
    # Agar admin hai to extra help dikhao
    if update.effective_user.id == OWNER_ID:
        txt += "\n\n👮‍♂️ **Admin Commands:**\n`/add 123456` - User ko add karein\n`/remove 123456` - User ko hatayein\n`/users` - List dekhein"
        
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

# -- Admin Commands --
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_id = int(context.args[0])
        AUTHORIZED_USERS.add(new_id)
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(f"✅ **User {new_id} Added!**")
    except:
        await update.message.reply_text("❌ Usage: `/add <user_id>`")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target_id = int(context.args[0])
        if target_id == OWNER_ID:
            await update.message.reply_text("❌ **Aap khud ko remove nahi kar sakte!**")
            return
        if target_id in AUTHORIZED_USERS:
            AUTHORIZED_USERS.remove(target_id)
            save_users(AUTHORIZED_USERS)
            await update.message.reply_text(f"🗑️ **User {target_id} Removed!**")
        else:
            await update.message.reply_text("⚠️ User list me nahi hai.")
    except:
        await update.message.reply_text("❌ Usage: `/remove <user_id>`")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    txt = "👥 **Authorized Users:**\n"
    for uid in AUTHORIZED_USERS:
        txt += f"`{uid}`\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

# -- Media Handling --
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("❌ **Ye valid link nahi lag raha.**")
        return

    context.user_data['url'] = url
    
    # Stylish Keyboard
    keyboard = [
        [
            InlineKeyboardButton("🎵 MP3 Audio", callback_data="audio"),
        ],
        [
            InlineKeyboardButton("🎥 360p", callback_data="360"),
            InlineKeyboardButton("🎥 720p", callback_data="720"),
        ],
        [
            InlineKeyboardButton("💎 1080p", callback_data="1080"),
            InlineKeyboardButton("🚀 Best Quality", callback_data="best")
        ]
    ]
    
    await update.message.reply_text(
        f"🔎 **Link Detected!**\n`{url}`\n\n👇 **Format Select Karein:**", 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    quality = query.data
    url = context.user_data.get('url')
    
    # Progress UI
    await query.edit_message_text(f"⚡ **Processing...**\n📥 Downloading: `{quality.upper()}`\n⏳ Please wait...", parse_mode=ParseMode.MARKDOWN)
    
    # Heavy task ko thread me bhejo (Crash Proofing)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, run_download_sync, url, quality)
    
    if not result['status']:
        await query.edit_message_text(f"❌ **Error:**\n`{result.get('error')}`", parse_mode=ParseMode.MARKDOWN)
        return

    file_path = result['path']
    file_size = os.path.getsize(file_path)
    file_name = result['title']
    
    # Size Check
    if file_size > MAX_FILE_SIZE:
        await query.edit_message_text(
            f"❌ **File Too Big!**\n"
            f"📁 Size: `{get_readable_size(file_size)}`\n"
            f"🛑 Limit: `{get_readable_size(MAX_FILE_SIZE)}`\n"
            f"Server mode change karein.", 
            parse_mode=ParseMode.MARKDOWN
        )
        os.remove(file_path)
        return

    # Upload UI
    await query.edit_message_text(f"📤 **Uploading...**\n📁 `{file_name}`\n📦 Size: `{get_readable_size(file_size)}`", parse_mode=ParseMode.MARKDOWN)
    
    chat_id = update.effective_chat.id
    
    try:
        with open(file_path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(
                    chat_id=chat_id, 
                    audio=f, 
                    title=file_name,
                    performer="Bot Downloader",
                    caption=f"🎵 **{file_name}**\n🤖 Via Bot", 
                    write_timeout=600
                )
            else:
                await context.bot.send_video(
                    chat_id=chat_id, 
                    video=f, 
                    caption=f"🎬 **{file_name}**\n✨ Quality: {quality}\n🤖 Via Bot", 
                    width=result.get('width'), 
                    height=result.get('height'), 
                    duration=result.get('duration'),
                    supports_streaming=True,
                    write_timeout=600
                )
        
        await query.delete_message() # Status msg delete
        
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        await query.edit_message_text("❌ **Upload Failed!** (Telegram Server Timeout)")
    
    finally:
        # Cleanup (Important for stability)
        if os.path.exists(file_path):
            os.remove(file_path)

# --- BOT SETUP ---
async def main():
    # Build App
    builder = Application.builder().token(BOT_TOKEN)
    
    # Connection Optimization
    request_params = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60)
    builder.request(request_params)
    
    if BASE_URL:
        builder.base_url(BASE_URL)
        
    ptb_app = builder.build()

    # Handlers
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("add", add_user))     # New Admin Command
    ptb_app.add_handler(CommandHandler("remove", remove_user)) # New Admin Command
    ptb_app.add_handler(CommandHandler("users", list_users))   # New Admin Command
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))

    # Initialize
    await ptb_app.initialize()
    if WEBHOOK_URL:
        await ptb_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        logger.info(f"🌍 Webhook set to: {WEBHOOK_URL}")
    
    return ptb_app

# Global Instance for Flask
loop = asyncio.get_event_loop_policy().get_event_loop()
bot_app = loop.run_until_complete(main())

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot_app.bot)
        loop.run_until_complete(bot_app.process_update(update))
        return "OK"
    return "Invalid Request"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
    
