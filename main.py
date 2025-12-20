import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import re
from bs4 import BeautifulSoup
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
# Target Website from your L1.txt
TARGET_DOMAIN = "https://hdhub4u.rehab"

# Limits
if API_MODE == 'local':
    BASE_URL = "http://localhost:8081/bot"
    MAX_FILE_SIZE = 1950 * 1024 * 1024
    SERVER_TAG = "🚀 Local Server (2GB)"
else:
    BASE_URL = None
    MAX_FILE_SIZE = 49 * 1024 * 1024
    SERVER_TAG = "☁️ Cloud Server (50MB)"

# Logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("BotEngine")

# --- DATA ---
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
    if COOKIES_ENV:
        with open(COOKIE_FILE, 'w') as f:
            f.write("# Netscape HTTP Cookie File\n")
            for line in COOKIES_ENV.split('\n'):
                if len(line.strip()) > 10 and not line.startswith('#'):
                    f.write(line + "\n")

setup_cookies()

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=4)

# --- UTILS ---
async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 **Access Denied!**")
        except: pass
        return False
    return True

# --- CUSTOM HDHUB4U SCRAPER ---

def search_hdhub(query):
    """
    Searches HDHub4u for the movie using requests & bs4.
    """
    search_url = f"{TARGET_DOMAIN}/?s={query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    try:
        resp = requests.get(search_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        
        results = []
        # Finding posts in the search result page
        # Usually they are in <article> tags or look for links with class "thumb" or similar
        # Fallback: Find all 'a' tags inside main content area that look like movie links
        
        # Taking a generic approach for WordPress themes (which HDHub uses)
        # Looking for article headers or thumbnails
        for article in soup.find_all('article'):
            link_tag = article.find('a')
            if link_tag and link_tag.get('href'):
                title = link_tag.get('title') or link_tag.text.strip()
                url = link_tag.get('href')
                if title and url:
                    results.append({"title": title, "url": url})
                    if len(results) >= 5: break
        
        # If articles not found (some themes use div based grid)
        if not results:
            for item in soup.select('div.post-item, div.result-item, div.latestPost'):
                link_tag = item.find('a')
                if link_tag:
                    results.append({
                        "title": link_tag.get('title', 'Movie Result'),
                        "url": link_tag['href']
                    })
                    if len(results) >= 5: break

        return results
    except Exception as e:
        logger.error(f"Search Error: {e}")
        return []

def extract_links_from_page(url):
    """
    Extracts download links from a specific movie page based on L1.txt structure.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        
        links = []
        
        # Based on L1.txt, links are often inside <h3> or <h4> tags
        # Example: <h3><a href="...">480p...</a></h3>
        
        # Find all 'a' tags that might be download links
        # We look for keywords like '480p', '720p', '1080p', 'Download' in the text
        keywords = ['480p', '720p', '1080p', '2160p', '4k', 'download']
        
        for a_tag in soup.find_all('a', href=True):
            text = a_tag.text.strip()
            href = a_tag['href']
            
            # Filter logic: Check if text contains quality info
            if any(k in text.lower() for k in keywords) and "http" in href:
                # Clean up the text
                clean_text = text.replace('⚡', '').replace('Download', '').strip()
                if not clean_text: clean_text = "Download Link"
                
                # Check duplicates
                if not any(l['url'] == href for l in links):
                    links.append({"quality": clean_text, "url": href})
        
        return links
    except Exception as e:
        logger.error(f"Extraction Error: {e}")
        return []

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    await update.message.reply_text(
        f"👋 **HDHub4u Bot Ready!**\n\n"
        f"🔎 **To Search:** `/search Movie Name`\n"
        f"🔗 **Direct Link:** Send any video link to download."
    )

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Pathaan`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching **HDHub4u** for: `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_hdhub, query)
    
    if not results:
        await update.message.reply_text("❌ No results found on HDHub4u.")
        return
    
    # Store results in context to handle button clicks
    context.user_data['movie_results'] = results
    
    keyboard = []
    for idx, movie in enumerate(results):
        # Using index as callback data to save space
        btn_text = f"🎬 {movie['title'][:30]}..."
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"sel_{idx}")])
        
    await update.message.reply_text(
        f"✅ Found {len(results)} movies:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # 1. User Selected a Movie
    if data.startswith("sel_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('movie_results', [])
        
        if idx >= len(results):
            await query.edit_message_text("❌ Session expired. Search again.")
            return
            
        movie = results[idx]
        await query.edit_message_text(f"🔄 Fetching links for: **{movie['title']}**...")
        
        # Scrape Links
        loop = asyncio.get_running_loop()
        links = await loop.run_in_executor(executor, extract_links_from_page, movie['url'])
        
        if not links:
            await query.edit_message_text("❌ No download links found on the page.")
            return
            
        # Show Link Buttons
        msg = f"🎬 **{movie['title']}**\n\n⬇️ **Download Links:**"
        keyboard = []
        for link in links:
            # We can't download direct files easily from these redirecting hosts (hubdrive etc)
            # So we provide the Direct Link to user
            btn_text = f"📥 {link['quality']}"
            keyboard.append([InlineKeyboardButton(btn_text, url=link['url'])])
            
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- GENERIC DOWNLOADER (YouTube/Insta) ---
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    
    if "http" not in url: return
    
    # Use yt-dlp for direct video links
    await update.message.reply_text("⚡ Downloading Video...")
    
    try:
        def download_task():
            if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
            ts = int(time.time())
            opts = {
                'outtmpl': f'{DOWNLOAD_DIR}/vid_{ts}.%(ext)s',
                'format': 'best',
                'quiet': True,
                'cookiefile': COOKIE_FILE if os.path.exists(COOKIE_FILE) else None
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info)

        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(executor, download_task)
        
        if os.path.getsize(path) > MAX_FILE_SIZE:
            await update.message.reply_text("❌ File too large for Telegram upload.")
            os.remove(path)
            return
            
        await update.message.reply_video(video=open(path, 'rb'), caption="✅ Downloaded")
        os.remove(path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:50]}")

# --- STARTUP ---
async def main():
    req = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60, connect_timeout=60)
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("search", search_command))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
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
