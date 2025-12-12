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

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Temp folder
TMP_DIR = Path("/tmp/terabox")
TMP_DIR.mkdir(exist_ok=True)

app = FastAPI()

# ---------------------------
# SUPPORTED MIRROR DOMAINS
# ---------------------------
VALID_DOMAINS = [
    "terabox.com",
    "www.terabox.com",
    "1024terabox.com",
    "teraboxapp.com",
    "teraboxdown.com",
    "teraboxshare.com",
    "terasharelink.com",
    "tibobox.com",
]

def normalize_link(url: str):
    """Convert mirror domain → official domain"""
    url = url.strip()
    for d in VALID_DOMAINS:
        if d in url.lower():
            # Extract ID from any domain format /s/<id>
            try:
                base = url.split("/s/")[1]
                return f"https://terabox.com/s/{base}"
            except:
                return url
    return url

# ---------------------------
# TELEGRAM HELPERS
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
# MULTI-API FALLBACK SYSTEM
# ---------------------------
APIS = [
    "https://tb.rip/api?url=",
    "https://api.terabox-link-downloader.xyz/?url=",
    "https://teraboxdownloader.com/api?url=",
]

def fetch_video_data(url):
    """Try multiple APIs until one returns valid result."""
    for api in APIS:
        try:
            r = requests.get(api + url, timeout=20)
            if r.status_code == 200:
                j = r.json()
                if "urls" in j:
                    return j
        except:
            pass
    return None

# ---------------------------
# DOWNLOAD FILE
# ---------------------------
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
# TEMP STATE: USER → Quality Map
# ---------------------------
user_choice = {}

# ---------------------------
# MAIN WEBHOOK
# ---------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):

    if token != BOT_TOKEN:
        return {"error": "Invalid token"}

    update = await request.json()
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if not text:
        send_message(chat_id, "➡️ TeraBox link bhejo.")
        return {"ok": True}

    # Normalize (mirror → official)
    normalized = normalize_link(text)

    send_message(chat_id, "🔍 Fetching video details...")

    # Fetch from multi-API
    data = fetch_video_data(normalized)
    if not data:
        send_message(chat_id, "❌ Video fetch fail. Ho sakta hai link private ho.")
        return {"ok": True}

    urls = data.get("urls", {})
    available = {}

    if "High" in urls:
        available["720p"] = urls["High"]
    if "Normal" in urls:
        available["480p"] = urls["Normal"]
    if "Original" in urls:
        available["Original"] = urls["Original"]

    if not available:
        send_message(chat_id, "❌ No downloadable quality found.")
        return {"ok": True}

    user_choice[chat_id] = available

    quality_list = "\n".join(f"• {q}" for q in available)
    send_message(chat_id, f"📥 <b>Select Quality</b>:\n{quality_list}\n\nType the quality (e.g., 720p)")

    return {"ok": True}

# ---------------------------
# QUALITY SELECTOR
# ---------------------------
@app.post("/webhook/choice/{token}")
async def quality_handler(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"error": "Invalid token"}

    update = await request.json()
    message = update.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip()

    if chat_id not in user_choice:
        send_message(chat_id, "⚠️ Pehle TeraBox link bhejo.")
        return {"ok": True}

    qualities = user_choice[chat_id]
    if text not in qualities:
        send_message(chat_id, "❌ Invalid quality.\nValid:\n" + "\n".join(qualities))
        return {"ok": True}

    dl_url = qualities[text]

    send_message(chat_id, f"⬇ Downloading <b>{text}</b>...")

    file_path = download_file(dl_url)
    if not file_path:
        send_message(chat_id, "❌ Download failed.")
        return {"ok": True}

    send_video(chat_id, file_path, caption=f"TeraBox Video ({text})")
    cleanup()

    return {"ok": True}

# ---------------------------
# SET WEBHOOK ON START
# ---------------------------
@app.on_event("startup")
async def startup():
    if PUBLIC_URL:
        url = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        requests.get(f"{TELEGRAM}/setWebhook?url={url}")
        print("Webhook set →", url)

@app.get("/")
def home():
    return {"status": "TeraBox Bot Running"}
