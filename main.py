import os
import logging
import asyncio
import time
import shutil
import json
import threading
from datetime import datetime

# Flask & Telegram Imports
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes
)
from telegram.request import HTTPXRequest
import yt_dlp

# --- CONFIGURATION ---

# 1. Credentials
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
OWNER_ID = int(os.getenv("OWNER_ID", "0")) 
COOKIES_ENV = os.getenv("COOKIES_CONTENT")

# 2. Mode Selection
API_MODE = os.getenv("API_MODE", "standard") 

if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024 
    server_mode = "🚀 Local Server (2GB)"
else:
    BASE_URL = None 
    MAX_FILE_SIZE = 49 * 1024 * 1024 
    server_mode = "☁️ Cloud Server (50MB)"

# Constants
DOWNLOAD_DIR = "downloads"
USER_DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- DATA MANAGEMENT (USER SYSTEM) ---
def load_users():
    if not os.path.exists(USER_DATA_FILE):
        return [OWNER_ID]
    try:
        with open(USER_DATA_FILE, 'r') as f:
            data = json.load(f)
            return data.get("allowed_ids", [OWNER_ID])
    except:
        return [OWNER_ID]

def save_user(user_id, action="add"):
    users = load_users()
    if action == "add" and user_id not in users:
        users.append(user_id)
    elif action == "remove" and user_id in users:
        if user_id == OWNER_ID: return False # Owner cannot be removed
        users.remove(user_id)
    
    with open(USER_DATA_FILE, 'w') as f:
        json.dump({"allowed_ids": users}, f)
    return True

# --- COOKIE SETUP ---
def setup_cookies():
    if not COOKIES_ENV: return
    try:
        valid_lines = ["# Netscape HTTP Cookie File"]
        for line in COOKIES_ENV.split('\n'):
            parts = line.strip().split()
            if len(parts) >= 7:
                valid_lines.append(f"{parts[0]}\t{parts[1]}\t{parts[2]}\t{parts[3]}\t{parts[4]}\t{parts[5]}\t{''.join(parts[6:])}")
        with open(COOKIE_FILE, 'w') as f:
            f.write("\n".join(valid_lines))
        logger.info("✅ Cookies Loaded Successfully")
    except Exception as e:
        logger.error(f"Cookie Error: {e}")

setup_cookies()

# --- YOUTUBE ENGINE (BLOCKING CODE) ---
def download_logic(url, quality):
    """
    Ye function actual download karta hai yt-dlp ke saath.
    Isse hum thread me run karenge taaki bot freeze na ho.
    """
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # Format Selection
    if quality == 'audio':
        format_str = 'bestaudio/best'
    elif quality == 'best':
        format_str = 'bestvideo+bestaudio/best'
    else: # 360, 720, 1080
        format_str = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'

    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': format_str,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
    }

    if os.path.exists(COOKIE_FILE):
        ydl_opts['cookiefile'] = COOKIE_FILE

    if quality == 'audio':
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    else:
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # File Extension Fix logic
            base, ext = os.path.splitext(filename)
            final_name = base + ".mp3" if quality == 'audio' else base + ".mp4"
            
            # Check actual file existence
            if not os.path.exists(final_name):
                if os.path.exists(filename): final_name = filename
            
            return {
                "path": final_name, 
                "title": info.get('title', 'Unknown'), 
                "duration": info.get('duration', 0),
                "width": info.get('width', 0),
                "height": info.get('height', 0),
                "thumb": info.get('thumbnail', None)
            }
            
    except Exception as e:
        logger.error(f"DL Error: {e}")
        return None

# --- TELEGRAM HANDLERS ---

async def restricted(update: Update):
    """Checks if user is allowed"""
    user_id = update.effective_user.id
    allowed_users = load_users()
    if user_id not in allowed_users:
        await update.message.reply_text(
            "⛔ **Access Denied**\n\nContact Admin to get access.", 
            parse_mode=ParseMode.MARKDOWN
        )
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await restricted(update): return
    
    user = update.effective_user
    welcome_msg = (
        f"👋 **Hello {user.first_name}!**\n\n"
        f"🤖 I am your **Personal Media Downloader**.\n"
        f"⚙️ System: `{server_mode}`\n\n"
        f"✨ **How to use:**\n"
        f"Just send me any YouTube/Instagram link.\n\n"
        f"🛡️ **Admin Commands:**\n"
        f"`/add <id>` - Give Access\n"
        f"`/remove <id>` - Revoke Access\n"
        f"`/users` - Check User List"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)

