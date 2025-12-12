# main.py
import os
import requests
from fastapi import FastAPI, Request
from pydantic import BaseModel
from pathlib import Path
import uuid
import shutil

# ---------------------------
# ENVIRONMENT VARIABLES
# ---------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")   # Render URL (auto webhook)
API_URL = "https://teraboxdownloader.com/api?url="

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"
TMP_DIR = Path("/tmp/terabox")
TMP_DIR.mkdir(exist_ok=True)

app = FastAPI()

# ---------------------------
# Telegram Helper Functions
# ---------------------------
def send_message(chat_id, text):
    url = f"{TELEGRAM}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

def send_video(chat_id, file_path, caption=None):
    url = f"{TELEGRAM}/sendVideo"
    with open(file_path, "rb") as f:
        requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"video": f},
        )

def cleanup():
    try:
        shutil.rmtree(TMP_DIR)
        TMP_DIR.mkdir(exist_ok=True)
    except:
        pass

# ---------------------------
# API Call to TeraBox
# ---------------------------
def fetch_terabox_data(link):
    try:
        r = requests.get(API_URL + link, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def download_from_url(url):
    filename = f"{uuid.uuid4()}.mp4"
    filepath = TMP_DIR / filename

    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        return filepath
    except:
        return None

# ---------------------------
# Webhook Receiver
# ---------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"status": "invalid token"}

    update = await request.json()
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not text:
        send_message(chat_id, "TeraBox link bhejo 👇")
        return {"ok": True}

    if "terabox" not in text.lower():
        send_message(chat_id, "❌ Yeh bot sirf TeraBox links support karta hai.")
        return {"ok": True}

    # Fetch metadata
    data = fetch_terabox_data(text)
    if not data or "urls" not in data:
        send_message(chat_id, "❌ Video fetch nahi ho paaya. Link private ho sakta hai.")
        return {"ok": True}

    urls = data["urls"]

    # Quality options
    quality_buttons = []
    available = {}

    if "High" in urls:
        available["720p"] = urls["High"]
        quality_buttons.append("720p")
    if "Normal" in urls:
        available["480p"] = urls["Normal"]
        quality_buttons.append("480p")
    if "Original" in urls:
        available["Original"] = urls["Original"]
        quality_buttons.append("Original")

    # Save user state
    user_choice[chat_id] = available

    # Ask user for quality
    send_message(
        chat_id,
        "📥 <b>Select Quality</b>:\n" +
        "\n".join(f"• {q}" for q in available.keys()),
    )

    return {"ok": True}

# ---------------------------
# Handle Quality Choice
# ---------------------------
user_choice = {}  # TEMP MEMORY (works fine on Render single instance)

@app.post("/webhook/choice/{token}")
async def choice_handler(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"status": "invalid token"}

    update = await request.json()
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if chat_id not in user_choice:
        send_message(chat_id, "❌ Pehle TeraBox link bhejo.")
        return {"ok": True}

    options = user_choice[chat_id]

    if text not in options:
        send_message(chat_id, "❌ Wrong quality. Valid options:\n" + ", ".join(options))
        return {"ok": True}

    dl_url = options[text]
    send_message(chat_id, f"⬇ Downloading ({text})... Please wait 🔄")

    file_path = download_from_url(dl_url)
    if not file_path:
        send_message(chat_id, "❌ Download fail ho gaya.")
        return {"ok": True}

    send_video(chat_id, file_path, caption=f"TeraBox Video ({text})")
    cleanup()

    return {"ok": True}

# ---------------------------
# Auto-set Webhook
# ---------------------------
@app.on_event("startup")
async def set_webhook():
    if PUBLIC_URL:
        url = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        requests.get(f"{TELEGRAM}/setWebhook?url={url}")
        print("Webhook set:", url)

@app.get("/")
def home():
    return {"status": "running"}
