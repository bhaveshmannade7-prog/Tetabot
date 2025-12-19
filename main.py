import os
import logging
import asyncio
import time
import shutil
import signal
import sys
import json
from datetime import date
from flask import Flask, request  # Flask request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
import yt_dlp
import nest_asyncio

# --- CONFIGURATION ---
nest_asyncio.apply()

# 1. Credentials
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
OWNER_ID = int(os.getenv("OWNER_ID", "0")) 

# 2. Mode Selection (Standard vs Local)
API_MODE = os.getenv("API_MODE", "standard") 

if API_MODE == 'local':
    # AWS EC2 Local Server URL
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024 
    logger_msg = "🚀 Running in LOCAL SERVER Mode (2GB Support)"
else:
    # Standard Telegram API
    BASE_URL = None 
    MAX_FILE_SIZE = 49 * 1024 * 1024 
    logger_msg = "⚠️ Running in STANDARD Mode (50MB Limit)"

# Constants
DOWNLOAD_DIR = "downloads"
DATA_FILE = "data.json"

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info(logger_msg)

IS_STOPPED = False

# --- FLASK (For Render Webhook) ---
app = Flask(__name__)

# --- AUTH CHECK ---
async def check_auth(update: Update):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("⛔ **Access Denied!**\nYe bot personal use ke liye hai.")
        return False
    return True

# --- YOUTUBE ENGINE ---
def download_video(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    format_str = ""
    if quality == 'audio':
        format_str = 'bestaudio/best'
    elif quality == '360':
        format_str = 'bestvideo[height<=360]+bestaudio/best[height<=360]'
    elif quality == '720':
        format_str = 'bestvideo[height<=720]+bestaudio/best[height<=720]'
    elif quality == '1080':
        format_str = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]'
    elif quality == 'best':
        format_str = 'bestvideo+bestaudio/best'

    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'format': format_str,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
    }

    if quality == 'audio':
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    else:
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            base, ext = os.path.splitext(filename)
            if quality == 'audio':
                final_name = base + ".mp3"
            else:
                final_name = base + ".mp4"
            
            if not os.path.exists(final_name):
                if os.path.exists(filename): final_name = filename
            
            return final_name, info.get('title', 'Video'), info.get('duration'), info.get('width'), info.get('height')
            
    except Exception as e:
        logger.error(f"Download Error: {e}")
        return None, None, None, None, None

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    status = "AWS (2GB)" if API_MODE == 'local' else "Render (50MB)"
    await update.message.reply_text(f"👋 **Personal Bot Active!**\nServer Mode: **{status}**\n\nLink bhejo video nikalne ke liye.", parse_mode=ParseMode.MARKDOWN)

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    global IS_STOPPED
    IS_STOPPED = True
    await update.message.reply_text("🛑 **Bot Stopping...**\nSabhi process rok diye gaye hain.\n(Restart ke liye deploy dobara karein)")
    os._exit(0)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if IS_STOPPED: return
    if not await check_auth(update): return
    
    url = update.message.text.strip()
    if "http" not in url: return

    context.user_data['url'] = url
    
    keyboard = [
        [InlineKeyboardButton("🎵 Audio (MP3)", callback_data="audio")],
        [InlineKeyboardButton("360p", callback_data="360"), InlineKeyboardButton("720p", callback_data="720")],
        [InlineKeyboardButton("1080p (FHD)", callback_data="1080"), InlineKeyboardButton("🔥 Best (1GB+)", callback_data="best")]
    ]
    await update.message.reply_text("⚙️ **Quality Select Karein:**", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if IS_STOPPED: return
    query = update.callback_query
    await query.answer()
    
    quality = query.data
    url = context.user_data.get('url')
    
    status_msg = await query.edit_message_text(f"⏳ **Downloading {quality.upper()}...**\n(Ye process lamba chal sakta hai)")

    path, title, duration, w, h = download_video(url, quality)

    if path and os.path.exists(path):
        file_size = os.path.getsize(path)
        
        if file_size > MAX_FILE_SIZE:
            await status_msg.edit_text(f"❌ **File Too Big!**\nSize: {round(file_size/(1024*1024))}MB\nAllowed: {round(MAX_FILE_SIZE/(1024*1024))}MB\n\n(AWS Local Server Setup karein 1GB+ ke liye)")
            os.remove(path)
            return

        await status_msg.edit_text("📤 **Uploading to Telegram...**")
        
        try:
            with open(path, 'rb') as f:
                if quality == 'audio':
                    await context.bot.send_audio(chat_id=OWNER_ID, audio=f, title=title, caption="Downloaded via Bot", read_timeout=1200, write_timeout=1200)
                else:
                    await context.bot.send_video(chat_id=OWNER_ID, video=f, caption=title, supports_streaming=True, width=w, height=h, duration=duration, read_timeout=1200, write_timeout=1200)
            
            await status_msg.delete()
        except Exception as e:
            await status_msg.edit_text(f"❌ Upload Error: {e}")
        finally:
            if os.path.exists(path): os.remove(path)
    else:
        await status_msg.edit_text("❌ Download Failed.")

# --- INITIALIZATION ---
# Renamed variable from 'request' to 'ptb_request' to avoid conflict with Flask 'request'
ptb_request = HTTPXRequest(connection_pool_size=8, read_timeout=1200, write_timeout=1200)

if BASE_URL:
    ptb_application = Application.builder().token(BOT_TOKEN).base_url(BASE_URL).request(ptb_request).build()
else:
    ptb_application = Application.builder().token(BOT_TOKEN).request(ptb_request).build()

ptb_application.add_handler(CommandHandler("start", start))
ptb_application.add_handler(CommandHandler("stop", stop_bot))
ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_application.add_handler(CallbackQueryHandler(button_handler))

async def setup_bot():
    await ptb_application.initialize()
    if WEBHOOK_URL:
        await ptb_application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    
    logger.info("✅ Bot Started Successfully")

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    if request.method == "POST":
        data = request.get_json(force=True)
        update = Update.de_json(data, ptb_application.bot)
        asyncio.run(ptb_application.process_update(update))
        return "OK"
    return "Invalid"

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(setup_bot())
    app.run(port=5000)
else:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(setup_bot())
    except: pass
