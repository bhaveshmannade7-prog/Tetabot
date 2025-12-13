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
# CONFIGURATION & LOGGING
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

# Validation
if not BOT_TOKEN or not ADMIN_ID or not ALLOWED_DOMAINS:
    logger.critical("Missing required Environment Variables! Check BOT_TOKEN, ADMIN_ID, ALLOWED_DOMAINS.")
    exit(1)

try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    logger.critical("ADMIN_ID must be an integer.")
    exit(1)

# Conversation States
SELECT_MOVIE, SELECT_QUALITY, CONFIRM_FETCH = range(3)

# ==============================================================================
# SCRAPER ENGINE (GENERIC & ROBUST)
# ==============================================================================
class PublicDomainScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

    def _safe_get(self, url):
        """Wrapper for requests to handle errors and detect CAPTCHA."""
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            
            # CAPTCHA / Protection Detection
            if response.status_code in [403, 503] or "captcha" in response.text.lower() or "verify human" in response.text.lower():
                return {"error": "captcha", "url": url}
            
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}
            
            return {"soup": BeautifulSoup(response.text, 'html.parser'), "url": response.url}
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def search_movies(self, query):
        """Searches the first allowed domain. Logic fits standard directory structures."""
        base_url = ALLOWED_DOMAINS[0].strip()
        # NOTE: Adjust search query parameter '?s=' or '?q=' based on specific site architecture
        search_url = f"{base_url}/?s={query}" 
        
        result = self._safe_get(search_url)
        if "error" in result:
            return result

        soup = result["soup"]
        movies = []
        
        # GENERIC PARSER: Looks for common article/post titles in WP/CMS sites
        # You may need to refine 'h2', 'a', or class names for specific sites.
        for item in soup.find_all(['h2', 'h3'], limit=15): 
            link_tag = item.find('a')
            if link_tag and link_tag.get('href'):
                title = link_tag.get_text(strip=True)
                # Simple filter to ensure it matches query somewhat
                if query.lower() in title.lower(): 
                    movies.append({"title": title, "url": link_tag['href']})
        
        return movies[:10] # Max 10

    def get_qualities(self, movie_url):
        """Scrapes movie page for quality links."""
        result = self._safe_get(movie_url)
        if "error" in result:
            return result

        soup = result["soup"]
        qualities = []

        # GENERIC PARSER: Looks for text explicitly mentioning resolutions
        target_keywords = ["480p", "720p", "1080p", "HEVC", "x265", "x264", "HQ"]
        
        for a in soup.find_all('a', href=True):
            text = a.get_text(strip=True)
            # Check if link text contains quality info
            if any(k in text for k in target_keywords):
                qualities.append({"quality": text[:30], "url": a['href']}) # Limit text length
        
        # Deduplicate by URL
        unique_qualities = {v['url']: v for v in qualities}.values()
        return list(unique_qualities)

    def extract_telegram_link(self, quality_url):
        """Visits the quality page/redirect and finds t.me links."""
        result = self._safe_get(quality_url)
        if "error" in result:
            return result

        soup = result["soup"]
        
        # Strict Regex for Telegram links
        tg_pattern = re.compile(r'(https?://t\.me/[a-zA-Z0-9_]+(/[0-9]+)?)')
        
        found_links = []
        
        # Check all hrefs
        for a in soup.find_all('a', href=True):
            if "t.me" in a['href']:
                found_links.append(a['href'])
        
        # Check raw text (sometimes links are not clickable)
        text_links = tg_pattern.findall(str(soup))
        for link_tuple in text_links:
            found_links.append(link_tuple[0])

        if not found_links:
            return {"error": "No Telegram link found on this page."}
        
        # Return first valid link
        return {"tg_link": found_links[0]}

scraper = PublicDomainScraper()

# ==============================================================================
# TELEGRAM HANDLERS
# ==============================================================================

