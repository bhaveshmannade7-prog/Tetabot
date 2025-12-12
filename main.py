# main.py
import os
import re
import json
import requests
from fastapi import FastAPI, Request
from pathlib import Path
import uuid
import shutil

BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")
TERABOX_COOKIE = os.environ.get("TERABOX_COOKIE", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN required")

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"
TMP = Path("/tmp/terabox")
TMP.mkdir(exist_ok=True)

app = FastAPI()

# ---------------------------
# TELEGRAM HELPERS
# ---------------------------
def send_msg(cid, text):
    try:
        requests.post(f"{TELEGRAM}/sendMessage",
                      json={"chat_id": cid, "text": text, "parse_mode": "HTML"})
    except:
        pass

def send_video(cid, path, caption=None):
    with open(path, "rb") as f:
        requests.post(
            f"{TELEGRAM}/sendVideo",
            data={"chat_id": cid, "caption": caption or ""},
            files={"video": f}
        )

def cleanup():
    try:
        shutil.rmtree(TMP)
        TMP.mkdir(exist_ok=True)
    except:
        pass

# ---------------------------
# Normalize any TeraBox mirror
# ---------------------------
def normalize(url: str):
    if "/s/" not in url:
        return url
    try:
        file_id = url.split("/s/")[1].split("?")[0].split("#")[0]
        return f"https://terabox.com/s/{file_id}"
    except:
        return url

# ---------------------------
# Safe HTML Scraper (NO RECURSIVE PATTERNS)
# ---------------------------
def extract_video_links(url):
    headers = {}
    if TERABOX_COOKIE:
        headers["Cookie"] = TERABOX_COOKIE

    try:
        html = requests.get(url, headers=headers, timeout=15).text
    except:
        return None

    # Extract JSON inside window.jsData = {...}
    js = re.search(r"window\.jsData\s*=\s*(\{.*?\});", html, re.DOTALL)
    if js:
        try:
            data = json.loads(js.group(1))
            links = {}
            if "downloadUrl" in data:
                links["Original"] = data["downloadUrl"]
            if "normalDownloadUrl" in data:
                links["720p"] = data["normalDownloadUrl"]
            if "lowDownloadUrl" in data:
                links["480p"] = data["lowDownloadUrl"]
            return links if links else None
        except:
            pass

    # Fallback: find dlink:"URL"
    d = re.search(r'dlink":"(https[^"]+)"', html)
    if d:
        return {"720p": d.group(1)}

    # Last fallback: look for .mp4 URLs
    mp4s = re.findall(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html)
    if mp4s:
        return {"Original": mp4s[0]}

    return None

# ---------------------------
# Download File
# ---------------------------
def download(url):
    fname = TMP / f"{uuid.uuid4()}.mp4"
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(fname, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        return fname
    except:
        return None

pending = {}

# ---------------------------
# MAIN WEBHOOK
# ---------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"error": "invalid token"}

    data = await request.json()
    msg = data.get("message", {})
    cid = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()

    if not text:
        send_msg(cid, "➡️ TeraBox link bhejo.")
        return {"ok": True}

    send_msg(cid, "🔎 Processing link...")

    link = normalize(text)
    qualities = extract_video_links(link)

    if not qualities:
        send_msg(cid, "❌ Video fetch fail. Shayad link private ho.")
        return {"ok": True}

    pending[cid] = qualities

    quality_list = "\n".join(f"• {q}" for q in qualities)
    send_msg(cid, f"📥 Select Quality:\n{quality_list}\n\nType quality name (e.g., 720p)")

    return {"ok": True}

# ---------------------------
# QUALITY SELECTOR
# ---------------------------
@app.post("/webhook/choice/{token}")
async def choice_handler(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"error": "invalid token"}

    data = await request.json()
    msg = data.get("message", {})
    cid = msg.get("chat", {}).get("id")
    choice = msg.get("text", "").strip()

    if cid not in pending:
        send_msg(cid, "⚠️ Pehle TeraBox link bhejo.")
        return {"ok": True}

    qualities = pending[cid]

    if choice not in qualities:
        send_msg(cid, "❌ Invalid quality.\nValid:\n" + "\n".join(qualities))
        return {"ok": True}

    dl_url = qualities[choice]
    send_msg(cid, f"⬇ Downloading {choice}...")

    path = download(dl_url)
    if not path:
        send_msg(cid, "❌ Download failed.")
        return {"ok": True}

    send_video(cid, path, caption=f"TeraBox Video ({choice})")
    cleanup()
    return {"ok": True}

# ---------------------------
# AUTO WEBHOOK ON START
# ---------------------------
@app.on_event("startup")
async def startup():
    if PUBLIC_URL:
        webhook_url = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        requests.get(f"{TELEGRAM}/setWebhook?url={webhook_url}")
        print("Webhook set →", webhook_url)

@app.get("/")
def home():
    return {"status": "running"}
