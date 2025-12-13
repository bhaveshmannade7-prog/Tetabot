import os
import re
import logging
import requests
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
# 🛠️ USER CONFIGURATION
# ==============================================================================
SITE_CONFIG = {
    "SEARCH_ITEM_SELECTOR": "ul.recent-movies li, div.latestPost article, article.post, li.post-item, div.post, figure", 
    "SEARCH_TITLE_SELECTOR": "h2.title, h2.entry-title, p.title, .caption, a",
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

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
# NEW: Webhook URL (Render app url)
WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
PORT = int(os.environ.get("PORT", "10000"))

# Validation
if not BOT_TOKEN or not WEBHOOK_URL:
    logger.critical("⚠️ BOT_TOKEN or WEBHOOK_URL is missing!")

try:
    ADMIN_ID = int(ADMIN_ID)
except (ValueError, TypeError):
    pass

SELECT_MOVIE, SELECT_QUALITY = range(2)

# ==============================================================================
# 2. SCRAPER ENGINE (OPTIMIZED)
# ==============================================================================
class CustomScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://google.com"
        })

    def _safe_get(self, url):
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            print(f"DEBUG: Visiting {url} - Status: {response.status_code}")
            
            if response.status_code in [403, 503]:
                return {"error": "Cloudflare/Protection Blocked Request", "url": url}
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}
            return {"soup": BeautifulSoup(response.text, 'html.parser'), "url": response.url}
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
        items = soup.select(SITE_CONFIG["SEARCH_ITEM_SELECTOR"])
        
        for item in items[:15]:
            link_tag = item.find('a', href=True)
            if not link_tag: continue

            title_tag = item.select_one(SITE_CONFIG["SEARCH_TITLE_SELECTOR"])
            if title_tag:
                title = title_tag.get_text(strip=True)
            else:
                title = link_tag.get_text(strip=True)
                if not title:
                    img = item.find('img')
                    if img: title = img.get('alt')

            url = link_tag['href']
            if title and len(title) > 2 and url:
                movies.append({"title": title, "url": url})
        
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
        target_keywords = ["480p", "720p", "1080p", "2160p", "4k", "hevc", "download", "link"]
        
        links = soup.select(SITE_CONFIG["QUALITY_BUTTON_SELECTOR"])
        if not links:
            content_div = soup.select_one("div.entry-content")
            links = content_div.find_all('a', href=True) if content_div else soup.find_all('a', href=True)

        for a in links:
            if not a.has_attr('href'): continue
            text = a.get_text(strip=True).lower()
            href = a['href']
            if "category" in href or "tag" in href or href == "/": continue
            if any(k in text for k in target_keywords):
                label = a.get_text(strip=True)[:50]
                qualities.append({"quality": label, "url": href})

        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        try:
            response = self.session.get(quality_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            soup = BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            return {"error": str(e)}

        tg_pattern = re.compile(r'(https?://t\.me/[a-zA-Z0-9_]+(/[0-9]+)?)')
        found_links = []
        for a in soup.find_all('a', href=True):
            if "t.me" in a['href']: found_links.append(a['href'])
        text_links = tg_pattern.findall(str(soup))
        for link_tuple in text_links: found_links.append(link_tuple[0])

        if not found_links: return {"error": "No Telegram link found."}
        return {"tg_link": found_links[0]}

scraper = CustomScraper()

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
    await update.message.reply_text("👋 <b>Bot Online (Webhook Mode)!</b>", parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Searching: {query}...")
    results = scraper.search_movies(query)
    
    if isinstance(results, dict) and "error" in results:
        await update.message.reply_text(f"⚠️ Error: {results['error']}")
        return ConversationHandler.END
    if not results:
        await update.message.reply_text("❌ No movies found.")
        return ConversationHandler.END

    keyboard = []
    for idx, movie in enumerate(results):
        context.user_data[f"movie_{idx}"] = movie['url']
        keyboard.append([InlineKeyboardButton(movie['title'], callback_data=f"mov_{idx}")])
    await update.message.reply_text(f"Found {len(results)} movies:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    await query.edit_message_text(f"⏳ Checking Qualities...")
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
    await query.edit_message_text("🕵️‍♂️ Getting Link...")
    result = scraper.extract_telegram_link(qual_url)
    if "error" in result:
        await query.edit_message_text(f"❌ {result['error']}")
        return ConversationHandler.END
    await query.edit_message_text("✅ Link Found!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Open", url=result["tg_link"])]]))
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
    
    # ==========================================================================
    # 🚀 WEBHOOK CONFIGURATION
    # ==========================================================================
    if not WEBHOOK_URL:
        print("❌ CRITICAL: WEBHOOK_URL is missing in Environment Variables!")
        return

    print(f"🚀 Starting Webhook on Port {PORT} with URL {WEBHOOK_URL}")
    
    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
        drop_pending_updates=True  # Purane atke hue updates clear karega (Avoid Conflict)
    )

if __name__ == '__main__':
    main()
