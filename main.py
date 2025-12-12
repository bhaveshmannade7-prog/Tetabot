# main.py
import os
import requests
from fastapi import FastAPI, Request
from pathlib import Path
import uuid
import shutil

# ---------------------------
# ENVIRONMENT VARIABLES
# ---------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

API_URL = "https://teraboxdownloader.com/api?url="
TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"

TMP_DIR = Path("/tmp/terabox")
TMP_DIR.mkdir(exist_ok=True)

app = FastAPI()

# ---------------------------
# Supported TeraBox Domains
# ---------------------------
VALID_TERABOX_DOMAINS = [
    "terabox.com",
    "www.terabox.com",
    "1024terabox.com",
    "teraboxapp.com",
    "teraboxdown.com",
    "teraboxshare.com",
    "terasharelink.com",  # Your domain
    "tibobox.com",
]

def is_terabox_link(url: str):
    url = url.lower()
    return any(domain in url for domain in VALID_TERABOX_DOMAINS)

# ---------------------------
# Telegram Helpers
# ---------------------------
def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
    )

def send_video(chat_id, file_path, caption=None):
    with open(file_path, "rb") as f:
        requests.post(
            f"{TELEGRAM}/sendVideo",
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
# Call TeraBox API
# ---------------------------
def fetch_terabox_data(url):
    try:
        r = requests.get(API_URL + url, timeout=30)
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def download_file(url):
    filename = f"{uuid.uuid4()}.mp4"
    filepath = TMP_DIR / filename

    try:
        with requests.get(url, stream=True, timeout=200) as r:
            r.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        return filepath
    except:
        return None

# ---------------------------
# TEMP User Quality Store
# ---------------------------
user_choice = {}

# ---------------------------
# Webhook Handler (Main)
# ---------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):

    if token != BOT_TOKEN:
        return {"status": "invalid token"}

    update = await request.json()
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if not text:
        send_message(chat_id, "➡️ TeraBox ka link bhejo.")
        return {"ok": True}

    # Check domain
    if not is_terabox_link(text):
        send_message(chat_id, "❌ Yeh bot sirf <b>TeraBox links</b> par kaam karta hai.")
        return {"ok": True}

    send_message(chat_id, "🔍 Fetching video details...")

    data = fetch_terabox_data(text)
    if not data or "urls" not in data:
        send_message(chat_id, "❌ Video fetch fail. Link private ya invalid ho sakta hai.")
        return {"ok": True}

    urls = data["urls"]

    available = {}
    if "High" in urls:
        available["720p"] = urls["High"]
    if "Normal" in urls:
        available["480p"] = urls["Normal"]
    if "Original" in urls:
        available["Original"] = urls["Original"]

    user_choice[chat_id] = available

    quality_list = "\n".join(f"• {q}" for q in available.keys())
    send_message(
        chat_id,
        f"📥 <b>Select Quality</b>:\n{quality_list}\n\nJust type the quality (e.g., 720p)",
    )

    return {"ok": True}

# ---------------------------
# Quality Selection Handler
# ---------------------------
@app.post("/webhook/choice/{token}")
async def choice_handler(token: str, request: Request):

    if token != BOT_TOKEN:
        return {"status": "invalid token"}

    update = await request.json()
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if chat_id not in user_choice:
        send_message(chat_id, "⚠️ Pehle TeraBox link bhejo.")
        return {"ok": True}

    quality_options = user_choice[chat_id]

    if text not in quality_options:
        send_message(
            chat_id,
            "❌ Invalid quality.\nValid options:\n" + "\n".join(quality_options.keys()),
        )
        return {"ok": True}

    url = quality_options[text]
    send_message(chat_id, f"⬇ Downloading <b>{text}</b> quality... 🔄")

    file_path = download_file(url)
    if not file_path:
        send_message(chat_id, "❌ Download fail ho gaya.")
        return {"ok": True}

    send_video(chat_id, file_path, caption=f"TeraBox Video ({text}) 📦")
    cleanup()

    return {"ok": True}

# ---------------------------
# Set Webhook Automatically
# ---------------------------
@app.on_event("startup")
async def startup_webhook():

    if PUBLIC_URL:
        webhook_url = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        try:
            r = requests.get(f"{TELEGRAM}/setWebhook?url={webhook_url}")
            print("Webhook set:", r.text)
        except:
            print("Webhook set failed")

@app.get("/")
def home():
    return {"status": "running"}
