import os
import logging
import asyncio
import time
import sys
import ujson as json
import requests
import re
import urllib.parse
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

# --- WEBSITE CONFIG ---
TARGET_DOMAIN = os.getenv("WEBSITE_URL", "https://hdhub4u.rehab").rstrip("/")

# --- SETTINGS ---
DOWNLOAD_DIR = "downloads"
DATA_FILE = "users.json"
COOKIE_FILE = "cookies.txt"

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

# --- SMART ENGINE ---

def get_headers(referer=None):
    head = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer if referer else TARGET_DOMAIN,
        "Upgrade-Insecure-Requests": "1"
    }
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

# 1. SMART SEARCH (Accurate Results Only)
def search_website_smart(query):
    search_url = f"{TARGET_DOMAIN}/?s={query.replace(' ', '+')}"
    logger.info(f"🔎 Searching: {query}")
    
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(search_url, timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        results = []
        
        # [span_0](start_span)[span_1](start_span)Structure Analysis from "New Text Document.txt"[span_0](end_span)[span_1](end_span)
        # We need to find 'li' with class 'thumb' inside 'ul'
        # Or 'article' tags if layout changes.
        
        candidates = soup.select('ul.recent-movies li.thumb') + soup.select('article.post')
        
        query_words = query.lower().split()
        
        for item in candidates:
            link_tag = item.find('a')
            if not link_tag: continue
            
            url = link_tag.get('href')
            
            # Title extraction logic
            caption = item.find('figcaption')
            if caption:
                title = caption.text.strip()
            elif link_tag.get('title'):
                title = link_tag.get('title')
            else:
                title = link_tag.text.strip()
            
            # **FILTER LOGIC:**
            # Check if at least one word from query exists in title to avoid junk
            if title and url:
                title_lower = title.lower()
                if any(word in title_lower for word in query_words):
                    clean_title = title.replace("Download", "").replace("Full Movie", "").strip()
                    results.append({"title": clean_title, "url": url})
        
        # If no results matched filter, return raw top 5 (fallback)
        if not results and candidates:
             for i, item in enumerate(candidates):
                if i >= 5: break
                link = item.find('a')
                if link: results.append({"title": link.get('title', 'Unknown'), "url": link['href']})

        return results[:8] # Limit to 8

    except Exception as e:
        logger.error(f"Search Error: {e}")
        return []

# 2. MOVIE PAGE PARSER (Clean Qualities Only)
def extract_quality_options(url):
    logger.info(f"📂 Parsing: {url}")
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        options = []
        
        # Identify the MAIN CONTENT area to avoid sidebar links
        # WordPress usually uses 'entry-content' or 'post-inner'
        main_content = soup.find('div', class_='entry-content') or soup.find('div', class_='post-content')
        if not main_content: main_content = soup # Fallback
        
        # Keywords for valid links
        valid_kw = ['hubcloud', 'drive', 'instant', 'download', 'watch online']
        quality_kw = ['480p', '720p', '1080p', '4k']
        
        # Find links specifically formatted as buttons often centered
        # Based on user description: "Hub Cloud and Instant Download ka button"
        
        all_links = main_content.find_all('a', href=True)
        
        for a in all_links:
            text = a.get_text(" ", strip=True)
            href = a['href']
            
            # Strict Filtering
            if "comment" in href or "#" in href: continue
            
            # Check if it's a download link
            is_download = any(k in text.lower() for k in valid_kw) or any(k in text.lower() for k in quality_kw)
            
            if is_download:
                # Determine Label
                label = text[:30].replace("Download", "").replace("Links", "").strip()
                if not label: label = "Download Link"
                
                # Add Icon
                icon = "📥"
                if "720" in label: icon = "🎥"
                elif "1080" in label: icon = "💎"
                elif "hub" in label.lower(): icon = "☁️"
                
                final_label = f"{icon} {label}"
                
                # Deduplicate
                if not any(o['url'] == href for o in options):
                    options.append({"label": final_label, "url": href})

        return options
    except Exception as e:
        logger.error(f"Parse Error: {e}")
        return []

# 3. AUTO-BYPASSER (The "Bot" Logic)
def bypass_mediator(url):
    """
    Simulates the User Journey: HubCloud -> Verify -> Timer -> Final Link
    """
    logger.info(f"🤖 Bypassing: {url}")
    session = requests.Session()
    session.headers.update(get_headers(TARGET_DOMAIN))
    session.cookies.update(get_cookies_dict())
    
    try:
        # Step 1: Visit the HubCloud / Intermediate Page
        resp1 = session.get(url, timeout=15, allow_redirects=True)
        soup1 = BeautifulSoup(resp1.text, 'lxml')
        
        # Check if we already have Telegram link
        if "t.me" in resp1.url: return {"type": "telegram", "url": resp1.url}
        
        # Step 2: Find "Verify" / "Not Robot" Button
        # User said: "not a robot ka option Aata hai"
        # We look for form submissions or links with 'verify', 'token', 'generate'
        
        next_link = None
        
        # Pattern A: Link based verification
        for a in soup1.find_all('a', href=True):
            h = a['href']
            t = a.text.lower()
            if "verify" in t or "robot" in t or "generate" in t or "unlock" in t:
                next_link = h
                break
        
        # Pattern B: Form based (POST request) - Common in HubCloud
        if not next_link:
            form = soup1.find('form', id='landing') # Common ID
            if form:
                inputs = {i['name']: i.get('value', '') for i in form.find_all('input') if i.has_attr('name')}
                action = form.get('action')
                if action:
                    # Simulate clicking "I am not a robot"
                    time.sleep(1) # Fake delay
                    post_resp = session.post(action, data=inputs)
                    soup1 = BeautifulSoup(post_resp.text, 'lxml')
                    # Now we are on the "Timer" page
        
        # Step 3: Handle Timer Page (5 seconds)
        # User said: "5 second ka timer... continue ka option"
        # Bots don't need to wait 5 seconds if they can find the link in HTML code!
        
        # Look for the FINAL link usually hidden or generated
        final_candidates = []
        
        # Look for 't.me' explicitly first (Priority)
        for a in soup1.find_all('a', href=True):
            if "t.me" in a['href']:
                return {"type": "telegram", "url": a['href']}
            
            # Look for "drive.google" or "gdtot" or "file"
            if any(x in a['href'] for x in ['drive.google', 'mega.nz', 'gdtot', 'filepress']):
                final_candidates.append(a['href'])
                
            # Look for text "Download Here" / "Click Here"
            if "click" in a.text.lower() or "download" in a.text.lower():
                final_candidates.append(a['href'])

        if final_candidates:
            # Return the best candidate
            return {"type": "link", "url": final_candidates[0]}
            
        # If we failed to find the deep link, return the current URL (User has to do last step)
        return {"type": "partial", "url": resp1.url}

    except Exception as e:
        return {"type": "error", "error": str(e)}

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    txt = (
        f"🤖 **Auto-Bypass Bot Active!**\n\n"
        f"🔍 `/search MovieName`\n"
        f"⚡ **Smart Mode:** Enabled\n"
        f"🎯 **Target:** Telegram Links"
    )
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ Admin: `/add`, `/remove`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    if not context.args: return await update.message.reply_text("Usage: `/search MovieName`")
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching **Accurate Results** for: `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_website_smart, query)
    
    if not results:
        await update.message.reply_text("❌ No accurate results found.")
        return
    
    context.user_data['search_res'] = results
    keyboard = []
    for idx, movie in enumerate(results):
        keyboard.append([InlineKeyboardButton(f"🎬 {movie['title']}", callback_data=f"s_{idx}")])
        
    await update.message.reply_text(f"✅ Found {len(results)} matches:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # 1. MOVIE SELECTED -> PARSE QUALITIES
    if data.startswith("s_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): return
        
        movie = results[idx]
        await query.edit_message_text(f"💿 **Analyzing Page...**\n`{movie['title']}`")
        
        loop = asyncio.get_running_loop()
        options = await loop.run_in_executor(executor, extract_quality_options, movie['url'])
        
        if not options:
            await query.edit_message_text("❌ No Download/HubCloud links found in Main Post.")
            return
            
        context.user_data['q_opts'] = options
        kb = []
        for i, opt in enumerate(options):
            kb.append([InlineKeyboardButton(opt['label'], callback_data=f"b_{i}")])
            
        await query.edit_message_text(f"🎬 **{movie['title']}**\n👇 Select Link to Bypass:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    # 2. LINK SELECTED -> AUTO BYPASS
    if data.startswith("b_"):
        idx = int(data.split("_")[1])
        opts = context.user_data.get('q_opts', [])
        if idx >= len(opts): return
        
        target = opts[idx]
        await query.edit_message_text(f"🤖 **Bypassing Mediator...**\nTarget: {target['label']}\n\n⏳ Solving Captcha/Timer internally...")
        
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(executor, bypass_mediator, target['url'])
        
        if res.get("type") == "error":
            await query.edit_message_text(f"❌ Bypass Failed: {res.get('error')}")
            return
            
        final_url = res.get("url")
        
        if res.get("type") == "telegram":
            msg = f"✈️ **Telegram Link Extracted!**\n\n🔗 [Click to Join/Download]({final_url})"
            kb = [[InlineKeyboardButton("✈️ Open Telegram", url=final_url)]]
        else:
            msg = f"✅ **Final Link Extracted!**\n\n🔗 [Download Now]({final_url})"
            kb = [[InlineKeyboardButton("⬇️ Open Link", url=final_url)]]
            
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return
        
    if data == "back":
        await query.delete_message()

# --- ADMIN & DOWNLOADER ---
async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        cmd, target = update.message.text.split()
        if cmd == "/add": AUTHORIZED_USERS.add(int(target))
        elif cmd == "/remove": AUTHORIZED_USERS.discard(int(target))
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text("✅ Done")
    except: pass

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    await update.message.reply_text("⚡ Direct Downloader not supported in Smart Search Mode.")

# --- STARTUP ---
async def main():
    req = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60)
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("search", search_command))
    app_bot.add_handler(CommandHandler(["add", "remove"], admin_ops))
    app_bot.add_handler(CallbackQueryHandler(button_callback))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    await app_bot.initialize()
    if WEBHOOK_URL: await app_bot.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
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
