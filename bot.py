# ===============================
# SMART TELEGRAM MOVIE BOT
# Improved & Production Ready
# ===============================

import os
import re
import time
import random
import logging
import cloudscraper
from bs4 import BeautifulSoup
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)

# ===============================
# CONFIG
# ===============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "10000"))

ALLOWED_DOMAINS = [
    d.strip().rstrip("/")
    for d in os.getenv("ALLOWED_DOMAINS", "").split(",")
    if d.strip()
]

REQUEST_TIMEOUT = 60
SAFE_DELAY = (1.5, 3.5)

SELECT_MOVIE, SELECT_QUALITY = range(2)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("MovieBot")

# ===============================
# SITE SELECTORS
# ===============================

SEARCH_ITEMS = (
    "article, div.post, li.post-item, div.latestPost article"
)

TITLE_SELECTORS = (
    "h2, h3, a, .title, .caption"
)

QUALITY_SELECTORS = (
    "a.buttn, a.btn, a.download, a.button"
)

VALID_QUALITY_KEYS = [
    "480p", "720p", "1080p", "2160p",
    "4k", "hevc", "10bit", "hdr"
]

BLOCK_WORDS = [
    "how to", "join", "login",
    "telegram", "whatsapp"
]

# ===============================
# SCRAPER CLASS
# ===============================

class SmartScraper:
    def __init__(self):
        self.scraper = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True
            }
        )

    def _delay(self):
        time.sleep(random.uniform(*SAFE_DELAY))

    def get_soup(self, url):
        try:
            self._delay()
            r = self.scraper.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )
            if "Just a moment" in r.text:
                return None
            return BeautifulSoup(r.text, "html.parser")
        except Exception:
            return None

    # -----------------------------

    def search_movies(self, query):
        results = []

        for domain in ALLOWED_DOMAINS:
            search_url = f"{domain}/?s={query}"
            soup = self.get_soup(search_url)
            if not soup:
                continue

            items = soup.select(SEARCH_ITEMS)[:15]

            for it in items:
                a = it.find("a", href=True)
                if not a:
                    continue

                title = ""
                for sel in TITLE_SELECTORS.split(","):
                    t = it.select_one(sel.strip())
                    if t and t.get_text(strip=True):
                        title = t.get_text(strip=True)
                        break

                if not title:
                    title = a.get_text(strip=True)

                if query.lower() not in title.lower():
                    continue

                results.append({
                    "title": title[:80],
                    "url": a["href"]
                })

        uniq = {}
        for r in results:
            uniq[r["url"]] = r

        return list(uniq.values())[:10]

    # -----------------------------

    def get_qualities(self, movie_url):
        soup = self.get_soup(movie_url)
        if not soup:
            return []

        links = soup.select(QUALITY_SELECTORS)
        if not links:
            links = soup.find_all("a", href=True)

        qualities = []

        for a in links:
            text = a.get_text(strip=True).lower()
            href = a.get("href")

            if not href:
                continue

            if any(b in text for b in BLOCK_WORDS):
                continue

            if any(k in text for k in VALID_QUALITY_KEYS):
                qualities.append({
                    "label": a.get_text(strip=True)[:40],
                    "url": href
                })

        uniq = {}
        for q in qualities:
            uniq[q["url"]] = q

        return list(uniq.values())

    # -----------------------------

    def resolve_chain(self, url):
        if "t.me" in url:
            return url

        soup = self.get_soup(url)
        if not soup:
            return None

        # HubDrive → HubCloud
        hubcloud = soup.find("a", href=re.compile("hubcloud", re.I))
        if hubcloud:
            return self.resolve_chain(hubcloud["href"])

        # Telegram button
        tg = soup.find("a", string=re.compile("telegram", re.I))
        if tg and tg.get("href"):
            return tg["href"]

        return None


scraper = SmartScraper()

# ===============================
# BOT HANDLERS
# ===============================

def is_admin(update: Update):
    return update.effective_user.id == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "🤖 <b>Movie Bot Ready</b>\nSend movie name to search.",
        parse_mode="HTML"
    )

async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    query = update.message.text.strip()
    await update.message.reply_text(f"🔍 Searching <b>{query}</b>…", parse_mode="HTML")

    movies = scraper.search_movies(query)
    if not movies:
        await update.message.reply_text("❌ Movie not found.")
        return ConversationHandler.END

    kb = []
    for i, m in enumerate(movies):
        context.user_data[f"m{i}"] = m["url"]
        kb.append([InlineKeyboardButton(m["title"], callback_data=f"m_{i}")])

    await update.message.reply_text(
        "🎬 Select Movie:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SELECT_MOVIE

async def movie_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    idx = int(q.data.split("_")[1])
    url = context.user_data.get(f"m{idx}")

    await q.edit_message_text("📀 Fetching qualities…")

    qualities = scraper.get_qualities(url)
    if not qualities:
        await q.edit_message_text("❌ No quality links found.")
        return ConversationHandler.END

    kb = []
    for i, qu in enumerate(qualities):
        context.user_data[f"q{i}"] = qu["url"]
        kb.append([InlineKeyboardButton(qu["label"], callback_data=f"q_{i}")])

    await q.edit_message_text(
        "⚙ Select Quality:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SELECT_QUALITY

async def quality_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    idx = int(q.data.split("_")[1])
    url = context.user_data.get(f"q{idx}")

    await q.edit_message_text("🔗 Resolving final link…")

    final = scraper.resolve_chain(url)
    if not final:
        await q.edit_message_text("❌ Failed to fetch Telegram link.")
        return ConversationHandler.END

    await q.edit_message_text(
        "✅ <b>Final Link</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Open Link", url=final)]
        ]),
        parse_mode="HTML"
    )
    return ConversationHandler.END

# ===============================
# MAIN
# ===============================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, search_handler)],
        states={
            SELECT_MOVIE: [CallbackQueryHandler(movie_select, "^m_")],
            SELECT_QUALITY: [CallbackQueryHandler(quality_select, "^q_")]
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
