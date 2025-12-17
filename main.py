import os
import logging
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")        # Telegram Bot Token
WEBHOOK_URL = os.getenv("WEBHOOK_URL")    # https://your-app.onrender.com
PORT = int(os.getenv("PORT", 10000))

# ----------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
tg_app = Application.builder().token(BOT_TOKEN).build()

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi!\n\n"
        "👉 YouTube video link bhejo\n"
        "🎵 /audio  – sirf audio ke liye\n\n"
        "⚠️ Personal / own content only"
    )

async def audio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "audio"
    await update.message.reply_text("🎵 Audio mode ON\nAb YouTube link bhejo")

# ---------------- VIDEO HANDLER ----------------
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    mode = context.user_data.get("mode", "video")

    await update.message.reply_text("⏳ Download ho raha hai...")

    ydl_opts = {
        "outtmpl": "download.%(ext)s",
        "quiet": True,
    }

    if mode == "audio":
        ydl_opts["format"] = "bestaudio"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_name = ydl.prepare_filename(info)
            if mode == "audio":
                file_name = file_name.rsplit(".", 1)[0] + ".mp3"

        with open(file_name, "rb") as f:
            if mode == "audio":
                await context.bot.send_audio(chat_id, audio=f)
            else:
                await context.bot.send_video(chat_id, video=f)

        os.remove(file_name)
        context.user_data["mode"] = "video"

    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Error aaya, dusra link try karo")

# ---------------- WEBHOOK ----------------
@app.route("/", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), tg_app.bot)
    tg_app.update_queue.put_nowait(update)
    return "ok"

@app.route("/", methods=["GET"])
def home():
    return "Bot is running"

# ---------------- MAIN ----------------
async def setup():
    await tg_app.initialize()
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}/")

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(CommandHandler("audio", audio_cmd))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    await tg_app.start()

if __name__ == "__main__":
    import asyncio
    asyncio.run(setup())
    app.run(host="0.0.0.0", port=PORT)
