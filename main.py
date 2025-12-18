import os
import logging
import asyncio
import json
import time
import shutil
from datetime import datetime, date
from flask import Flask, request, Response
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError
import yt_dlp
import nest_asyncio

# --- CONFIGURATION ---
nest_asyncio.apply()  # Fixes event loop issues in Flask/Gunicorn

# Load Env Vars
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Your Telegram User ID
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "") # e.g., "@mychannel"

# Constants
DOWNLOAD_DIR = "downloads"
DATA_FILE = "data.json"
DAILY_LIMIT = 3
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB (Telegram Bot API limit)

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- DATA PERSISTENCE (Simple JSON) ---
# Structure: { user_id: { "premium": bool, "mode": "video/audio", "date": "YYYY-MM-DD", "count": int } }

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def get_user_data(user_id):
    data = load_data()
    str_id = str(user_id)
    today = str(date.today())
    
    if str_id not in data:
        data[str_id] = {"premium": False, "mode": "video", "date": today, "count": 0}
    
    # Reset daily limit if new day
    if data[str_id]["date"] != today:
        data[str_id]["date"] = today
        data[str_id]["count"] = 0
        save_data(data)
        
    return data, data[str_id]

def update_user_stat(user_id, key, value):
    data, user = get_user_data(user_id)
    data[str(user_id)][key] = value
    save_data(data)

def increment_download(user_id):
    data, user = get_user_data(user_id)
    data[str(user_id)]["count"] += 1
    save_data(data)

# --- FLASK SERVER SETUP ---
app = Flask(__name__)

# Initialize PTB Application
ptb_application = Application.builder().token(BOT_TOKEN).build()

# --- HELPER FUNCTIONS ---

async def check_subscription(user_id, bot):
    """Check if user has joined the required channel."""
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        if member.status in ["left", "kicked"]:
            return False
        return True
    except TelegramError as e:
        logger.error(f"Channel check error: {e}")
        # If bot isn't admin in channel or channel invalid, allow pass to avoid blocking
        return True

