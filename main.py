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

# --- SCRAPER ENGINE ---

def get_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": TARGET_DOMAIN,
    }

def search_website(query):
    search_url = f"{TARGET_DOMAIN}/?s={query.replace(' ', '+')}"
    try:
        resp = requests.get(search_url, headers=get_headers(), timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        results = []
        
        candidates = soup.find_all('article')
        if not candidates:
            candidates = soup.select('div.post, div.result-item, div.latestPost')
            
        for item in candidates:
            a_tag = item.find('a')
            if a_tag and a_tag.get('href'):
                url = a_tag['href']
                title = a_tag.get('title') or a_tag.text.strip()
                if title and url and "http" in url:
                    results.append({"title": title, "url": url})
                    if len(results) >= 8: break

        return results
    except Exception as e:
        logger.error(f"Search Failed: {e}")
        return []

def extract_links(url):
    try:
        resp = requests.get(url, headers=get_headers(), timeout=15)
        soup = BeautifulSoup(resp.text, 'lxml')
        links = []
        keywords = ['480p', '720p', '1080p', 'Download']
        
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            href = a['href']
            if any(k.lower() in text.lower() for k in keywords) and "http" in href:
                clean_text = text.replace('⚡', '').replace('Download Links', '').strip()
                if not any(l['url'] == href for l in links):
                    links.append({"quality": clean_text, "url": href})
        return links
    except Exception as e:
        logger.error(f"Extraction Failed: {e}")
        return []

# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    txt = (
        f"👋 **Bot Ready!**\n"
        f"🌐 Target: `{TARGET_DOMAIN}`\n\n"
        "🔎 `/search MovieName`\n"
        "🔗 Send Link to Download"
    )
    if update.effective_user.id == OWNER_ID: txt += "\n\n👮‍♂️ Admin: `/add`, `/remove`"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

# --- DEFINING ADMIN OPS (The Missing Function) ---
async def admin_ops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        cmd = update.message.text.split()[0]
        if not context.args:
            await update.message.reply_text("Usage: `/add 123456`")
            return
            
        target = int(context.args[0])
        if cmd == "/add": 
            AUTHORIZED_USERS.add(target)
            msg = "✅ User Added"
        elif cmd == "/remove": 
            if target != OWNER_ID: 
                AUTHORIZED_USERS.discard(target)
                msg = "🗑️ User Removed"
            else:
                msg = "❌ Cannot remove Owner"
                
        save_users(AUTHORIZED_USERS)
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search Iron Man`")
        return
    
    query = " ".join(context.args)
    await update.message.reply_text(f"🔍 Searching: `{query}`...")
    
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(executor, search_website, query)
    
    if not results:
        await update.message.reply_text("❌ No results found.")
        return
    
    context.user_data['search_res'] = results
    keyboard = []
    for idx, movie in enumerate(results):
        keyboard.append([InlineKeyboardButton(f"🎬 {movie['title'][:30]}...", callback_data=f"sel_{idx}")])
        
    await update.message.reply_text(f"✅ Found {len(results)} movies:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("sel_"):
        idx = int(data.split("_")[1])
        results = context.user_data.get('search_res', [])
        if idx >= len(results): return
            
        movie = results[idx]
        await query.edit_message_text(f"🔄 Fetching links for: {movie['title']}...")
        
        loop = asyncio.get_running_loop()
        links = await loop.run_in_executor(executor, extract_links, movie['url'])
        
        if not links:
            await query.edit_message_text("❌ No links found.")
            return
            
        keyboard = []
        for link in links:
            keyboard.append([InlineKeyboardButton(f"📥 {link['quality']}", url=link['url'])])
            
        await query.edit_message_text(f"🎬 **{movie['title']}**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update): return
    url = update.message.text.strip()
    if "http" not in url: return
    
    await update.message.reply_text("⚡ Downloading...")
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
    # admin_ops is now properly defined above
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
            
