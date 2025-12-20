import os
import logging
import asyncio
import time
import json
import threading
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
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
OWNER_ID = int(os.getenv("OWNER_ID", "0")) 
COOKIES_ENV = os.getenv("COOKIES_CONTENT")
API_MODE = os.getenv("API_MODE", "standard") 

# Server Mode Config
if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024 
    server_mode = "🚀 Local Server (2GB)"
else:
    BASE_URL = None 
    MAX_FILE_SIZE = 49 * 1024 * 1024 
    server_mode = "☁️ Cloud Server (50MB)"

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

# --- FUNCTIONS ---
def load_users():
    if not os.path.exists(USER_DATA_FILE): return [OWNER_ID]
    try:
        with open(USER_DATA_FILE, 'r') as f:
            return json.load(f).get("allowed_ids", [OWNER_ID])
    except: return [OWNER_ID]

def save_user(user_id, action="add"):
    users = load_users()
    if action == "add" and user_id not in users: users.append(user_id)
    elif action == "remove" and user_id in users:
        if user_id == OWNER_ID: return False
        users.remove(user_id)
    with open(USER_DATA_FILE, 'w') as f: json.dump({"allowed_ids": users}, f)
    return True

def setup_cookies():
    if not COOKIES_ENV: return
    try:
        valid_lines = ["# Netscape HTTP Cookie File"]
        for line in COOKIES_ENV.split('\n'):
            parts = line.strip().split()
            if len(parts) >= 7:
                valid_lines.append(f"{parts[0]}\t{parts[1]}\t{parts[2]}\t{parts[3]}\t{parts[4]}\t{parts[5]}\t{''.join(parts[6:])}")
        with open(COOKIE_FILE, 'w') as f: f.write("\n".join(valid_lines))
        logger.info("✅ Cookies Loaded")
    except Exception as e: logger.error(f"Cookie Error: {e}")

setup_cookies()

def download_logic(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    if quality == 'audio': format_str = 'bestaudio/best'
    elif quality == 'best': format_str = 'bestvideo+bestaudio/best'
    else: format_str = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]'

    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': format_str,
        'quiet': True, 'no_warnings': True, 'geo_bypass': True, 'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
    }
    if os.path.exists(COOKIE_FILE): ydl_opts['cookiefile'] = COOKIE_FILE
    if quality == 'audio': ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    else: ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            base, ext = os.path.splitext(filename)
            final_name = base + ".mp3" if quality == 'audio' else base + ".mp4"
            if not os.path.exists(final_name):
                if os.path.exists(filename): final_name = filename
            return {"path": final_name, "title": info.get('title', 'Unknown'), "duration": info.get('duration', 0), "width": info.get('width', 0), "height": info.get('height', 0)}
    except Exception as e:
        logger.error(f"DL Error: {e}")
        return None

# --- TELEGRAM HANDLERS ---
async def restricted(update: Update):
    if update.effective_user.id not in load_users():
        await update.message.reply_text("⛔ **Access Denied**\nContact Admin.")
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await restricted(update): return
    await update.message.reply_text(f"👋 **Bot Active!**\nSystem: `{server_mode}`\n\nLink bhejo.", parse_mode=ParseMode.MARKDOWN)

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        save_user(int(context.args[0]), "add")
        await update.message.reply_text("✅ User Added")
    except: await update.message.reply_text("Usage: /add <id>")

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        save_user(int(context.args[0]), "remove")
        await update.message.reply_text("🗑️ User Removed")
    except: await update.message.reply_text("Usage: /remove <id>")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await restricted(update): return
    url = update.message.text.strip()
    if not url.startswith("http"): return
    context.user_data['url'] = url
    keyboard = [[InlineKeyboardButton("🎵 MP3", callback_data="audio")], [InlineKeyboardButton("📺 720p", callback_data="720"), InlineKeyboardButton("🌟 Best", callback_data="best")]]
    await update.message.reply_text(f"🔗 Link Received", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    quality = query.data
    url = context.user_data.get('url')
    
    msg = await query.edit_message_text("⚡ Processing...")
    
    # Run in Thread (Async Fix)
    loop = asyncio.get_running_loop()
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    data = await loop.run_in_executor(None, download_logic, url, quality)
    
    if not data or not os.path.exists(data['path']):
        await msg.edit_text("❌ Download Failed")
        return

    path = data['path']
    if os.path.getsize(path) > MAX_FILE_SIZE:
        await msg.edit_text("❌ File too big")
        os.remove(path)
        return

    await msg.edit_text("📤 Uploading...")
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, title=data['title'], write_timeout=600)
            else:
                await context.bot.send_video(chat_id=query.message.chat_id, video=f, caption=data['title'], write_timeout=600, supports_streaming=True)
        await msg.delete()
    except Exception as e:
        logger.error(e)
        await msg.edit_text("❌ Upload Error")
    finally:
        if os.path.exists(path): os.remove(path)

# --- GLOBAL APP INITIALIZATION (CRITICAL FIX) ---
# Ye 'ptb_application' ab global scope me hai, Flask isse direct access kar payega
ptb_request = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60)
builder = Application.builder().token(BOT_TOKEN).request(ptb_request)
if BASE_URL:
    builder.base_url(BASE_URL)
ptb_application = builder.build()

# Add Handlers
ptb_application.add_handler(CommandHandler("start", start))
ptb_application.add_handler(CommandHandler("add", add_user))
ptb_application.add_handler(CommandHandler("remove", remove_user))
ptb_application.add_handler(CallbackQueryHandler(button_handler))
ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# --- FLASK SERVER ---
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot Running 🟢"

@app.route('/webhook', methods=['POST'])
async def webhook():
    # Ab ptb_application defined hai
    update = Update.de_json(request.get_json(force=True), ptb_application.bot)
    await ptb_application.process_update(update)
    return "OK"

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    if WEBHOOK_URL:
        # Webhook Setup for Render
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ptb_application.bot.set_webhook(f"{WEBHOOK_URL}/webhook"))
        
        # Start Flask
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    else:
        # Local Polling
        ptb_application.run_polling()
