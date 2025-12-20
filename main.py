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
# Website URL (Default: hdhub4u.rehab)
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
logger = logging.getLogger("SmartBot")

# --- DATA & AUTH ---
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
executor = ThreadPoolExecutor(max_workers=8)

# --- UTILS ---
async def check_auth(update: Update):
    if not update.effective_user: return False
    uid = update.effective_user.id
    if uid not in AUTHORIZED_USERS:
        try: await update.message.reply_text("🔒 **Access Denied!**")
        except: pass
        return False
    return True

# --- SMART NETWORK ENGINE ---

def get_headers(referer=None):
    head = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer: head["Referer"] = referer
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

# --- 1. INTELLIGENT SEARCH (Garbage Remover) ---
def search_smart(query):
    search_url = f"{TARGET_DOMAIN}/?s={query.replace(' ', '+')}"
    logger.info(f"🔎 Scanning: {search_url}")
    
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(search_url, timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        results = []
        
        # --- Strict Filtering ---
        # Only look for actual movie posts, not sidebar items
        # Usually inside a container like 'main', 'primary', or specific classes
        
        candidates = []
        # Priority 1: Search specific lists
        lists = soup.select('ul.recent-movies, div.search-results, main#main')
        if lists:
            for l in lists:
                candidates.extend(l.find_all('li'))
                candidates.extend(l.find_all('article'))
        
        # Priority 2: Generic fallback if layout changes
        if not candidates:
            candidates = soup.find_all('article')

        query_words = query.lower().split()
        
        for item in candidates:
            a_tag = item.find('a')
            if not a_tag: continue
            
            url = a_tag.get('href')
            
            # Smart Title Extraction
            title = ""
            caption = item.find('figcaption')
            if caption: title = caption.text.strip()
            elif a_tag.get('title'): title = a_tag.get('title')
            else: title = a_tag.text.strip()
            
            if not url or not title: continue
            if "page/" in url: continue # Skip pagination links
            
            # --- RELEVANCE CHECK ---
            # Title MUST contain at least one word from query to be shown
            title_lower = title.lower()
            if any(word in title_lower for word in query_words):
                # Clean Title
                clean_title = title.replace("Download", "").replace("Full Movie", "").replace("Free", "").strip()
                results.append({"title": clean_title, "url": url})
        
        return results[:8] # Return top 8 accurate results

    except Exception as e:
        logger.error(f"Search Error: {e}")
        return []

# --- 2. SURGICAL EXTRACTION (No Trash Links) ---
def extract_quality_smart(url):
    logger.info(f"📂 Extracting: {url}")
    try:
        session = requests.Session()
        session.headers.update(get_headers(TARGET_DOMAIN))
        session.cookies.update(get_cookies_dict())
        
        resp = session.get(url, timeout=20)
        soup = BeautifulSoup(resp.text, 'lxml')
        options = []
        
        # --- SURGICAL SCOPE ---
        # Only look inside 'entry-content' or 'the-content' div
        # This removes "Related Movies", Sidebar, Footer, etc.
        content_area = soup.find('div', class_=re.compile(r'(entry-content|the-content|post-content)'))
        
        if not content_area: 
            logger.warning("Main content area not found, scanning full page carefully.")
            content_area = soup
            
        # Regex for valid qualities
        valid_res = re.compile(r'(480p|720p|1080p|2160p|4k|10bit|hevc)', re.IGNORECASE)
        
        all_links = content_area.find_all('a', href=True)
        
        for a in all_links:
            text = a.get_text(" ", strip=True)
            href = a['href']
            
            # Strict Filter: Must look like a download button
            # Usually these buttons have specific classes or text styles
            is_download_link = (
                valid_res.search(text) or 
                "Download" in text or 
                "HubCloud" in text or 
                "Drive" in text or
                "Instant" in text
            )
            
            if is_download_link and "http" in href:
                # Icon assignment
                icon = "📥"
                if "1080p" in text: icon = "💎 1080p"
                elif "720p" in text: icon = "🎥 720p"
                elif "480p" in text: icon = "📱 480p"
                
                # Clean Label
                label = text.replace("Download", "").replace("Links", "").replace("Link", "").strip()
                if len(label) > 30: label = label[:30] + ".."
                final_label = f"{icon} {label}"
                
                # Deduplicate
                if not any(o['url'] == href for o in options):
                    options.append({"label": final_label, "url": href})
                    
        return options
    except Exception as e:
        logger.error(f"Extraction Error: {e}")
        return []

# --- 3. AUTO-BYPASS ENGINE (The "Smart" Part) ---
def bypass_mediator(url):
    """
    Attempts to traverse the Timer/Verification pages automatically.
    """
    logger.info(f"🤖 Bypassing: {url}")
    
    session = requests.Session()
    session.headers.update(get_headers(TARGET_DOMAIN))
    session.cookies.update(get_cookies_dict())
    
    try:
        # Step 1: Visit the Link
        resp = session.get(url, allow_redirects=True, timeout=15)
        final_url = resp.url
        html = resp.text
        
        # CHECK 1: Is it already Telegram?
        if "t.me" in final_url:
            return {"type": "telegram", "url": final_url}
            
        soup = BeautifulSoup(html, 'lxml')
        
        # CHECK 2: Look for "HubCloud" / "Drive" Links hidden in the page
        # Often the verification page has the real link hidden in variables or forms
        
        # Regex to find links starting with common file hosts
        link_regex = re.compile(r'https?://(hubcloud|hubdrive|drive|gdfluen|file|gdtot)[^\s"\']+')
        found_links = link_regex.findall(html)
        
        if found_links:
            # Found a deeper link!
            deep_link = found_links[0]
            if "t.me" in deep_link:
                return {"type": "telegram", "url": deep_link}
            return {"type": "link", "url": deep_link}

        # CHECK 3: Try to find the "Form" that submits after timer
        # Many sites use a POST request to validate
        forms = soup.find_all('form')
        for form in forms:
            action = form.get('action')
            if action and ("verify" in action or "download" in action):
                # This is risky without browser, but we can try to return this URL
                # often clicking this form gives the link
                return {"type": "link", "url": url} # Return original if complex

        # If we can't bypass programmatically (due to Captcha), 
        # we return the current URL but cleaned up.
        return {"type": "link", "url": final_url}

    except Exception as e:
        return {"type": "error", "error": str(e)}

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    txt = (
        f"👋 **Ultra-Smart Bot Ready!**\n"
        f"🌐 Site: `{TARGET_DOMAIN}`\n\n"
        "🧠 **Capabilities:**\n"
        "1. **Smart Search:** No garbage results.\n"
        "2. **Auto-Filter:** Only extracts Movie Links.\n"
        "3. **Bypass:** Attempts to find Telegram/Final links."
    )
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ Admin: `/add`, `/remove`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Pushpa`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🧠 **AI Searching:** `{query}`...\n(Filtering garbage...)")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_smart, query)
    
    if not results:
        await update.message.reply_text("❌ No accurate movies found.\n(Try exact spelling)")
        return
    
    context.user_data['search_res'] = results
    
    keyboard = []
    for idx, movie in enumerate(results):
        keyboard.append([InlineKeyboardButton(f"🎬 {movie['title']}", callback_data=f"s_{idx}")])
        
    await update.message.reply_text(f"✅ Found {len(results)} Matches:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # 1. MOVIE SELECTION (Clean Extraction)
    if data.startswith("s_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): return
            
        movie = results[idx]
        await query.edit_message_text(f"📂 **Scanning Page...**\nMovie: `{movie['title']}`\n\n(Extracting ONLY quality links...)")
        
        loop = asyncio.get_running_loop()
        quality_options = await loop.run_in_executor(executor, extract_quality_smart, movie['url'])
        
        if not quality_options:
            await query.edit_message_text("❌ No valid download links found inside content area.")
            return
        
        context.user_data['quality_opts'] = quality_options
        
        keyboard = []
        for i, opt in enumerate(quality_options):
            keyboard.append([InlineKeyboardButton(opt['label'], callback_data=f"l_{i}")])
            
        await query.edit_message_text(f"🎬 **{movie['title']}**\n👇 Select Quality:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # 2. LINK BYPASS (The "Smart" Click)
    if data.startswith("l_"):
        idx = int(data.split("_")[1])
        opts = context.user_data.get('quality_opts', [])
        if idx >= len(opts): return
        
        target = opts[idx]
        await query.edit_message_text(f"🤖 **Bot is Bypassing...**\nTarget: {target['label']}\n\n(Handling Timers & Redirects...)")
        
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, bypass_mediator, target['url'])
        
        if result.get("type") == "error":
            await query.edit_message_text("❌ Bypass Failed. Manual Link below.")
            final_url = target['url']
        else:
            final_url = result.get("url")
        
        # Result Display
        if "t.me" in final_url:
            msg = f"✈️ **Telegram Link Extracted!**\n\n🔗 [Click to Join/Download]({final_url})"
            kb = [[InlineKeyboardButton("✈️ Open Telegram", url=final_url)]]
        else:
            msg = f"✅ **Final Link Extracted!**\n\nOriginal: {target['label']}\n🔗 **Bypassed Link:** `{final_url}`\n\n(Click below to Download)"
            kb = [[InlineKeyboardButton("⬇️ Download / Watch", url=final_url)]]
            
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_qual")])
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return

    # 3. BACK
    if data == "back_to_qual":
        opts = context.user_data.get('quality_opts', [])
        keyboard = []
        for i, opt in enumerate(opts):
            keyboard.append([InlineKeyboardButton(opt['label'], callback_data=f"l_{i}")])
        await query.edit_message_text("👇 Select Quality:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- ADMIN ---
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

# --- STARTUP ---
async def main():
    req = HTTPXRequest(connection_pool_size=10, read_timeout=60, write_timeout=60, connect_timeout=60)
    app_bot = Application.builder().token(BOT_TOKEN).request(req).build()

    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("search", search_command))
    app_bot.add_handler(CommandHandler(["add", "remove"], admin_ops))
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
