import os
import re
import logging
import requests
import threading
from flask import Flask
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

# ==============================================================================
# 1. DUMMY WEB SERVER FOR RENDER (CRITICAL FIX)
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running perfectly! Port is bound."

def run_web_server():
    # Render assigns a PORT via environment variable. Default to 10000 if missing.
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def start_keep_alive():
    """Starts the Flask server in a separate thread to keep Render happy."""
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==============================================================================
# 2. CONFIGURATION & LOGGING
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

# Validation (Soft fail to allow local debugging if needed)
if not BOT_TOKEN or not ADMIN_ID:
    logger.warning("Environment variables are missing! Bot might not start.")

try:
    ADMIN_ID = int(ADMIN_ID)
except (ValueError, TypeError):
    pass

# Conversation States
SELECT_MOVIE, SELECT_QUALITY = range(2)

# ==============================================================================
# 3. SCRAPER ENGINE (SAFE & ROBUST)
# ==============================================================================
class PublicDomainScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

    def _safe_get(self, url):
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            
            # Basic Anti-Bot Detection Check
            if response.status_code in [403, 503] or "captcha" in response.text.lower():
                return {"error": "captcha", "url": url}
            
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}
            
            return {"soup": BeautifulSoup(response.text, 'html.parser'), "url": response.url}
        except Exception as e:
            return {"error": str(e)}

    def search_movies(self, query):
        if not ALLOWED_DOMAINS or not ALLOWED_DOMAINS[0]:
            return {"error": "No ALLOWED_DOMAINS configured in Env Vars."}

        base_url = ALLOWED_DOMAINS[0].strip()
        search_url = f"{base_url}/?s={query}"
        
        result = self._safe_get(search_url)
        if "error" in result: return result

        soup = result["soup"]
        movies = []
        
        # Generic parsing logic for WP/Blog style sites
        for item in soup.find_all(['h2', 'h3'], limit=15): 
            link_tag = item.find('a')
            if link_tag and link_tag.get('href'):
                title = link_tag.get_text(strip=True)
                # Fuzzy match check
                if query.lower() in title.lower(): 
                    movies.append({"title": title, "url": link_tag['href']})
        
        return movies[:10]

    def get_qualities(self, movie_url):
        result = self._safe_get(movie_url)
        if "error" in result: return result

        soup = result["soup"]
        qualities = []
        target_keywords = ["480p", "720p", "1080p", "HEVC", "x265", "x264", "HQ"]
        
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            if any(k in text for k in target_keywords):
                # Ensure we capture a clean label
                label = text[:30] if len(text) > 30 else text
                qualities.append({"quality": label, "url": a['href']})
        
        # Deduplicate
        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        result = self._safe_get(quality_url)
        if "error" in result: return result

        soup = result["soup"]
        tg_pattern = re.compile(r'(https?://t\.me/[a-zA-Z0-9_]+(/[0-9]+)?)')
        found_links = []
        
        # Method 1: Check hrefs
        for a in soup.find_all('a', href=True):
            if "t.me" in a['href']: found_links.append(a['href'])
        
        # Method 2: Check raw text
        text_links = tg_pattern.findall(str(soup))
        for link_tuple in text_links:
            found_links.append(link_tuple[0])

        if not found_links:
            return {"error": "No Telegram link found on the final page."}
        
        return {"tg_link": found_links[0]}

scraper = PublicDomainScraper()

# ==============================================================================
# 4. TELEGRAM HANDLERS
# ==============================================================================
async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only gatekeeper."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied. Private Admin Bot.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    welcome_msg = (
        "👋 <b>Welcome Admin!</b>\n\n"
        "Send a movie name to search public domain databases.\n"
        "<i>Server status: Online on Render</i>"
    )
    await update.message.reply_text(welcome_msg, parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return

    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Searching for: <b>{query}</b>...", parse_mode='HTML')
    
    results = scraper.search_movies(query)

    if isinstance(results, dict) and "error" in results:
        err_msg = results['error']
        if err_msg == "captcha":
            await update.message.reply_text(f"⚠️ CAPTCHA Detected!\nOpen manually: {results['url']}")
        else:
            await update.message.reply_text(f"❌ Error: {err_msg}")
        return ConversationHandler.END

    if not results:
        await update.message.reply_text("❌ No movies found.")
        return ConversationHandler.END

    keyboard = []
    for idx, movie in enumerate(results):
        context.user_data[f"movie_{idx}"] = movie['url']
        keyboard.append([InlineKeyboardButton(movie['title'], callback_data=f"mov_{idx}")])

    await update.message.reply_text(
        f"✅ Found {len(results)} movies:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    
    await query.edit_message_text(f"⏳ Scanning qualities...")
    qualities = scraper.get_qualities(movie_url)

    if isinstance(qualities, dict) and "error" in qualities:
        await query.edit_message_text(f"❌ Error: {qualities['error']}")
        return ConversationHandler.END

    if not qualities:
        await query.edit_message_text("❌ No quality links found.")
        return ConversationHandler.END

    keyboard = []
    for idx, q in enumerate(qualities):
        context.user_data[f"qual_{idx}"] = q['url']
        keyboard.append([InlineKeyboardButton(q['quality'], callback_data=f"qual_{idx}")])
    
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text("🎬 Select Quality:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_QUALITY

async def quality_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("🚫 Cancelled.")
        return ConversationHandler.END

    idx = int(query.data.split("_")[1])
    qual_url = context.user_data.get(f"qual_{idx}")

    await query.edit_message_text("🕵️‍♂️ Fetching Telegram link...")
    result = scraper.extract_telegram_link(qual_url)

    if "error" in result:
        await query.edit_message_text(f"❌ Failed: {result['error']}")
        return ConversationHandler.END

    # Success
    keyboard = [[InlineKeyboardButton("📥 Fetch from Telegram", url=result["tg_link"])]]
    await query.edit_message_text(
        "✅ <b>Link Found!</b>\nClick below to open.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Reset.")
    return ConversationHandler.END

async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("✅ Resumed.")

def main():
    # 1. Start Dummy Server in Background
    start_keep_alive()
    
    # 2. Start Bot
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler)],
        states={
            SELECT_MOVIE: [CallbackQueryHandler(movie_selection_handler, pattern="^mov_")],
            SELECT_QUALITY: [CallbackQueryHandler(quality_selection_handler, pattern="^qual_|cancel")],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(conv_handler)
    
    print(f"Bot started. Listening for Admin ID: {ADMIN_ID}")
    
    # Polling runs on the main thread
    application.run_polling()

if __name__ == '__main__':
    main()