def download_video(url, is_audio_only=False):
    """Downloads video/audio using yt-dlp."""
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    timestamp = int(time.time())
    
    # Options for yt-dlp
    # Note: We restrict to <50MB to ensure Telegram can send it
    ydl_opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    if is_audio_only:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        # Try to get best video+audio under 50MB, otherwise just best regular
        ydl_opts.update({
            'format': 'best[filesize<50M]/best', 
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if is_audio_only:
                # yt-dlp changes extension to mp3 after post-processing
                filename = os.path.splitext(filename)[0] + ".mp3"
            return filename, info.get('title', 'Unknown')
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return None, None

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    _, user = get_user_data(user_id)
    
    msg = (
        "👋 **Welcome to YT Downloader Bot!**\n\n"
        "Send me a YouTube link to download.\n"
        f"Current Mode: **{user['mode'].upper()}**\n\n"
        "**Commands:**\n"
        "/audio - Switch to MP3 Audio mode\n"
        "/video - Switch to MP4 Video mode\n"
        "/help - Show help"
    )
    if REQUIRED_CHANNEL:
        msg += f"\n\n⚠️ You must join {REQUIRED_CHANNEL} to use this bot."
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def set_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user_stat(update.effective_user.id, "mode", "audio")
    await update.message.reply_text("✅ Mode set to **Audio (MP3)**.", parse_mode="Markdown")

async def set_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user_stat(update.effective_user.id, "mode", "video")
    await update.message.reply_text("✅ Mode set to **Video (MP4)**.", parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    # Basic URL Validation
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("❌ Please send a valid YouTube link.")
        return

    # Check Channel Subscription
    if not await check_subscription(user_id, context.bot):
        await update.message.reply_text(
            f"🚫 **Access Denied!**\n\nPlease join our channel {REQUIRED_CHANNEL} to use this bot.",
            parse_mode="Markdown"
        )
        return

    # Check Daily Limits
    _, user_data = get_user_data(user_id)
    if not user_data["premium"] and user_data["count"] >= DAILY_LIMIT:
        await update.message.reply_text(
            f"🚫 **Daily Limit Reached!**\n\nYou have used your {DAILY_LIMIT} free downloads for today.\n"
            "Ask Admin to upgrade to Premium for unlimited access."
        )
        return

    # Processing
    wait_msg = await update.message.reply_text("⏳ Processing... Please wait.")
    
    is_audio = (user_data["mode"] == "audio")
    file_path, title = download_video(url, is_audio)
    
    if file_path and os.path.exists(file_path):
        try:
            # Check file size
            file_size = os.path.getsize(file_path)
            if file_size > MAX_FILE_SIZE:
                await wait_msg.edit_text("❌ File is too large (>50MB) for me to send via Telegram.")
                os.remove(file_path)
                return

            await wait_msg.edit_text("📤 Uploading...")
            
            with open(file_path, 'rb') as f:
                if is_audio:
                    await update.message.reply_audio(audio=f, title=title, caption="Downloaded via Bot")
                else:
                    await update.message.reply_video(video=f, caption=title)
            
            # Update Limit
            increment_download(user_id)
            await wait_msg.delete()
            
        except Exception as e:
            logger.error(f"Send error: {e}")
            await wait_msg.edit_text("❌ Error uploading file.")
        finally:
            # Cleanup
            if os.path.exists(file_path):
                os.remove(file_path)
    else:
        await wait_msg.edit_text("❌ Download failed. Invalid link or geo-restricted content.")

# --- ADMIN COMMANDS ---

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    data = load_data()
    total_users = len(data)
    total_downloads = sum(u["count"] for u in data.values())
    premium_users = sum(1 for u in data.values() if u["premium"])
    
    await update.message.reply_text(
        f"📊 **Bot Statistics**\n\n"
        f"Users: {total_users}\n"
        f"Premium: {premium_users}\n"
        f"Todays Downloads: {total_downloads}" # Note: This is simplified
    )

async def add_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = context.args[0]
        update_user_stat(target_id, "premium", True)
        await update.message.reply_text(f"✅ User {target_id} is now Premium.")
    except IndexError:
        await update.message.reply_text("Usage: /addpremium <user_id>")

async def remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = context.args[0]
        update_user_stat(target_id, "premium", False)
        await update.message.reply_text(f"User {target_id} removed from Premium.")
    except IndexError:
        await update.message.reply_text("Usage: /removepremium <user_id>")

# --- FLASK ROUTES ---

@app.route('/webhook', methods=['POST'])
def webhook_handler():
    """Receives updates from Telegram"""
    if request.method == "POST":
        # Retrieve the update object
        update = Update.de_json(request.get_json(force=True), ptb_application.bot)
        
        # Run the async process_update in the loop
        asyncio.run(ptb_application.process_update(update))
        
        return "OK"
    return "Invalid Request"

@app.route('/')
def index():
    return "Bot is running!"

# --- APP SETUP ---

# Register Handlers
ptb_application.add_handler(CommandHandler("start", start))
ptb_application.add_handler(CommandHandler("audio", set_audio))
ptb_application.add_handler(CommandHandler("video", set_video))
ptb_application.add_handler(CommandHandler("stats", stats))
ptb_application.add_handler(CommandHandler("addpremium", add_premium))
ptb_application.add_handler(CommandHandler("removepremium", remove_premium))
ptb_application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Set Webhook on Startup (Executed only once when script loads)
async def setup_webhook():
    webhook_info = await ptb_application.bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL + "/webhook":
        await ptb_application.bot.set_webhook(url=WEBHOOK_URL + "/webhook")
        logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")

if __name__ == "__main__":
    # Local testing only
    loop = asyncio.get_event_loop()
    loop.run_until_complete(setup_webhook())
    app.run(port=5000)
else:
    # Production (Gunicorn)
    # We need to set the webhook when the app starts. 
    # Since Gunicorn loads this file, we can trigger it here, but carefully.
    # Note: In a multiprocess Gunicorn env, this might run multiple times, which is fine for set_webhook.
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(setup_webhook())
    except Exception as e:
        logger.warning(f"Webhook setup warning: {e}")

