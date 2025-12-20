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

# --- WEBSITE CONFIGURATION ---
# Default website. ENV se change kar sakte hain.
TARGET_DOMAIN = os.getenv("WEBSITE_URL", "https://hdhub4u.rehab").rstrip("/")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

# Limit Settings
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
logger = logging.getLogger("SmartBot")

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
# Increased workers for parallel processing
executor = ThreadPoolExecutor(max_workers=6)

# --- UTILS ---
async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 **Access Denied!**")
        except: pass
        return False
    return True

# --- SMART ENGINE (HUMAN MIMIC) ---

def get_headers(referer=None):
    """Returns headers that look exactly like a real Chrome Browser"""
    head = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    }
    if referer:
        head["Referer"] = referer
    return head

def get_cookies_dict():
    cookies = {}
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r') as f:
                for line in f:
                    if not line.startswith("#") and line.strip():
                        parts = line.strip().split('\t')
                        if len(parts) >= 7:
                            cookies[parts[5]] = parts[6]
        except: pass
    return cookies

# 1. SEARCH FUNCTION (Accurate)
def search_website_smart(query):
    search_url = f"{TARGET_DOMAIN}/?s={query.replace(' ', '+')}"
    logger.info(f"🔎 Smart Search: {search_url}")
    
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(search_url, timeout=15)
        
        # Check if we were redirected to home (Anti-bot behavior)
        if resp.url.strip('/') == TARGET_DOMAIN.strip('/'):
            logger.warning("Search redirected to Homepage (Anti-Bot Triggered)")
            return []

        soup = BeautifulSoup(resp.text, 'lxml')
        results = []
        
        # Strict parsing: Only look for search results, not recent posts
        # Usually inside a specific container for search results
        # We look for 'li' with class 'thumb' inside 'ul'
        
        # Attempt 1: Standard Structure
        items = soup.select('ul.recent-movies li.thumb')
        if not items:
            # Attempt 2: Article structure
            items = soup.select('article.post')
            
        for item in items:
            link_tag = item.find('a')
            if not link_tag: continue
            
            url = link_tag.get('href')
            
            # Smart Title Extraction
            # Try figcaption > p
            caption = item.find('figcaption')
            if caption and caption.find('p'):
                title = caption.find('p').text.strip()
            # Try img alt
            elif item.find('img') and item.find('img').get('alt'):
                title = item.find('img').get('alt')
            # Try link title attribute
            elif link_tag.get('title'):
                title = link_tag.get('title')
            else:
                title = link_tag.text.strip()
                
            if url and title:
                # Clean Title
                title = title.replace("Download", "").replace("Full Movie", "").strip()
                results.append({"title": title, "url": url})
                if len(results) >= 8: break
        
        return results

    except Exception as e:
        logger.error(f"Search Error: {e}")
        return []

# 2. MOVIE PAGE PARSER (Find Quality Buttons)
def extract_quality_options(url):
    logger.info(f"📂 Parsing Movie Page: {url}")
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        options = []
        
        # Smart Regex to find quality links
        # Matches: "Download 720p", "1080p Link", "480p", "Telegram"
        quality_patterns = re.compile(r'(480p|720p|1080p|2160p|4k|hevc|telegram|g-drive|watch online)', re.IGNORECASE)
        
        # Scan all links in the main content area (usually entry-content)
        content_div = soup.find('div', class_='entry-content')
        if not content_div: content_div = soup # Fallback to full page
        
        all_links = content_div.find_all('a', href=True)
        
        for a in all_links:
            text = a.get_text(" ", strip=True)
            href = a['href']
            
            # Skip internal junk links
            if "wp-login" in href or "#" in href or "javascript" in href: continue
            
            # Check if link text matches a quality pattern
            if quality_patterns.search(text) or "Download" in text:
                
                # Assign an Icon based on type
                icon = "📥"
                if "720" in text: icon = "🎥"
                elif "1080" in text: icon = "💎"
                elif "480" in text: icon = "📱"
                elif "Telegram" in text or "t.me" in href: icon = "✈️"
                elif "Watch" in text: icon = "▶️"
                
                # Clean Label
                label = f"{icon} {text.replace('Download', '').replace('Links', '').strip()[:25]}"
                if len(label) < 4: label = f"{icon} Download Link"
                
                # Deduplicate
                if not any(o['url'] == href for o in options):
                    options.append({"label": label, "url": href})

        return options
    except Exception as e:
        logger.error(f"Page Parse Error: {e}")
        return []

