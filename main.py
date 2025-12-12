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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN required")

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"
TMP = Path("/tmp/terabox")
TMP.mkdir(exist_ok=True)

app = FastAPI()

# ---------------------------
# MIRROR DOMAIN SUPPORT
# ---------------------------
def normalize(url):
    """Convert any mirror to main terabox domain"""
    if "/s/" not in url:
        return url
    try:
        file_id = url.split("/s/")[1]
        return f"https://terabox.com/s/{file_id}"
    except:
        return url

# ---------------------------
# TELEGRAM HELPERS
# ---------------------------
def send_msg(cid, text):
    requests.post(f"{TELEGRAM}/sendMessage",
                  json={"chat_id": cid, "text": text, "parse_mode": "HTML"})

def send_vid(cid, path, caption=None):
    with open(path, "rb") as f:
        requests.post(f"{TELEGRAM}/sendVideo",
                      data={"chat_id": cid, "caption": caption},
                      files={"video": f})

# ---------------------------
# CORE: SCRAPE TERABOX HTML
# ---------------------------
def extract_download_urls(url):
    """Extract working video URLs directly from TeraBox page"""
    try:
        html = requests.get(url, timeout=20).text
    except:
        return None

    # Try extracting jsonData block
    js_match = re.search(r"window\.jsData\s*=\s*(\{.*?\});", html, re.DOTALL)
    if not js_match:
        return None

    try:
        js_data = json.loads(js_match.group(1))
    except:
        return None

    # Possible link locations
    qualities = {}

    # Highest quality link
    if "downloadUrl" in js_data:
        qualities["Original"] = js_data["downloadUrl"]

    # sometimes different key
    if "normalDownloadUrl" in js_data:
        qualities["720p"] = js_data["normalDownloadUrl"]

    if "lowDownloadUrl" in js_data:
        qualities["480p"] = js_data["lowDownloadUrl"]

    # Last fallback: look for dlink token
    dlink = re.search(r'dlink":"(.*?)"', html)
    if dlink:
        qualities["720p"] = dlink.group(1)

    return qualities if qualities else None

# ---------------------------
# FILE DOWNLOADER
# ---------------------------
def download(url):
    fname = TMP / f"{uuid.uuid4()}.mp4"
    try:
        with requests.get(url, stream=True, timeout=200) as r:
            r.raise_for_status()
            with open(fname, "wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        return fname
    except:
        return None

# ---------------------------
# STATE
# ---------------------------
pending = {}

# ---------------------------
# MAIN WEBHOOK
# ---------------------------
@app.post("/webhook/{token}")
async def main_handler(token, request: Request):
    if token != BOT_TOKEN:
        return {"error": "bad token"}

    data = await request.json()
    msg = data.get("message", {})
    cid = msg.get("chat", {}).get("id")
    text = msg.get("text", "")

    if not text:
        send_msg(cid, "➡️ TeraBox link bhejo.")
        return {"ok": True}

    # Normalize any mirror → official
    link = normalize(text)

    send_msg(cid, "🔍 Fetching video info...")

    qualities = extract_download_urls(link)
    if not qualities:
        send_msg(cid, "❌ Video fetch fail. Link private ya invalid ho sakta hai.")
        return {"ok": True}

    pending[cid] = qualities

    qlist = "\n".join(f"• {k}" for k in qualities)
    send_msg(cid, f"📥 <b>Select Quality:</b>\n{qlist}\n\nJust type the quality name.")

    return {"ok": True}

# ---------------------------
# QUALITY SELECTOR
# ---------------------------
@app.post("/webhook/choice/{token}")
async def choice_handler(token, request: Request):
    if token != BOT_TOKEN:
        return {"error": "bad token"}

    data = await request.json()
    msg = data.get("message", {})
    cid = msg.get("chat", {}).get("id")
    q = msg.get("text", "").strip()

    if cid not in pending:
        send_msg(cid, "⚠️ Pehle TeraBox link bhejo.")
        return {"ok": True}

    qualities = pending[cid]

    if q not in qualities:
        send_msg(cid, "❌ Wrong quality.\nValid:\n" + "\n".join(qualities))
        return {"ok": True}

    url = qualities[q]
    send_msg(cid, f"⬇ Downloading <b>{q}</b>...")

    path = download(url)
    if not path:
        send_msg(cid, "❌ Download fail.")
        return {"ok": True}

    send_vid(cid, path, caption=f"TeraBox Video ({q})")
    shutil.rmtree(TMP)
    TMP.mkdir(exist_ok=True)

    return {"ok": True}

# ---------------------------
# SET WEBHOOK ON START
# ---------------------------
@app.on_event("startup")
async def start():
    if PUBLIC_URL:
        wh = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
        requests.get(f"{TELEGRAM}/setWebhook?url={wh}")
        print("Webhook set:", wh)

@app.get("/")
def home():
    return {"status": "running"}
