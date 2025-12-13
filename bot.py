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
# 🛠️ USER CONFIGURATION (REVERTED TO BROAD SELECTORS)
# ==============================================================================
SITE_CONFIG = {
    # Wapas broad selectors taaki search result miss na ho
    "SEARCH_ITEM_SELECTOR": "article, div.post, div.latestPost article, ul.recent-movies li, li.post-item, div.result-item", 
    
    # Title extraction
    "SEARCH_TITLE_SELECTOR": "h2.title, h2.entry-title, p.title, .caption, a",
    
    # Buttons
    "QUALITY_BUTTON_SELECTOR": "a.buttn, a.btn, a.download-link, a.button"
}

# ==============================================================================
# 1. CONFIGURATION & LOGGING
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
    logger.critical("⚠️ BOT_TOKEN or WEBHOOK_URL missing!")

try:
    ADMIN_ID = int(ADMIN_ID)
except (ValueError, TypeError):
    pass

SELECT_MOVIE, SELECT_QUALITY = range(2)

# ==============================================================================
# 2. ROBUST SCRAPER ENGINE
# ==============================================================================
class SmartScraper:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )

    def _safe_get(self, url):
        try:
            response = self.scraper.get(url, timeout=REQUEST_TIMEOUT)
            # 404 ya errors ko ignore karke aage badho agar content hai
            if response.status_code not in [200, 404]: 
                return {"error": f"HTTP {response.status_code}"}
            
            soup = BeautifulSoup(response.text, 'html.parser')
            title = soup.title.string if soup.title else ""
            
            # Cloudflare Check
            if "Just a moment" in title or "Attention Required" in title:
                return {"error": "Blocked by Cloudflare", "url": url}
                
            return {"soup": soup, "url": response.url}
        except Exception as e:
            return {"error": str(e)}

    def search_movies(self, query):
        if not ALLOWED_DOMAINS or not ALLOWED_DOMAINS[0]:
            return {"error": "ALLOWED_DOMAINS missing."}

        base_url = ALLOWED_DOMAINS[0].strip().rstrip('/')
        search_url = f"{base_url}/?s={query}"
        
        result = self._safe_get(search_url)
        if "error" in result: return result

        soup = result["soup"]
        movies = []
        
        # 1. Broad Selection (Sab kuch utha lo)
        items = soup.select(SITE_CONFIG["SEARCH_ITEM_SELECTOR"])
        
        # Debugging: Log kitne items mile
        print(f"DEBUG: Scraper found {len(items)} raw items on page.")

        for item in items[:20]: # Check top 20 items
            link_tag = item.find('a', href=True)
            if not link_tag: continue

            # Title Dhoondo
            title = ""
            title_tag = item.select_one(SITE_CONFIG["SEARCH_TITLE_SELECTOR"])
            
            if title_tag:
                title = title_tag.get_text(strip=True)
            else:
                title = link_tag.get_text(strip=True)
                if not title:
                    img = item.find('img')
                    if img: title = img.get('alt')

            url = link_tag['href']
            
            # 2. Python-Side Filtering (Ye important hai accuracy ke liye)
            if title and len(title) > 2 and url:
                # Query ke words title me match karke dekho
                # Example: Query "Avengers" -> Title "Avengers Endgame" (Match!)
                # Example: Query "Avengers" -> Title "Download App" (No Match!)
                query_words = query.lower().split()
                if any(word in title.lower() for word in query_words):
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
        
        links = soup.select(SITE_CONFIG["QUALITY_BUTTON_SELECTOR"])
        
        # Fallback to scanning all links if buttons missing
        if not links:
            content_div = soup.select_one("div.entry-content")
            links = content_div.find_all('a', href=True) if content_div else soup.find_all('a', href=True)

        for a in links:
            if not a.has_attr('href'): continue
            text = a.get_text(strip=True).lower()
            href = a['href']
            
            # Junk Filter
            if "category" in href or "tag" in href or href == "/" or "facebook" in href: continue
            
            if any(k in text for k in target_keywords):
                label = a.get_text(strip=True)[:50]
                qualities.append({"quality": label, "url": href})

        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        try:
            response = self.scraper.get(quality_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            soup = BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            return {"error": str(e)}

        deep_link_pattern = re.compile(r'(https?://t\.me/[a-zA-Z0-9_]+/[0-9]+)')
        
        all_hrefs = [a['href'] for a in soup.find_all('a', href=True)]
        
        # 1. Deep Link Priority
        for link in all_hrefs:
            if deep_link_pattern.search(link):
                return {"tg_link": link}
        
        # 2. Deep Link in Text
        text_deep = deep_link_pattern.findall(str(soup))
        if text_deep:
            return {"tg_link": text_deep[0]}

        # 3. Channel Link Fallback (Exclude JoinChat/Official wrappers if possible)
        for link in all_hrefs:
            if "t.me/" in link and "joinchat" not in link:
                 return {"tg_link": link}

        return {"error": "No valid Telegram file link found."}

scraper = SmartScraper()

# ==============================================================================
# 3. BOT HANDLERS
# ==============================================================================
async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Access denied.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("👋 <b>Bot Ready!</b>\nSelectors Reset & Logic Improved.", parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Searching: {query}...")
    
    results = scraper.search_movies(query)
    
    if isinstance(results, dict) and "error" in results:
        await update.message.reply_text(f"⚠️ Error: {results['error']}")
        return ConversationHandler.END
        
    if not results:
        # Fallback message
        await update.message.reply_text("❌ No relevant movies found.\nTry a simpler keyword.")
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
    await query.edit_message_text(f"⏳ Scanning Qualities...")
    qualities = scraper.get_qualities(movie_url)

    if not qualities or (isinstance(qualities, dict) and "error" in qualities):
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
    await query.edit_message_text("🕵️‍♂️ Fetching File Link...")
    result = scraper.extract_telegram_link(qual_url)
    if "error" in result:
        await query.edit_message_text(f"❌ {result['error']}")
        return ConversationHandler.END
    
    tg_link = result["tg_link"]
    await query.edit_message_text(
        f"✅ <b>Link Found!</b>", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Open Link", url=tg_link)]]),
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
