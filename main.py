import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
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

COOKIES_ENV = os.getenv("COOKIES_CONTENT")
API_MODE = os.getenv("API_MODE", "standard")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

# File Size Limits
if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024  # 2GB
    SERVER_TAG = "🚀 Local Server (2GB)"
else:
    BASE_URL = None
    MAX_FILE_SIZE = 49 * 1024 * 1024    # 50MB (Telegram Cloud Limit)
    SERVER_TAG = "☁️ Cloud Server (50MB)"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BotEngine")

# --- DATA MANAGEMENT (Admin System) ---
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

# --- COOKIE SETUP ---
def setup_cookies():
    valid_lines = ["# Netscape HTTP Cookie File"]
    if COOKIES_ENV and len(COOKIES_ENV) > 10:
        lines = COOKIES_ENV.split('\n')
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 7 and not line.startswith('#'):
                valid_lines.append("\t".join(parts))
    
    with open(COOKIE_FILE, 'w') as f:
        f.write("\n".join(valid_lines))
        f.write("\n")

setup_cookies()

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=4)

# --- UTILS ---
def get_readable_size(size_in_bytes):
    if not size_in_bytes: return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_in_bytes < 1024.0: return f"{size_in_bytes:.1f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.1f} TB"

async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 **Access Denied!** Contact Admin.")
        except: pass
        return False
    return True

# --- MOVIE SEARCH ENGINE (YTS API) ---
def search_movie_api(query_term):
    """
    Searches for movies using YTS Public API.
    Returns list of movies with title, year, and torrent links.
    """
    try:
        url = "https://yts.mx/api/v2/list_movies.json"
        params = {"query_term": query_term, "limit": 5, "sort_by": "year"}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        
        if data.get("status") != "ok" or data.get("data", {}).get("movie_count") == 0:
            return []
            
        return data["data"]["movies"]
    except Exception as e:
        logger.error(f"Movie API Error: {e}")
        return []

