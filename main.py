import os
import logging
import asyncio
import json
import time
import shutil
import traceback
from datetime import datetime, date
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
import nest_asyncio

# --- CONFIGURATION ---
nest_asyncio.apply()

# Load Env Vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "") 
COOKIES_ENV = os.getenv("COOKIES_CONTENT")

# Constants
DOWNLOAD_DIR = "downloads"
DATA_FILE = "data.json"
COOKIE_FILE = "cookies.txt"
DAILY_LIMIT = 10 # Limit thodi badha di hai multi-platform ke liye
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Telegram Limit

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- COOKIE SETUP ---
# TeraBox aur Diskwala ke liye Cookies BOHOT zaroori hain
if COOKIES_ENV and not os.path.exists(COOKIE_FILE):
    try:
        with open(COOKIE_FILE, 'w') as f:
            f.write(COOKIES_ENV)
        logger.info("✅ Cookies loaded for TeraBox/Diskwala/YouTube.")
    except Exception as e:
        logger.error(f"Cookie Error: {e}")

# --- DATA PERSISTENCE ---
def load_data():
    if not os.path.exists(DATA_FILE): return {}
    try:
        with open(DATA_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_data(data):
    try:
        with open(DATA_FILE, 'w') as f: json.dump(data, f, indent=4)
    except: pass

def get_user_data(user_id):
    data = load_data()
    str_id = str(user_id)
    today = str(date.today())
    if str_id not in data:
        data[str_id] = {"premium": False, "date": today, "count": 0}
    if data[str_id]["date"] != today:
        data[str_id]["date"] = today
        data[str_id]["count"] = 0
        save_data(data)
    return data, data[str_id]

def increment_download(user_id):
    data, user = get_user_data(user_id)
    data[str(user_id)]["count"] += 1
    save_data(data)

# --- FLASK ---
app = Flask(__name__)
ptb_application = Application.builder().token(BOT_TOKEN).build()

# --- DOWNLOADER ENGINE ---
def download_media(url, mode='best'):
    """
    mode: 'audio', '360', '720', 'best'
    """
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    has_cookies = os.path.exists(COOKIE_FILE)
    
    # Generic Options (Works for TeraBox, Diskwala, YouTube)
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    }

    if has_cookies:
        ydl_opts['cookiefile'] = COOKIE_FILE

    # --- Mode Logic ---
    if mode == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
        })
    elif mode == '360':
        ydl_opts.update({
            'format': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best[height<=360]',
            'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
        })
    elif mode == '720':
        ydl_opts.update({
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]',
            'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
        })
    else: # BEST (Default for TeraBox/Diskwala)
        # Try to get MP4 under 50MB, else just get best and hope
        ydl_opts.update({
            'format': 'best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Extension fix
            base, ext = os.path.splitext(filename)
            if mode == 'audio':
                final_filename = base + ".mp3"
            else:
                final_filename = base + ".mp4"
            
            # Check file existence
            if not os.path.exists(final_filename) and os.path.exists(filename):
                final_filename = filename

            return final_filename, info.get('title', 'Media File'), info.get('duration'), info.get('width'), info.get('height')

    except Exception as e:
        logger.error(f"Download Error: {e}")
        return None, None, None, None, None

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 **All-in-One Downloader**\n\n"
        "Supported Sites:\n"
        "✅ **YouTube** (Shorts/Video)\n"
        "✅ **TeraBox** (Link bhejein)\n"
        "✅ **Diskwala** (Link bhejein)\n\n"
        "Bus link copy-paste karein!"
    )
    if REQUIRED_CHANNEL: msg += f"\n\n⚠️ Pehle {REQUIRED_CHANNEL} join karein."
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    if "http" not in url: return # Ignore non-links
    
    # 1. Subscription Check
    if not await check_subscription(user_id, context.bot):
        await update.message.reply_text(f"🚫 Pehle {REQUIRED_CHANNEL} join karein.")
        return

    # 2. Limit Check
    _, user_data = get_user_data(user_id)
    if not user_data["premium"] and user_data["count"] >= DAILY_LIMIT:
        await update.message.reply_text(f"🚫 Daily Limit ({DAILY_LIMIT}) khatam.")
        return

    # 3. Platform Detection & Processing
    # Check if it is YouTube
    is_youtube = "youtube.com" in url or "youtu.be" in url
    
    if is_youtube:
        # Show Quality Buttons for YouTube
        context.user_data['current_url'] = url
        keyboard = [
            [InlineKeyboardButton("🎵 MP3", callback_data="audio"), InlineKeyboardButton("🎬 360p", callback_data="360")],
            [InlineKeyboardButton("🎬 720p", callback_data="720"), InlineKeyboardButton("💎 Best", callback_data="best")]
        ]
        await update.message.reply_text("⚙️ **Quality Select Karein:**", reply_markup=InlineKeyboardMarkup(keyboard))
    
    else:
        # Direct Download for TeraBox / Diskwala / Others
        wait_msg = await update.message.reply_text("⏳ **Link Detect: TeraBox/Other**\nDownloading Best Quality...", parse_mode=ParseMode.MARKDOWN)
        await process_download(update, context, url, 'best', wait_msg, user_id)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    url = context.user_data.get('current_url')
    if not url:
        await query.edit_message_text("❌ Session expired. Link dobara bhejein.")
        return
        
    wait_msg = await query.edit_message_text(f"⏳ **Downloading {query.data.upper()}...**", parse_mode=ParseMode.MARKDOWN)
    await process_download(update, context, url, query.data, wait_msg, query.from_user.id)

# Common Download & Upload Logic
async def process_download(update, context, url, quality, wait_msg, user_id):
    try:
        file_path, title, duration, width, height = download_media(url, quality)
        
        if file_path and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            
            # Size Limit Check
            if file_size > MAX_FILE_SIZE:
                await wait_msg.edit_text(f"❌ File is too big ({round(file_size/(1024*1024))}MB).\nTelegram Bot limit is 50MB.")
                os.remove(file_path)
                return

            await wait_msg.edit_text("📤 **Uploading...**", parse_mode=ParseMode.MARKDOWN)
            
            with open(file_path, 'rb') as f:
                if quality == 'audio':
                    await context.bot.send_audio(
                        chat_id=user_id, 
                        audio=f, 
                        title=title, 
                        caption="Downloaded by Bot"
                    )
                else:
                    await context.bot.send_video(
                        chat_id=user_id, 
                        video=f, 
                        caption=title, 
                        supports_streaming=True,
                        width=width, 
                        height=height, 
                        duration=duration,
                        read_timeout=120, 
                        write_timeout=120
                    )
            
            increment_download(user_id)
            await wait_msg.delete()
        
        else:
            await wait_msg.edit_text("❌ Download Failed.\nCheck Cookies or Link Validity.")

    except Exception as e:
        logger.error(f"Process Error: {e}")
        try: await wait_msg.edit_text("❌ Error during processing.")
        except: pass
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

# --- ADMIN ---
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    data = load_data()
    dl_count = sum(u["count"] for u in data.values())
    await update.message.reply_text(f"📊 Users: {len(data)} | Downloads: {dl_count}")

# --- SETUP ---
ptb_application.add_handler(CommandHandler("start", start))
ptb_application.add_handler(CommandHandler("stats", stats))
ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
ptb_application.add_handler(CallbackQueryHandler(button_handler))

async def setup_bot():
    await ptb_application.initialize()
    url = f"{WEBHOOK_URL}/webhook"
    if (await ptb_application.bot.get_webhook_info()).url != url:
        await ptb_application.bot.set_webhook(url=url)

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    if request.method == "POST":
        data = request.get_json(force=True)
        update = Update.de_json(data, ptb_application.bot)
        asyncio.run(ptb_application.process_update(update))
        return "OK"
    return "Invalid"

@app.route('/')
def index(): return "Multi-Platform Bot Running"

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
