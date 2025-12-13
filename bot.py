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
# 🛠️ CONFIGURATION
# ==============================================================================
SITE_CONFIG = {
    "SEARCH_ITEM_SELECTOR": "article, div.post, div.latestPost article, ul.recent-movies li, li.post-item, div.result-item",
    "SEARCH_TITLE_SELECTOR": "h2.title, h2.entry-title, p.title, .caption, a",
    # Sirf specific buttons uthayenge taaki "How to download" na aaye
    "QUALITY_BUTTON_SELECTOR": "a.buttn, a.btn, a.download-link, a.button, div.linkbutton a"
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
ALLOWED_DOMAINS = os.getenv("ALLOWED_DOMAINS", "").split(",")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60")) # Increased for multiple page hops
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
# 2. INTELLIGENT CHAIN SCRAPER (The Magic Logic)
# ==============================================================================
class ChainScraper:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
        )

    def _get_soup(self, url):
        try:
            resp = self.scraper.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if "Just a moment" in resp.text: return None, "Cloudflare Block"
            return BeautifulSoup(resp.text, 'html.parser'), None
        except Exception as e:
            return None, str(e)

    def search_movies(self, query):
        if not ALLOWED_DOMAINS: return {"error": "Domain missing"}
        base_url = ALLOWED_DOMAINS[0].strip().rstrip('/')
        search_url = f"{base_url}/?s={query}"
        
        soup, err = self._get_soup(search_url)
        if err: return {"error": err}

        movies = []
        items = soup.select(SITE_CONFIG["SEARCH_ITEM_SELECTOR"])
        
        for item in items[:20]:
            link_tag = item.find('a', href=True)
            if not link_tag: continue
            
            title_tag = item.select_one(SITE_CONFIG["SEARCH_TITLE_SELECTOR"])
            title = title_tag.get_text(strip=True) if title_tag else link_tag.get_text(strip=True)
            if not title: img = item.find('img'); title = img.get('alt') if img else ""

            url = link_tag['href']
            
            # Simple Filter: Title me query ka koi bhi hissa hona chahiye
            if title and len(title) > 2 and url and "http" in url:
                query_words = query.lower().split()
                if any(w in title.lower() for w in query_words):
                    movies.append({"title": title, "url": url})
        
        # Deduplicate
        seen = set()
        unique = []
        for m in movies:
            if m['url'] not in seen: unique.append(m); seen.add(m['url'])
        return unique[:10]

    def get_qualities(self, movie_url):
        soup, err = self._get_soup(movie_url)
        if err: return []

        qualities = []
        # Keywords to identify REAL quality links (ignore garbage)
        valid_keywords = ["480p", "720p", "1080p", "2160p", "4k", "hevc", "60fps", "10bit"]
        
        # 1. Select all potential buttons
        links = soup.select(SITE_CONFIG["QUALITY_BUTTON_SELECTOR"])
        if not links: links = soup.find_all('a', href=True)

        for a in links:
            if not a.has_attr('href'): continue
            text = a.get_text(strip=True)
            lower_text = text.lower()
            href = a['href']

            # 🛑 GARBAGE FILTER (Ye "How to download" ko hatayega)
            if any(x in lower_text for x in ["how to", "join", "telegram", "whatsapp", "login"]): continue
            
            # ✅ VALIDITY CHECK
            if any(k in lower_text for k in valid_keywords):
                label = text[:50]
                qualities.append({"quality": label, "url": href})

        unique = {v['url']: v for v in qualities}.values()
        return list(unique)

    # --- THE CHAIN RESOLVER ---
    def resolve_chain(self, start_url):
        """
        Follows the path: Movie Page -> HubDrive -> HubCloud -> Verification Page
        """
        current_url = start_url
        
        # STEP 1: Check if we are on HubDrive (Look for HubCloud button)
        if "hubdrive" in current_url or "drive" in current_url:
            print(f"DEBUG: Landed on HubDrive: {current_url}")
            soup, err = self._get_soup(current_url)
            if err: return {"error": err}
            
            # Find "[HubCloud Server]" button
            hubcloud_btn = soup.find('a', string=re.compile(r"HubCloud", re.I))
            if not hubcloud_btn:
                # Try finding any button that links to hubcloud
                hubcloud_btn = soup.find('a', href=re.compile(r"hubcloud"))
            
            if hubcloud_btn:
                current_url = hubcloud_btn['href']
            else:
                return {"error": "Could not find [HubCloud Server] button on HubDrive page."}

        # STEP 2: Check if we are on HubCloud (Look for Telegram button)
        if "hubcloud" in current_url:
            print(f"DEBUG: Landed on HubCloud: {current_url}")
            soup, err = self._get_soup(current_url)
            if err: return {"error": err}
            
            # Find "Download From Telegram" button
            # Button class is typically 'btn-primary' or text contains 'Telegram'
            tg_btn = soup.find('a', string=re.compile(r"Telegram", re.I))
            if not tg_btn:
                 tg_btn = soup.find('a', class_=re.compile(r"btn-primary|btn-success"))
            
            if tg_btn:
                final_link = tg_btn['href']
                return {"tg_link": final_link} # Ye link Verification page ka hoga
            else:
                return {"error": "Could not find 'Download From Telegram' button on HubCloud page."}

        # Fallback: If it's a direct Telegram link
        if "t.me" in current_url:
            return {"tg_link": current_url}

        return {"error": "Could not resolve the link chain. Page structure might have changed."}

scraper = ChainScraper()

# ==============================================================================
# 3. BOT HANDLERS
# ==============================================================================
async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(ADMIN_ID): return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("👋 <b>Bot Ready!</b>\nSmart Chain Resolution Active.", parse_mode='HTML')

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
    await update.message.reply_text(f"✅ Found {len(results)} movies:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    await query.edit_message_text(f"⏳ Scanning Qualities...")
    qualities = scraper.get_qualities(movie_url)

    if not qualities:
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
    
    await query.edit_message_text("⚙️ <b>Processing Link Chain...</b>\n1. HubDrive...\n2. HubCloud...\n3. Fetching Final Link...", parse_mode="HTML")
    
    # Run the Chain Resolver
    result = scraper.resolve_chain(qual_url)
    
    if "error" in result:
        await query.edit_message_text(f"❌ {result['error']}")
        return ConversationHandler.END
    
    final_link = result["tg_link"]
    
    await query.edit_message_text(
        f"✅ <b>Final Link Ready!</b>\n\nClick below to open the Verification Page:", 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📥 Open & Verify", url=final_link)]]),
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
            
