import os
import subprocess
import uuid
import shutil
import requests
from pathlib import Path
from fastapi import FastAPI, Request

# ================= ENV =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"

BASE_DIR = Path("/tmp/downloads")
BASE_DIR.mkdir(exist_ok=True)

app = FastAPI()
pending = {}  # chat_id -> youtube_url

# ================= Telegram Helpers =================
def send_message(chat_id, text):
    requests.post(
        f"{TELEGRAM}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20
    )

def send_file(chat_id, file_path, caption=None):
    with open(file_path, "rb") as f:
        requests.post(
            f"{TELEGRAM}/sendDocument",
            data={"chat_id": chat_id, "caption": caption or ""},
            files={"document": f},
            timeout=600
        )

# ================= FFMPEG FIX =================
def ensure_ffmpeg():
    if shutil.which("ffmpeg"):
        return "ffmpeg"

    ffmpeg_path = "/tmp/ffmpeg"
    if not os.path.exists(ffmpeg_path):
        os.system(
            "curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz "
            "| tar -xJ --strip-components=1 -C /tmp"
        )
    return ffmpeg_path

FFMPEG = ensure_ffmpeg()

# ================= Downloaders =================
def download_youtube(url, choice):
    out = BASE_DIR / f"{uuid.uuid4()}.%(ext)s"

    if choice == "audio":
        cmd = [
            "yt-dlp",
            "-f", "bestaudio",
            "--extract-audio",
            "--audio-format", "mp3",
            "--ffmpeg-location", FFMPEG,
            "-o", str(out),
            url
        ]
    else:
        height = "360" if choice == "360" else "720"
        cmd = [
            "yt-dlp",
            "-f", f"best[height<={height}]",
            "--merge-output-format", "mp4",
            "--ffmpeg-location", FFMPEG,
            "-o", str(out),
            url
        ]

    subprocess.run(cmd, check=True)
    return list(BASE_DIR.glob("*"))[-1]

def download_instagram(url):
    out = BASE_DIR / f"{uuid.uuid4()}.mp4"
    cmd = [
        "yt-dlp",
        "-f", "best",
        "--ffmpeg-location", FFMPEG,
        "-o", str(out),
        url
    ]
    subprocess.run(cmd, check=True)
    return out

# ================= Webhook =================
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

    # YouTube
    if "youtube.com" in text or "youtu.be" in text:
        pending[chat_id] = text
        send_message(
            chat_id,
            "Select format:\n360 / 720 / audio"
        )
        return {"ok": True}

    # Instagram
    if "instagram.com" in text:
        send_message(chat_id, "⬇ Downloading Instagram video...")
        try:
            file = download_instagram(text)
            send_file(chat_id, file, "Instagram Video")
            file.unlink(missing_ok=True)
        except Exception as e:
            send_message(chat_id, "❌ Instagram download failed")
        return {"ok": True}

    # YouTube choice
    if chat_id in pending and text.lower() in ["360", "720", "audio"]:
        url = pending.pop(chat_id)
        send_message(chat_id, "⬇ Downloading...")
        try:
            file = download_youtube(url, text.lower())
            send_file(chat_id, file, "YouTube Download")
            file.unlink(missing_ok=True)
        except Exception as e:
            send_message(chat_id, "❌ YouTube download failed")
        return {"ok": True}

    send_message(chat_id, "❌ Unsupported link")
    return {"ok": True}

# ================= Startup =================
@app.on_event("startup")
async def startup():
    if PUBLIC_URL:
        webhook = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        requests.get(f"{TELEGRAM}/setWebhook?url={webhook}", timeout=10)

@app.get("/")
def home():
    return {"status": "running"}
