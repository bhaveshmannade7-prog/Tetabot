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
# 🛠️ USER CONFIGURATION (STRICTER & SMARTER)
# ==============================================================================
SITE_CONFIG = {
    # Search ke liye broad selectors taaki koi movie miss na ho
    "SEARCH_ITEM_SELECTOR": "article, div.post, div.latestPost article, ul.recent-movies li, li.post-item, div.result-item",
    "SEARCH_TITLE_SELECTOR": "h2.title, h2.entry-title, p.title, .caption, a",
    
    # Quality Buttons ke liye specific classes jo aapki theme use karti hai
    "QUALITY_BUTTON_SELECTOR": "a.buttn, a.btn, a.download-link, a.button, div.linkbutton a"
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
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "45")) # Timeout badhaya
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
# 2. SCRAPER ENGINE (AGGRESSIVE LINK EXTRACTION)
# ==============================================================================
class SmartScraper:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )

    def _safe_get(self, url):
        try:
            response = self.scraper.get(url, timeout=REQUEST_TIMEOUT)
            # Cloudflare check
            if "Just a moment" in response.text or "Attention Required" in response.text:
                 return {"error": "Cloudflare Blocked", "url": url}
            return {"soup": BeautifulSoup(response.text, 'html.parser'), "url": response.url}
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
        
        items = soup.select(SITE_CONFIG["SEARCH_ITEM_SELECTOR"])
        for item in items[:25]:
            link_tag = item.find('a', href=True)
            if not link_tag: continue
            
            title_tag = item.select_one(SITE_CONFIG["SEARCH_TITLE_SELECTOR"])
            title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
            if not title: img = item.find('img'); title = img.get('alt') if img else ""

            url = link_tag['href']
            if title and len(title) > 2 and url and "http" in url:
                # Basic filter to ensure relevance
                query_words = query.lower().split()
                if any(w in title.lower() for w in query_words):
                    movies.append({"title": title, "url": url})
        
        seen = set()
        unique_movies = []
        for m in movies:
            if m['url'] not in seen: unique_movies.append(m); seen.add(m['url'])
        return unique_movies[:10]

    def get_qualities(self, movie_url):
        result = self._safe_get(movie_url)
        if "error" in result: return result
        soup = result["soup"]
        qualities = []
        
        # STRICT FILTER KEYWORDS
        target_keywords = ["480p", "720p", "1080p", "2160p", "4k", "hevc", "10bit", "hdr", "60fps"]
        
        # 1. Try finding styled buttons first
        links = soup.select(SITE_CONFIG["QUALITY_BUTTON_SELECTOR"])
        
        # 2. Fallback: Search inside main content area
        if not links:
            content_div = soup.select_one("div.entry-content, div.post-content")
            links = content_div.find_all('a', href=True) if content_div else []

        for a in links:
            if not a.has_attr('href'): continue
            text = a.get_text(strip=True)
            text_lower = text.lower()
            href = a['href']
            
            # Junk Filter
            if any(x in href for x in ["category", "tag", "#", "facebook", "twitter"]): continue

            # STRICT CHECK: Button text MUST contain a resolution keyword
            if any(k in text_lower for k in target_keywords):
                label = text[:50] # Keep label short
                qualities.append({"quality": label, "url": href})

        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        try:
            # Follow redirects, important for final pages
            response = self.scraper.get(quality_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            html_content = response.text # Get Raw HTML
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            return {"error": f"Network Error: {str(e)}"}

        # Regex for Deep Links (t.me/channel/123) - Highest Priority
        file_pattern = re.compile(r't\.me/[a-zA-Z0-9_]+/\d+')

        # STRATEGY 1: Check standard 'href' attributes
        for a in soup.find_all('a', href=True):
            if file_pattern.search(a['href']):
                return {"tg_link": a['href']}

        # STRATEGY 2: BRUTE FORCE - Search in raw HTML (Scripts, hidden inputs)
        # This finds links hidden by JavaScript
        found_files = file_pattern.findall(html_content)
        if found_files:
            link = found_files[0]
            if not link.startswith("http"): link = "https://" + link
            return {"tg_link": link}

        # STRATEGY 3: Fallback to any Telegram link that isn't "Join Channel"
        for a in soup.find_all('a', href=True):
            if "t.me/" in a['href'] and "join" not in a.get_text(strip=True).lower():
                 return {"tg_link": a['href']}

        return {"error": "No valid Telegram file link could be extracted from the final page."}

scraper = SmartScraper()

# ==============================================================================
# 3. BOT HANDLERS
# ==============================================================================
async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("👋 <b>Bot Updated!</b>\n✅ Strict Quality Filter\n✅ Aggressive Link Extraction", parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Searching: {query}...")
    
    results = scraper.search_movies(query)
    if isinstance(results, dict) and "error" in results:
        await update.message.reply_text(f"⚠️ Error: {results['error']}")
        return ConversationHandler.END
    if not results:
        await update.message.reply_text("❌ No relevant movies found.")
        return ConversationHandler.END

    keyboard = []
    for idx, movie in enumerate(results):
        context.user_data[f"movie_{idx}"] = movie['url']
        keyboard.append([InlineKeyboardButton(movie['title'], callback_data=f"mov_{idx}")])
    await update.message.reply_text(f"✅ Found {len(results)} movies:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    await query.edit_message_text(f"⏳ Filtering & Fetching Qualities...")
    qualities = scraper.get_qualities(movie_url)

    if not qualities:
        await query.edit_message_text("❌ No valid quality links (480p/720p/1080p) found.")
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
    
    await query.edit_message_text("🕵️‍♂️ Brute-Forcing Final Link (This may take a moment)...")
    result = scraper.extract_telegram_link(qual_url)
    
    if "error" in result:
        await query.edit_message_text(f"❌ {result['error']}")
        return ConversationHandler.END
    
    await query.edit_message_text(
        f"✅ <b>Link Successfully Extracted!</b>", 
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
    
    print(f"🚀 Starting Webhook on Port {PORT}")
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
            