# --- ADMIN COMMANDS ---
async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    try:
        new_id = int(context.args[0])
        save_user(new_id, "add")
        await update.message.reply_text(f"✅ **User {new_id} Added!**", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("⚠️ Usage: `/add 12345678`", parse_mode=ParseMode.MARKDOWN)

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    
    try:
        target_id = int(context.args[0])
        save_user(target_id, "remove")
        await update.message.reply_text(f"🗑️ **User {target_id} Removed!**", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("⚠️ Usage: `/remove 12345678`", parse_mode=ParseMode.MARKDOWN)

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    users = load_users()
    await update.message.reply_text(f"👥 **Authorized Users:**\n`{users}`", parse_mode=ParseMode.MARKDOWN)

# --- MEDIA HANDLER ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await restricted(update): return
    
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text("⚠️ Please send a valid **Link**.")
        return

    context.user_data['url'] = url
    
    # Stylish Keyboard
    keyboard = [
        [InlineKeyboardButton("🎵 MP3 Audio", callback_data="audio")],
        [
            InlineKeyboardButton("📺 360p", callback_data="360"), 
            InlineKeyboardButton("📺 720p", callback_data="720")
        ],
        [
            InlineKeyboardButton("📀 1080p", callback_data="1080"), 
            InlineKeyboardButton("🌟 Best Quality", callback_data="best")
        ]
    ]
    
    await update.message.reply_text(
        f"🔎 **Link Detected!**\n`{url}`\n\n👇 Select format to download:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    quality = query.data
    url = context.user_data.get('url')
    
    # 1. Update UI to "Processing"
    await query.edit_message_text(
        f"⚡ **Processing Request...**\n"
        f"📥 Quality: `{quality.upper()}`\n"
        f"⏳ Please wait, fetching data from server..."
    , parse_mode=ParseMode.MARKDOWN)

    # 2. Run Download in THREAD (Non-Blocking)
    # This prevents the bot from freezing for other users
    loop = asyncio.get_running_loop()
    
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    
    # Running blocking function in executor
    data = await loop.run_in_executor(None, download_logic, url, quality)
    
    if not data or not os.path.exists(data['path']):
        await query.edit_message_text("❌ **Download Failed.**\nTry a lower quality or check the link.")
        return

    # 3. Size Check
    path = data['path']
    file_size = os.path.getsize(path)
    
    if file_size > MAX_FILE_SIZE:
        await query.edit_message_text(f"⚠️ **File Too Large!**\nSize: `{round(file_size/1024/1024, 2)}MB`\nLimit: `{round(MAX_FILE_SIZE/1024/1024)}MB`")
        os.remove(path)
        return

    # 4. Uploading
    await query.edit_message_text(f"📤 **Uploading...**\n`{data['title']}`")
    
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=f,
                    title=data['title'],
                    performer="Bot Downloader",
                    caption=f"🎵 **{data['title']}**\n🤖 Downloaded via Bot",
                    write_timeout=600
                )
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=f"🎬 **{data['title']}**\n✨ Quality: {quality}\n🤖 Downloaded via Bot",
                    width=data['width'],
                    height=data['height'],
                    duration=data['duration'],
                    supports_streaming=True,
                    write_timeout=600
                )
        await query.delete_message()
    except Exception as e:
        logger.error(f"Upload Fail: {e}")
        await query.edit_message_text("❌ Error during upload.")
    finally:
        if os.path.exists(path):
            os.remove(path)

# --- FLASK SERVER (For Webhook/Keep-Alive) ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is Running! 🟢"

@app.route('/webhook', methods=['POST'])
async def webhook():
    update = Update.de_json(request.get_json(force=True), ptb_application.bot)
    await ptb_application.process_update(update)
    return "OK"

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Initialize Application
    ptb_request = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60)
    
    builder = Application.builder().token(BOT_TOKEN).request(ptb_request)
    if BASE_URL:
        builder.base_url(BASE_URL)
    
    ptb_application = builder.build()

    # Add Handlers
    ptb_application.add_handler(CommandHandler("start", start))
    ptb_application.add_handler(CommandHandler("add", add_user))
    ptb_application.add_handler(CommandHandler("remove", remove_user))
    ptb_application.add_handler(CommandHandler("users", list_users))
    ptb_application.add_handler(CallbackQueryHandler(button_handler))
    ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run Logic
    logger.info("🔥 Bot Started Successfully!")
    
    if WEBHOOK_URL:
        # Webhook Mode (Render/Heroku Production)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # We need to set webhook explicitly if using Flask wrapper
        loop.run_until_complete(ptb_application.bot.set_webhook(f"{WEBHOOK_URL}/webhook"))
        
        # Start Flask
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    else:
        # Polling Mode (Local Testing)
        ptb_application.run_polling()
        