# --- UNIVERSAL DOWNLOADER ENGINE ---
def run_downloader(url, quality):
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    timestamp = int(time.time())
    
    is_yt = "youtube.com" in url or "youtu.be" in url
    
    # Configuration
    opts = {
        'outtmpl': f'{DOWNLOAD_DIR}/%(id)s_{timestamp}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'noplaylist': True,
        'socket_timeout': 30,
        # Browser Spoofing
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }

    # Format Logic
    if is_yt:
        fmt = 'bestvideo+bestaudio/best' if quality == 'best' else f'bestvideo[height<={quality}]+bestaudio/best'
        if quality == 'audio': fmt = 'bestaudio/best'
        opts['format'] = fmt
        opts['merge_output_format'] = 'mp4'
        if quality == 'audio': 
            opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
    else:
        opts['format'] = 'best' # Universal fallback

    if os.path.exists(COOKIE_FILE): opts['cookiefile'] = COOKIE_FILE

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
    
    user = update.effective_user.first_name
    txt = (
        f"👋 **Hello {user}!**\n\n"
        f"🚀 **Server:** {SERVER_TAG}\n\n"
        "✨ **Features:**\n"
        "1️⃣ **Universal Downloader:** Send any video link (Insta, YT, FB, Twitter).\n"
        "2️⃣ **Movie Search:** `/search <Movie Name>` likhein."
    )
    
    if update.effective_user.id == OWNER_ID:
        txt += "\n\n👮‍♂️ **Admin:** `/add`, `/remove`, `/users`"
        
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /search command for movies"""
    if not await check_auth(update): return
    
    if not context.args:
        await update.message.reply_text("❌ **Usage:** `/search Avengers`", parse_mode=ParseMode.MARKDOWN)
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 **Searching for:** `{query}`...")
    
    loop = asyncio.get_running_loop()
    movies = await loop.run_in_executor(executor, search_movie_api, query)
    
    if not movies:
        await update.message.reply_text("❌ **No movies found!** Try a different name.")
        return

    # Create Buttons for results
    keyboard = []
    for movie in movies:
        title = movie.get('title')
        year = movie.get('year')
        movie_id = movie.get('id')
        btn_text = f"🎬 {title} ({year})"
        # Store movie ID in callback data
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"mov_{movie_id}")])
    
    # Store movie data in context for retrieval later (Simple cache)
    context.user_data['search_results'] = {str(m['id']): m for m in movies}
    
    await update.message.reply_text(
        f"found {len(movies)} results for `{query}`:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        cmd, target = update.message.text.split()
        target = int(target)
        if cmd == "/add": 
            AUTHORIZED_USERS.add(target)
            msg = "✅ User Added"
        elif cmd == "/remove": 
            if target != OWNER_ID: 
                AUTHORIZED_USERS.discard(target)
                msg = "🗑️ User Removed"
            else: msg = "❌ Cannot remove Owner"
        
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(msg)
    except: await update.message.reply_text("Usage: `/add 12345`")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if not url.startswith("http"): return

    context.user_data['url'] = url
    
    # Generic Downloader UI
    keyboard = [
        [InlineKeyboardButton("🎵 Audio", callback_data="audio")],
        [InlineKeyboardButton("🎥 720p", callback_data="720"), InlineKeyboardButton("💎 Best", callback_data="best")]
    ]
    
    if "youtube" in url or "youtu.be" in url:
        site = "YouTube"
    elif "instagram" in url:
        site = "Instagram"
    else:
        site = "Web"

    await update.message.reply_text(
        f"🔗 **Link Detected: {site}**\n👇 Select Quality:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # --- MOVIE DOWNLOAD HANDLER ---
    if data.startswith("mov_"):
        movie_id = data.split("_")[1]
        movies_cache = context.user_data.get('search_results', {})
        movie = movies_cache.get(movie_id)
        
        if not movie:
            await query.edit_message_text("❌ Data expired. Search again.")
            return
        
        # Build Download Info
        txt = f"🎬 **{movie['title']} ({movie['year']})**\n\n"
        txt += f"⭐ Rating: {movie.get('rating')}/10\n"
        txt += f"⏱ Duration: {movie.get('runtime')} min\n\n"
        txt += "⬇️ **Download Links (Torrent/Magnet):**\n"
        
        torrents = movie.get('torrents', [])
        keyboard = []
        
        for t in torrents:
            quality = t.get('quality')
            size = t.get('size')
            url = t.get('url') # .torrent file link
            hash_val = t.get('hash')
            
            # Create Magnet Link
            magnet = f"magnet:?xt=urn:btih:{hash_val}&dn={movie['title']}&tr=udp://open.demonii.com:1337/announce"
            
            # Note: Bots can't upload magnets directly easily, so we give the .torrent URL or Text
            txt += f"🔹 **{quality}** ({size})\nLink: `{url}`\n\n"
            
        await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        return

    # --- VIDEO DOWNLOAD HANDLER ---
    url = context.user_data.get('url')
    if not url: return

    quality = data
    await query.edit_message_text(f"⚡ **Downloading...**\n⏳ Please wait...", parse_mode=ParseMode.MARKDOWN)
    
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(executor, run_downloader, url, quality)
    
    if not result['status']:
        await query.edit_message_text(f"❌ **Error:**\n`{result.get('error')[:200]}`", parse_mode=ParseMode.MARKDOWN)
        return

    path = result['path']
    size = os.path.getsize(path)
    
    if size > MAX_FILE_SIZE:
        await query.edit_message_text(
            f"❌ **File Too Big!**\nSize: `{get_readable_size(size)}`\nLimit: `{get_readable_size(MAX_FILE_SIZE)}`\n\n⚠️ Cloud Servers cannot upload >50MB."
        )
        os.remove(path)
        return

    await query.edit_message_text(f"📤 **Uploading...**\n📦 Size: `{get_readable_size(size)}`")
    
    try:
        with open(path, 'rb') as f:
            if quality == 'audio':
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id, 
                    audio=f, 
                    title=result['title'],
                    write_timeout=120
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id, 
                    video=f, 
                    caption=f"🎬 {result['title']}", 
                    supports_streaming=True,
                    write_timeout=120
                )
        await query.delete_message()
    except Exception as e:
        logger.error(f"Upload Error: {e}")
        await query.edit_message_text("❌ Upload Failed (Server Timeout)")
    finally:
        if os.path.exists(path): os.remove(path)

# --- APP STARTUP ---
async def main():
    # Crash Proof Timeouts
    req = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60, connect_timeout=60)
    
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    # Handlers
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("search", search_handler)) # New Movie Feature
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
    
