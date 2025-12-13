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
# 🛠️ USER CONFIGURATION (YAHAN SETTINGS CHANGE KAREIN)
# ==============================================================================
# Apne website ke structure ke hisab se ye selectors badlein.
# Agar samajh na aaye, toh niche instructions padhein.

SITE_CONFIG = {
    # 1. Search Result me Movie ka Dabba (Container) kaisa dikhta hai?
    # Example: 'div.movie-card', 'article.post', 'div.result-item'
    "SEARCH_ITEM_SELECTOR": "article, div.post, div.item", 

    # 2. Us Dabbe ke andar Title kahan hai?
    "SEARCH_TITLE_SELECTOR": "h1, h2, h3, .entry-title, .title",

    # 3. Movie Page par Quality Links (480p, 720p) kahan hain?
    # Example: 'a.download-btn', 'a.button'
    "QUALITY_BUTTON_SELECTOR": "a"
}

# ==============================================================================
# 1. DUMMY WEB SERVER FOR RENDER
# ==============================================================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Custom Scraper!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def start_keep_alive():
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

if not BOT_TOKEN or not ADMIN_ID:
    logger.warning("⚠️ Environment variables missing!")

try:
    ADMIN_ID = int(ADMIN_ID)
except (ValueError, TypeError):
    pass

SELECT_MOVIE, SELECT_QUALITY = range(2)

# ==============================================================================
# 3. ADVANCED SCRAPER ENGINE (CUSTOMIZABLE)
# ==============================================================================
class CustomScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

    def _safe_get(self, url):
        try:
            # Automatic generic search query parameter handling
            if "?" not in url and "s=" not in url:
                pass 
            
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            
            if response.status_code in [403, 503]:
                return {"error": "Access Denied (Cloudflare/Captcha)", "url": url}
            
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}
            
            return {"soup": BeautifulSoup(response.text, 'html.parser'), "url": response.url}
        except Exception as e:
            return {"error": str(e)}

    def search_movies(self, query):
        if not ALLOWED_DOMAINS or not ALLOWED_DOMAINS[0]:
            return {"error": "ALLOWED_DOMAINS not set."}

        base_url = ALLOWED_DOMAINS[0].strip().rstrip('/')
        # Adjust search URL pattern if your site uses something other than /?s=
        search_url = f"{base_url}/?s={query}"
        
        result = self._safe_get(search_url)
        if "error" in result: return result

        soup = result["soup"]
        movies = []
        
        # --- NEW LOGIC: CSS SELECTORS ---
        # Find all containers that look like a movie post
        items = soup.select(SITE_CONFIG["SEARCH_ITEM_SELECTOR"])
        
        for item in items[:15]:
            # Try to find the link (a tag) inside the container
            link_tag = item.find('a', href=True)
            
            # Try to find the title inside the container
            title_tag = item.select_one(SITE_CONFIG["SEARCH_TITLE_SELECTOR"])
            
            if link_tag:
                url = link_tag['href']
                # If title tag exists, use it. Otherwise try link text.
                title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
                
                # Check if it's actually a movie link
                if title and len(title) > 2:
                    movies.append({"title": title, "url": url})
        
        # Deduplicate results
        seen = set()
        unique_movies = []
        for m in movies:
            if m['url'] not in seen:
                unique_movies.append(m)
                seen.add(m['url'])

        return unique_movies[:10]

    def get_qualities(self, movie_url):
        result = self._safe_get(movie_url)
        if "error" in result: return result

        soup = result["soup"]
        qualities = []
        # Keywords to identify quality buttons
        target_keywords = ["480p", "720p", "1080p", "2160p", "4k", "hevc", "download", "watch"]
        
        # Search for all links that might be download buttons
        links = soup.select(SITE_CONFIG["QUALITY_BUTTON_SELECTOR"])
        
        for a in links:
            if not a.has_attr('href'): continue
            
            text = a.get_text(strip=True).lower()
            href = a['href']
            
            # Filter: Check if text contains quality info or 'download'
            if any(k in text for k in target_keywords):
                # Clean up the label
                label = a.get_text(strip=True)[:40] 
                qualities.append({"quality": label, "url": href})

        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        result = self._safe_get(quality_url)
        if "error" in result: return result

        soup = result["soup"]
        tg_pattern = re.compile(r'(https?://t\.me/[a-zA-Z0-9_]+(/[0-9]+)?)')
        found_links = []
        
        # Scan all links
        for a in soup.find_all('a', href=True):
            if "t.me" in a['href']: found_links.append(a['href'])
        
        # Scan plain text
        text_links = tg_pattern.findall(str(soup))
        for link_tuple in text_links:
            found_links.append(link_tuple[0])

        if not found_links:
            return {"error": "No Telegram link found."}
        
        return {"tg_link": found_links[0]}

scraper = CustomScraper()

# ==============================================================================
# 4. BOT HANDLERS
# ==============================================================================
async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("👋 <b>Bot Ready!</b>\nSearching your custom site.", parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Searching: <b>{query}</b>...", parse_mode='HTML')
    
    results = scraper.search_movies(query)
    
    if isinstance(results, dict) and "error" in results:
        await update.message.reply_text(f"⚠️ Error: {results['error']}")
        return ConversationHandler.END
        
    if not results:
        await update.message.reply_text("❌ No movies found. Try checking SITE_CONFIG selectors.")
        return ConversationHandler.END

    keyboard = []
    for idx, movie in enumerate(results):
        context.user_data[f"movie_{idx}"] = movie['url']
        keyboard.append([InlineKeyboardButton(movie['title'], callback_data=f"mov_{idx}")])

    await update.message.reply_text(f"✅ Found {len(results)} matches:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    
    await query.edit_message_text(f"⏳ Extracting download links...")
    qualities = scraper.get_qualities(movie_url)

    if isinstance(qualities, dict) or not qualities:
        await query.edit_message_text("❌ No quality links found.")
        return ConversationHandler.END

    keyboard = []
    for idx, q in enumerate(qualities):
        context.user_data[f"qual_{idx}"] = q['url']
        keyboard.append([InlineKeyboardButton(q['quality'], callback_data=f"qual_{idx}")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text("Select Quality:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_QUALITY

async def quality_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        await query.edit_message_text("🚫 Cancelled.")
        return ConversationHandler.END
        
    idx = int(query.data.split("_")[1])
    qual_url = context.user_data.get(f"qual_{idx}")
    await query.edit_message_text("🕵️‍♂️ Finding Telegram Link...")
    
    result = scraper.extract_telegram_link(qual_url)
    if "error" in result:
        await query.edit_message_text(f"❌ {result['error']}")
        return ConversationHandler.END
        
    await query.edit_message_text(
        "✅ <b>Link Found!</b>", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Open Link", url=result["tg_link"])]]),
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Reset.")
    return ConversationHandler.END

def main():
    start_keep_alive()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler)],
        states={
            SELECT_MOVIE: [CallbackQueryHandler(movie_selection_handler, pattern="^mov_")],
            SELECT_QUALITY: [CallbackQueryHandler(quality_selection_handler, pattern="^qual_|cancel")],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
    
