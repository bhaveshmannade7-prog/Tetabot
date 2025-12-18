import os
import logging
import asyncio
import json
import time
import shutil
import requests
import re
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
DAILY_LIMIT = 50
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB Limit

PROCESSING_QUEUE = set()

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- COOKIE SETUP ---
if COOKIES_ENV and not os.path.exists(COOKIE_FILE):
    try:
        with open(COOKIE_FILE, 'w') as f:
            f.write(COOKIES_ENV)
    except:
        pass

# --- HELPER: ULTIMATE URL RESOLVER ---
def resolve_url(url):
    try:
        logger.info(f"Original URL: {url}")
        
        # 1. TeraBox 'surl' Fix (Direct String Manipulation - Fastest & Safest)
        if "terabox" in url or "terashare" in url or "1024tera" in url:
            # Agar 'surl=' dikh jaye, toh network call mat karo, seedha fix karo
            if "surl=" in url:
                match = re.search(r'surl=([a-zA-Z0-9_-]+)', url)
                if match:
                    code = match.group(1)
                    final_url = f"https://www.terabox.com/s/1{code}"
                    logger.info(f"Fixed 'surl' Link -> {final_url}")
                    return final_url
            
            # Agar link shortener hai (teraboxurl), toh resolve karo
            if "teraboxurl" in url:
                try:
                    resp = requests.head(url, allow_redirects=True, timeout=5)
                    resolved = resp.url
                    # Resolve hone ke baad wapas surl check karo
                    if "surl=" in resolved:
                        match = re.search(r'surl=([a-zA-Z0-9_-]+)', resolved)
                        if match:
                            return f"https://www.terabox.com/s/1{match.group(1)}"
                    return resolved
                except:
                    pass

        # 2. Domain Corrections
        if "terabox.app" in url:
            return url.replace("terabox.app", "terabox.com")
            
        return url
    except Exception as e:
        logger.error(f"URL Fix Error: {e}")
        return url

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
    if str_id not in data: data[str_id] = {"premium": False, "date": today, "count": 0}
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

# --- HELPER ---
async def check_subscription(user_id, bot):
    if not REQUIRED_CHANNEL: return True
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        if member.status in ["left", "kicked"]: return False
        return True
    except: return True

# --- DOWNLOADER ENGINE ---
def download_media(url, mode='best'):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    # 1. Fix URL Format
    final_url = resolve_url(url)
    logger.info(f"Passing to yt-dlp: {final_url}")

    # 2. Configure Options
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        # Diskwala/Generic ke liye zaroori settings:
        'allow_unplayable_formats': True,
        'ignoreerrors': True,
    }

    # Cookies check
    if "diskwala" not in final_url and os.path.exists(COOKIE_FILE):
        ydl_opts['cookiefile'] = COOKIE_FILE

    # Mode Selection
    if mode == 'audio':
        ydl_opts.update({'format': 'bestaudio/best', 'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]})
    else:
        # Generic fallback ke liye 'best' format safe rehta hai
        ydl_opts.update({
            'format': 'best[ext=mp4][filesize<50M]/best[filesize<50M]/best',
            # Video Convertor hata diya hai generic sites ke liye taaki error na aaye
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(final_url, download=True)
            
            # Agar info None hai (Download fail)
            if not info:
                return None, None, None, None, None, "Extraction Failed"

            filename = ydl.prepare_filename(info)
            
            # Extension Check
            base, ext = os.path.splitext(filename)
            final_filename = base + ".mp3" if mode == 'audio' else filename
            
            if mode == 'audio' and os.path.exists(filename) and not os.path.exists(final_filename):
                # Kabhi kabhi yt-dlp rename nahi karta, hum manually check kar rahe hain
                pass 

            return filename, info.get('title', 'Media'), info.get('duration'), info.get('width'), info.get('height'), None

    except Exception as e:
        return None, None, None, None, None, str(e)

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Bot Ready!**\nSupports: YouTube, TeraBox, Diskwala.", parse_mode=ParseMode.MARKDOWN)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    update_id = update.update_id
    if update_id in PROCESSING_QUEUE: return
    PROCESSING_QUEUE.add(update_id)

    try:
        url = update.message.text.strip()
        if "http" not in url: return
        
        if not await check_subscription(user_id, context.bot):
            await update.message.reply_text(f"🚫 Join {REQUIRED_CHANNEL} first.")
            return

        _, user_data = get_user_data(user_id)
        if not user_data["premium"] and user_data["count"] >= DAILY_LIMIT:
            await update.message.reply_text("🚫 Daily Limit Reached.")
            return

        if "youtube.com" in url or "youtu.be" in url:
            context.user_data['current_url'] = url
            keyboard = [[InlineKeyboardButton("🎵 MP3", callback_data="audio"), InlineKeyboardButton("🎬 360p", callback_data="360")],
                        [InlineKeyboardButton("🎬 720p", callback_data="720"), InlineKeyboardButton("💎 Best", callback_data="best")]]
            await update.message.reply_text("⚙️ **Quality:**", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            # TeraBox / Diskwala
            wait_msg = await update.message.reply_text("⏳ **Processing...**", parse_mode=ParseMode.MARKDOWN)
            await process_download(update, context, url, 'best', wait_msg, user_id)

    except Exception as e:
        logger.error(f"Handler Error: {e}")
    finally:
        if update_id in PROCESSING_QUEUE: PROCESSING_QUEUE.remove(update_id)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = context.user_data.get('current_url')
    if not url:
        await query.edit_message_text("❌ Link expired.")
        return
    wait_msg = await query.edit_message_text(f"⏳ **Downloading {query.data.upper()}...**")
    await process_download(update, context, url, query.data, wait_msg, query.from_user.id)

async def process_download(update, context, url, quality, wait_msg, user_id):
    file_path = None
    try:
        file_path, title, duration, width, height, error_msg = download_media(url, quality)
        
        if file_path and os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                await wait_msg.edit_text(f"❌ File too big ({round(file_size/(1024*1024))}MB). Limit 50MB.")
                os.remove(file_path)
                return

            await wait_msg.edit_text("📤 **Uploading...**")
            with open(file_path, 'rb') as f:
                if quality == 'audio':
                    await context.bot.send_audio(chat_id=user_id, audio=f, title=title)
                else:
                    await context.bot.send_video(chat_id=user_id, video=f, caption=title, supports_streaming=True, width=width, height=height, duration=duration, read_timeout=120, write_timeout=120)
            increment_download(user_id)
            await wait_msg.delete()
        else:
            # Error Formatting
            clean_error = str(error_msg).replace(os.getcwd(), "")[:300]
            if "Unsupported URL" in clean_error and "terabox" in url:
                clean_error = "TeraBox Link Format Issue. (Still working on fix)"
            elif "Unsupported URL" in clean_error and "diskwala" in url:
                clean_error = "Diskwala is not supported directly (Landing Page detected)."
            
            await wait_msg.edit_text(f"❌ **Failed.**\n\nError: `{clean_error}`", parse_mode=ParseMode.MARKDOWN)
            
    except Exception as e:
        logger.error(f"Process Error: {e}")
        try: await wait_msg.edit_text("❌ Bot Processing Error.")
        except: pass
    finally:
        if file_path and os.path.exists(file_path): 
            try: os.remove(file_path)
            except: pass

# --- SETUP ---
ptb_application.add_handler(CommandHandler("start", start))
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
        if update.update_id in PROCESSING_QUEUE: return "OK"
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