# 3. DEEP LINK EXTRACTOR (The "Human Click" Logic)
def resolve_landing_page(url):
    """
    Visits the intermediate download page to find the REAL link.
    """
    logger.info(f"🕵️ Deep resolving: {url}")
    
    # Check if it's already a Telegram link
    if "t.me" in url:
        return {"type": "telegram", "url": url}
    
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        # Follow redirects (upto a limit)
        resp = session.get(url, timeout=20, allow_redirects=True)
        
        # Final URL check
        if "t.me" in resp.url:
            return {"type": "telegram", "url": resp.url}
            
        soup = BeautifulSoup(resp.text, 'lxml')
        
        # LOGIC: Find the "Click to Verify" or "Fast Download" button
        # Common classes on these sites: .btn, .button, or links inside .code-block
        
        # Priority 1: Look for "HubCloud" or "Drive" links in the new page
        target_links = []
        for a in soup.find_all('a', href=True):
            h = a['href']
            t = a.text.lower()
            if any(x in h for x in ['hubcloud', 'gdfluen', 'drive', 'hubdrive', 'file']):
                target_links.append(h)
            elif "verify" in t or "click here" in t or "unlock" in t:
                target_links.append(h)
        
        if target_links:
            # Return the first valid looking link
            return {"type": "link", "url": target_links[0]}
            
        # If no deeper link found, return the current URL (User can handle it)
        return {"type": "link", "url": resp.url}

    except Exception as e:
        return {"type": "error", "error": str(e)}

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    txt = (
        f"👋 **Smart Bot Active!**\n"
        f"🌐 Site: `{TARGET_DOMAIN}`\n\n"
        "🤖 **Features:**\n"
        "1. **Smart Search:** `/search MovieName`\n"
        "2. **Deep Extraction:** I click buttons like a human!\n"
        "3. **Telegram Finder:** I prioritize Telegram links."
    )
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ Admin: `/add`, `/remove`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        cmd, target = update.message.text.split()
        target = int(target)
        if cmd == "/add": AUTHORIZED_USERS.add(target)
        elif cmd == "/remove": AUTHORIZED_USERS.discard(target)
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text("✅ Done")
    except: await update.message.reply_text("Usage: `/add 12345`")

# --- SEARCH HANDLER ---
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Kalki`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 **Smart Search:** `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_website_smart, query)
    
    if not results:
        await update.message.reply_text(f"❌ No results for `{query}`.\n(Try checking spelling or Cookies)")
        return
    
    context.user_data['search_res'] = results
    
    keyboard = []
    for idx, movie in enumerate(results):
        # Callback: s_{index} (s for selection)
        keyboard.append([InlineKeyboardButton(f"🎬 {movie['title']}", callback_data=f"s_{idx}")])
        
    await update.message.reply_text(f"✅ Found {len(results)} movies:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- BUTTON HANDLER ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # 1. MOVIE SELECTED -> SHOW QUALITY OPTIONS
    if data.startswith("s_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): 
            await query.edit_message_text("❌ Session expired. Search again.")
            return
            
        movie = results[idx]
        # Store selected movie url
        context.user_data['selected_movie_url'] = movie['url']
        
        await query.edit_message_text(f"📂 **Parsing Movie Page...**\n`{movie['title']}`\n\n(Finding all Blue Links...)")
        
        loop = asyncio.get_running_loop()
        quality_options = await loop.run_in_executor(executor, extract_quality_options, movie['url'])
        
        if not quality_options:
            await query.edit_message_text("❌ Bot couldn't find download buttons.\n(Page structure might be complex)")
            return
        
        # Save options to context so we can resolve them later
        context.user_data['quality_opts'] = quality_options
        
        keyboard = []
        for i, opt in enumerate(quality_options):
            # Callback: l_{index} (l for link resolution)
            keyboard.append([InlineKeyboardButton(opt['label'], callback_data=f"l_{i}")])
            
        await query.edit_message_text(f"🎬 **{movie['title']}**\n👇 Select Quality to Fetch Link:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 2. QUALITY SELECTED -> DEEP RESOLVE (The "Human Click")
    if data.startswith("l_"):
        idx = int(data.split("_")[1])
        opts = context.user_data.get('quality_opts', [])
        if idx >= len(opts): return
        
        target_opt = opts[idx]
        await query.edit_message_text(f"🕵️ **Deep Extracting...**\nOpening: {target_opt['label']}\n\n(Please wait, visiting link...)")
        
        loop = asyncio.get_running_loop()
        final_result = await loop.run_in_executor(executor, resolve_landing_page, target_opt['url'])
        
        if final_result.get("type") == "error":
            await query.edit_message_text(f"❌ Failed to extract: {final_result.get('error')}")
            return
            
        final_url = final_result.get("url")
        
        # Display the result
        if "t.me" in final_url:
            msg = f"✈️ **Telegram Link Found!**\n\n🔗 [Join Channel / Download]({final_url})"
            kb = [[InlineKeyboardButton("✈️ Open Telegram", url=final_url)]]
        else:
            msg = f"✅ **Download Link Extracted!**\n\nOriginal: {target_opt['label']}\n\n🔗 **Link:** `{final_url}`"
            kb = [[InlineKeyboardButton("⬇️ Download Now", url=final_url)]]
            
        # Give option to go back
        kb.append([InlineKeyboardButton("🔙 Back to Qualities", callback_data="back_to_qual")])
        
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    # 3. BACK BUTTON
    if data == "back_to_qual":
        opts = context.user_data.get('quality_opts', [])
        keyboard = []
        for i, opt in enumerate(opts):
            keyboard.append([InlineKeyboardButton(opt['label'], callback_data=f"l_{i}")])
        await query.edit_message_text("👇 Select Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- GENERIC DOWNLOADER (YouTube/Insta) ---
async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if "http" not in url: return
    
    await update.message.reply_text("⚡ Processing URL (Generic)...")
    
    def dl_task():
        if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
        opts = {'outtmpl': f'{DOWNLOAD_DIR}/vid_%(id)s.%(ext)s', 'format': 'best', 'quiet': True}
        if os.path.exists(COOKIE_FILE): opts['cookiefile'] = COOKIE_FILE
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        loop = asyncio.get_running_loop()
        path = await loop.run_in_executor(executor, dl_task)
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
