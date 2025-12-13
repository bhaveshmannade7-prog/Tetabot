import os
import subprocess
import requests
from fastapi import FastAPI, Request

# =============== ENV =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = FastAPI()
pending = {}  # chat_id -> youtube_url

# =============== Telegram helpers =================
def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20
    )

# =============== yt-dlp helpers =================
def get_youtube_link(url, choice):
    if choice == "audio":
        cmd = ["yt-dlp", "-f", "bestaudio", "-g", url]
    else:
        height = "360" if choice == "360" else "720"
        cmd = ["yt-dlp", "-f", f"best[height<={height}]", "-g", url]

    result = subprocess.run(
        cmd, capture_output=True, text=True
    )

    if result.returncode != 0:
        return None

    return result.stdout.strip().split("\n")[0]

def get_instagram_link(url):
    cmd = ["yt-dlp", "-f", "best", "-g", url]
    result = subprocess.run(
        cmd, capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip().split("\n")[0]

# =============== Webhook =================
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"ok": False}

    data = await request.json()
    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    # YouTube link
    if "youtube.com" in text or "youtu.be" in text:
        pending[chat_id] = text
        send_message(
            chat_id,
            "Select format:\n360 / 720 / audio"
        )
        return {"ok": True}

    # Instagram link
    if "instagram.com" in text:
        send_message(chat_id, "🔎 Extracting direct link...")
        link = get_instagram_link(text)
        if link:
            send_message(chat_id, f"✅ Direct Download Link:\n{link}")
        else:
            send_message(
                chat_id,
                "❌ Link extract failed.\n(Login required or rate-limit)"
            )
        return {"ok": True}

    # YouTube format selection
    if chat_id in pending and text.lower() in ["360", "720", "audio"]:
        url = pending.pop(chat_id)
        send_message(chat_id, "🔎 Generating direct link...")
        link = get_youtube_link(url, text.lower())
        if link:
            send_message(
                chat_id,
                f"✅ Direct Download Link ({text.upper()}):\n{link}"
            )
        else:
            send_message(
                chat_id,
                "❌ Link extract failed (bot-detection / rate-limit)."
            )
        return {"ok": True}

    send_message(chat_id, "❌ Unsupported link")
    return {"ok": True}

# =============== Startup =================
@app.on_event("startup")
async def startup():
    if PUBLIC_URL:
        webhook = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        requests.get(
            f"{TELEGRAM}/setWebhook?url={webhook}",
            timeout=10
        )

@app.get("/")
def home():
    return {"status": "running"}
