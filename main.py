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
import asyncio

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
PORT = int(os.environ.get("PORT", 10000))

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
telegram_app = Application.builder().token(BOT_TOKEN).build()

# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 YouTube Downloader Bot\n\n"
        "🔗 Link bhejo\n"
        "🎵 /audio = audio only\n\n"
        "⚠️ Personal use only"
    )

async def audio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "audio"
    await update.message.reply_text("🎵 Audio mode ON")

# ---------- HANDLER ----------
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    chat_id = update.message.chat_id
    mode = context.user_data.get("mode", "video")

    await update.message.reply_text("⏳ Downloading...")

    ydl_opts = {"outtmpl": "file.%(ext)s", "quiet": True}

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
            filename = ydl.prepare_filename(info)
            if mode == "audio":
                filename = filename.rsplit(".", 1)[0] + ".mp3"

        with open(filename, "rb") as f:
            if mode == "audio":
                await context.bot.send_audio(chat_id, f)
            else:
                await context.bot.send_video(chat_id, f)

        os.remove(filename)
        context.user_data["mode"] = "video"

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text("❌ Download failed")

# ---------- WEBHOOK ----------
@app.route("/", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    asyncio.create_task(telegram_app.process_update(update))
    return "ok"

@app.route("/", methods=["GET"])
def home():
    return "Bot running"

async def main():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/")
    await telegram_app.start()

    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("audio", audio_cmd))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

if __name__ == "__main__":
    asyncio.run(main())
    app.run(host="0.0.0.0", port=PORT)