async def restricted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Security Gatekeeper."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Access denied.\nThis is a private admin-only bot.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    
    welcome_text = (
        "👋 <b>Welcome Admin!</b>\n\n"
        "🎬 <b>Public-Domain Movie Assistant Bot</b>\n"
        "<i>Ready to curate legal content.</i>\n\n"
        "<b>Instructions:</b>\n"
        "1. Just type a movie name (e.g., 'Night of the Living Dead').\n"
        "2. Select the correct movie.\n"
        "3. Choose the quality.\n"
        "4. Confirm and open the Telegram link.\n\n"
        "⚠️ <i>System uses safe headers. If CAPTCHA is detected, you will be notified.</i>"
    )
    await update.message.reply_text(welcome_text, parse_mode='HTML')

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return

    query = update.message.text.strip()
    if len(query) < 2:
        await update.message.reply_text("⚠️ Search query too short.")
        return ConversationHandler.END

    await update.message.reply_text(f"🔍 Searching public domain sources for: <b>{query}</b>...", parse_mode='HTML')
    
    # Perform Search
    results = scraper.search_movies(query)

    # Error Handling
    if isinstance(results, dict) and "error" in results:
        if results["error"] == "captcha":
            await update.message.reply_text(
                f"⚠️ <b>Human Verification Detected!</b>\n\nBot was stopped. Please visit this link manually to solve the captcha:\n{results['url']}\n\nType /resume when done.",
                parse_mode='HTML'
            )
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"❌ Error: {results['error']}")
            return ConversationHandler.END

    if not results:
        await update.message.reply_text("❌ No matching public-domain movies found.")
        return ConversationHandler.END

    # Build Buttons
    keyboard = []
    for idx, movie in enumerate(results):
        # Store URL in callback data (truncated if necessary, handled better with DB but keeping simple)
        # Using a list index to avoid ContextLengthExceeded if URLs are long
        context.user_data[f"movie_{idx}"] = movie['url']
        keyboard.append([InlineKeyboardButton(movie['title'], callback_data=f"mov_{idx}")])

    await update.message.reply_text(
        f"✅ Found {len(results)} results:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECT_MOVIE

async def movie_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Parse Index
    idx = int(query.data.split("_")[1])
    movie_url = context.user_data.get(f"movie_{idx}")
    
    await query.edit_message_text(f"⏳ Scanning qualities for selection...")
    
    # Scrape Qualities
    qualities = scraper.get_qualities(movie_url)

    if isinstance(qualities, dict) and "error" in qualities:
        await query.edit_message_text(f"❌ Error fetching qualities: {qualities['error']}")
        return ConversationHandler.END

    if not qualities:
        await query.edit_message_text("❌ No specific quality links found on page. Page might differ from standard structure.")
        return ConversationHandler.END

    # Build Buttons
    keyboard = []
    for idx, q in enumerate(qualities):
        context.user_data[f"qual_{idx}"] = q['url']
        keyboard.append([InlineKeyboardButton(q['quality'], callback_data=f"qual_{idx}")])
    
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

    await query.edit_message_text(
        "🎬 <b>Select Video Quality:</b>\nBot will look for official Telegram links.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return SELECT_QUALITY

async def quality_selection_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("🚫 Operation cancelled.")
        return ConversationHandler.END

    idx = int(query.data.split("_")[1])
    qual_url = context.user_data.get(f"qual_{idx}")

    await query.edit_message_text("🕵️‍♂️ Extracting official Telegram link...")

    # Extract Deep Link
    result = scraper.extract_telegram_link(qual_url)

    if "error" in result:
        await query.edit_message_text(f"❌ Extraction Failed: {result['error']}")
        return ConversationHandler.END

    tg_link = result["tg_link"]

    # Final Confirmation Button
    keyboard = [
        [InlineKeyboardButton("📥 Fetch from Telegram", url=tg_link)],
        [InlineKeyboardButton("🔄 New Search", callback_data="restart")]
    ]

    await query.edit_message_text(
        "✅ <b>Link Discovered!</b>\n\n"
        "Click the button below to open the link in your Telegram client.\n"
        "<i>(Bot does not auto-forward for safety)</i>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 Process Cancelled.")
    return ConversationHandler.END

async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await restricted(update, context): return
    await update.message.reply_text("✅ Bot resumed. Please try your search again.")

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
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(conv_handler)
    
    # Callback for 'New Search' button in final message which isn't in conversation
    application.add_handler(CallbackQueryHandler(lambda u,c: u.callback_query.message.reply_text("Send a new movie name."), pattern="restart"))

    print(f"Bot started. Listening for Admin ID: {ADMIN_ID}")
    application.run_polling()

if __name__ == '__main__':
    main()
                
