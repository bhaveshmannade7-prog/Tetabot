import os
import re
import logging
import cloudscraper
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
# 🛠️ USER CONFIGURATION (BROADEST SELECTORS)
# ==============================================================================
SITE_CONFIG = {
    # Ab hum specific div nahi dhoondenge, seedha Links aur Titles uthayenge
    "SEARCH_ITEM_SELECTOR": "article, div.post, li, div.result-item, div.latestPost, div.item", 
    "QUALITY_BUTTON_SELECTOR": "a" # Check ALL links for quality
}

# ==============================================================================
# 1. CONFIGURATION
# ==============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
PORT = int(os.environ.get("PORT", "10000"))

if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("⚠️ CONFIG MISSING")

try:
    ADMIN_ID = int(ADMIN_ID)
except (ValueError, TypeError):
    pass

SELECT_MOVIE, SELECT_QUALITY = range(2)

# ==============================================================================
# 2. SCRAPER ENGINE (UNFILTERED & AGGRESSIVE)
# ==============================================================================
class AggressiveScraper:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )

    def _safe_get(self, url):
        try:
            response = self.scraper.get(url, timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(response.text, 'html.parser')
            # Check Cloudflare
            if "Just a moment" in str(soup.title):
                return {"error": "Cloudflare Blocked", "url": url}
            return {"soup": soup, "url": response.url}
        except Exception as e:
            return {"error": str(e)}

    def search_movies(self, query):
        if not ALLOWED_DOMAINS: return {"error": "Domain missing"}
        
        base_url = ALLOWED_DOMAINS[0].strip().rstrip('/')
        search_url = f"{base_url}/?s={query}"
        
        result = self._safe_get(search_url)
        if "error" in result: return result

        soup = result["soup"]
        movies = []
        
        # METHOD 1: Look for any link that looks like a movie post
        # Hum page ke saare links scan karenge jo 'title' attribute rakhte hain
        all_links = soup.find_all('a', href=True)
        
        print(f"DEBUG: Found {len(all_links)} total links on page.")

        for a in all_links:
            title = ""
            url = a['href']
            
            # Title extraction attempts
            if a.get('title'):
                title = a['title']
            elif a.find('img') and a.find('img').get('alt'):
                title = a.find('img')['alt']
            else:
                title = a.get_text(strip=True)

            # CLEANING:
            # 1. Title hona chahiye
            # 2. URL valid hona chahiye
            # 3. Title me search query ka koi bhi ek shabd hona chahiye (Loose Check)
            if title and len(title) > 3 and "http" in url:
                # Exclude Garbage
                if "comment" in url or "reply" in url or "login" in url or "author" in url:
                    continue
                
                # Check if ANY word from query is in title (Case Insensitive)
                # Agar 'Avengers' search kiya aur title 'The Avengers' hai -> PASS
                query_words = query.lower().split()
                if any(w in title.lower() for w in query_words):
                    movies.append({"title": title, "url": url})
        
        # Deduplicate
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
        target_keywords = ["480p", "720p", "1080p", "2160p", "4k", "hevc", "download", "link", "watch"]
        
        # Scan ALL links on the page
        links = soup.find_all('a', href=True)

        for a in links:
            text = a.get_text(strip=True).lower()
            href = a['href']
            
            # Extra text check (parent wrapper text)
            parent_text = a.parent.get_text(strip=True).lower() if a.parent else ""
            
            if any(k in text for k in target_keywords) or any(k in parent_text for k in target_keywords):
                # Clean Label
                label = a.get_text(strip=True)
                if not label: label = "Download Link"
                label = label[:40] # Shorten
                
                # Avoid garbage links
                if "facebook" in href or "whatsapp" in href or "twitter" in href: continue
                
                qualities.append({"quality": label, "url": href})

        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        try:
            response = self.scraper.get(quality_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            return {"error": str(e)}

        # REGEX PATTERNS
        # 1. Strict File Link (t.me/channel/123)
        file_pattern = re.compile(r't\.me/[a-zA-Z0-9_]+/\d+')
        # 2. General Link (t.me/channel)
        general_pattern = re.compile(r't\.me/[a-zA-Z0-9_]+')

        # SEARCH STRATEGY 1: Look in HREF attributes
        all_a_tags = soup.find_all('a', href=True)
        for a in all_a_tags:
            if "t.me" in a['href']:
                if file_pattern.search(a['href']):
                    return {"tg_link": a['href']} # Gold Mine!

        # SEARCH STRATEGY 2: Look in Raw HTML (Scripts, OnClick, Hidden text)
        # This fixes "Link not found" if link is hidden in JS
        found_files = file_pattern.findall(html_content)
        if found_files:
            link = found_files[0]
            if not link.startswith("http"): link = "https://" + link
            return {"tg_link": link}

        # SEARCH STRATEGY 3: Fallback to General Link (e.g. Channel)
        # Avoid "Join Channel" header links if possible
        for a in all_a_tags:
            if "t.me" in a['href'] and "join" not in a.text.lower():
                return {"tg_link": a['href']}

        # Final Fallback: Any t.me link
        found_generals = general_pattern.findall(html_content)
        if found_generals:
            link = found_generals[0]
            if not link.startswith("http"): link = "https://" + link
            return {"tg_link": link}

        return {"error": "No Telegram link found."}

scraper = AggressiveScraper()

# ==============================================================================
# 3. BOT HANDLERS
# ==============================================================================
async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("👋 <b>Aggressive Mode On</b>\nFilters removed. Searching deep.", parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Deep Searching: {query}...")
    
    results = scraper.search_movies(query)
    
    if isinstance(results, dict) and "error" in results:
        await update.message.reply_text(f"⚠️ Error: {results['error']}")
        return ConversationHandler.END
        
    if not results:
        await update.message.reply_text("❌ No movies found even with aggressive search.")
        return ConversationHandler.END

    keyboard = []
    for idx, movie in enumerate(results):
        context.user_data[f"movie_{idx}"] = movie['url']
        keyboard.append([InlineKeyboardButton(movie['title'], callback_data=f"mov_{idx}")])
    await update.message.reply_text(f"✅ Found {len(results)} raw results:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    await query.edit_message_text(f"⏳ Scanning Links...")
    qualities = scraper.get_qualities(movie_url)

    if not qualities:
        await query.edit_message_text("❌ No download links found.")
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
    await query.edit_message_text("🕵️‍♂️ Brute-Forcing Link...")
    result = scraper.extract_telegram_link(qual_url)
    if "error" in result:
        await query.edit_message_text(f"❌ {result['error']}")
        return ConversationHandler.END
    
    await query.edit_message_text(
        f"✅ <b>Link Found!</b>", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Open Link", url=result["tg_link"])]]),
        parse_mode="HTML"
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Reset.")
    return ConversationHandler.END

def main():
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
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
                
