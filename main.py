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

# --- WEBSITE CONFIGURATION (New Feature) ---
# Environment variable 'WEBSITE_URL' se link uthayega. 
# Agar set nahi hai to default use karega.
TARGET_DOMAIN = os.getenv("WEBSITE_URL", "https://hdhub4u.rehab").rstrip("/")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

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
    # YT-DLP ke liye file banayenge
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
async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 **Access Denied!**")
        except: pass
        return False
    return True

# --- ADVANCED SCRAPER ENGINE ---

def get_headers():
    """Real Browser Headers to bypass blocking"""
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": TARGET_DOMAIN,
        "Upgrade-Insecure-Requests": "1"
    }

def get_request_cookies():
    """
    Parses cookies.txt to dictionary for 'requests' library.
    This helps bypass Cloudflare if you have fresh cookies.
    """
    cookies = {}
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r') as f:
                for line in f:
                    if not line.startswith("#") and len(line.strip()) > 0:
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookies[parts[5]] = parts[6]
        except: pass
    return cookies

def search_website(query):
    """
    Searches the website using the query.
    Handles 'No Results' by checking status code and HTML structure.
    """
    search_url = f"{TARGET_DOMAIN}/?s={query.replace(' ', '+')}"
    logger.info(f"Searching: {search_url}")
    
    try:
        session = requests.Session()
        session.headers.update(get_headers())
        session.cookies.update(get_request_cookies())
        
        resp = session.get(search_url, timeout=15)
        
        if resp.status_code != 200:
            logger.error(f"Website Blocked Bot. Status Code: {resp.status_code}")
            return {"error": f"Website Blocked (Status {resp.status_code}). Update Cookies."}
            
        soup = BeautifulSoup(resp.text, 'lxml')
        results = []
        
        # Generic WordPress Structure Parser
        # Strategies to find movie posts:
        # 1. Look for <article> tags
        # 2. Look for divs with class 'post', 'result', 'thumb'
        
        candidates = soup.find_all('article')
        if not candidates:
            candidates = soup.select('div.post, div.result-item, div.latestPost, div.post-item')
            
        for item in candidates:
            # Find Link and Title
            a_tag = item.find('a')
            img_tag = item.find('img')
            
            if a_tag and a_tag.get('href'):
                url = a_tag['href']
                title = a_tag.get('title')
                
                # If title not in a_tag, check inside or img alt
                if not title:
                    title = a_tag.text.strip()
                if not title and img_tag:
                    title = img_tag.get('alt')
                
                if title and url and "http" in url:
                    # Clean title
                    title = title.replace("Download", "").replace("Watch", "").strip()
                    results.append({"title": title, "url": url})
                    if len(results) >= 8: break # Limit results

        if not results:
            # Debugging: Check if we got a Captcha page
            if "Cloudflare" in resp.text or "captcha" in resp.text.lower():
                return {"error": "Cloudflare/Captcha Blocked. Cookies Expired."}
            return []
            
        return results

    except Exception as e:
        logger.error(f"Search Failed: {e}")
        return {"error": str(e)}

def extract_links(url):
    """
    Extracts download links from the movie page (Based on L1.txt structure)
    """
    logger.info(f"Extracting links from: {url}")
    try:
        session = requests.Session()
        session.headers.update(get_headers())
        session.cookies.update(get_request_cookies())
        
        resp = session.get(url, timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        
        links = []
        
        # Based on L1.txt: Links are often in <h3> or <h4> or simple <a> tags
        # We look for specific keywords in the link TEXT or CLASS
        keywords = ['480p', '720p', '1080p', '2160p', '4k', 'Download']
        
        # Strategy: Find all links that look like download links
        all_links = soup.find_all('a', href=True)
        
        for a in all_links:
            text = a.get_text(strip=True)
            href = a['href']
            
            # Simple Filter: If text has quality info
            is_valid = any(k.lower() in text.lower() for k in keywords)
            
            # Avoid category/tag links
            if is_valid and "category" not in href and "tag" not in href:
                # Clean text
                clean_text = text.replace('⚡', '').replace('Download Links', '').strip()
                if not clean_text: clean_text = "Download Link"
                
                # Deduplicate
                if not any(l['url'] == href for l in links):
                    links.append({"quality": clean_text, "url": href})

        return links
    except Exception as e:
        logger.error(f"Extraction Failed: {e}")
        return []

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    current_site = TARGET_DOMAIN.replace("https://", "")
    txt = (
        f"👋 **Bot Ready!**\n"
        f"🌐 **Target Site:** `{current_site}`\n\n"
        "🔎 **Search:** `/search Movie Name`\n"
        "🔗 **Direct:** Send any URL to download."
    )
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ Admin: `/add`, `/remove`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Iron Man`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching on **{TARGET_DOMAIN}** for: `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_website, query)
    
    if isinstance(results, dict) and "error" in results:
        await update.message.reply_text(f"❌ **Error:** {results['error']}")
        return

    if not results:
        await update.message.reply_text("❌ **No results found.**\nTip: Try changing the website URL in ENV if domain changed.")
        return
    
    # Store results
    context.user_data['search_res'] = results
    
    keyboard = []
    for idx, movie in enumerate(results):
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
    
    # 1. Movie Selected -> Fetch Links
    if data.startswith("sel_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        
        if idx >= len(results):
            await query.edit_message_text("❌ Session expired.")
            return
            
        movie = results[idx]
        await query.edit_message_text(f"🔄 **Extracting Links...**\nMovie: {movie['title']}")
        
        loop = asyncio.get_running_loop()
        links = await loop.run_in_executor(executor, extract_links, movie['url'])
        
        if not links:
            await query.edit_message_text("❌ No download links found on page (Structure might have changed).")
            return
            
        msg = f"🎬 **{movie['title']}**\n\n👇 **Click to Download:**"
        keyboard = []
        for link in links:
            # We provide direct URL buttons
            keyboard.append([InlineKeyboardButton(f"📥 {link['quality']}", url=link['url'])])
            
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

# --- GENERIC DOWNLOADER (YouTube/Insta) ---
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if "http" not in url: return
    
    await update.message.reply_text("⚡ Using Generic Downloader...")
    
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

    try:
        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(executor, download_task)
        await update.message.reply_video(video=open(path, 'rb'), caption="✅ Done")
        os.remove(path)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)[:50]}")

# --- STARTUP ---
async def main():
    req = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60, connect_timeout=60)
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("search", search_command))
    app_bot.add_handler(CommandHandler(["add", "remove"], admin_ops))
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
