# main.py
import os
import re
import json
import requests
import shutil
import uuid
from pathlib import Path
from fastapi import FastAPI, Request

# Try import TeraboxDL (pip package terabox-downloader)
TERABOXDL_AVAILABLE = False
try:
    from TeraboxDL import TeraboxDL
    TERABOXDL_AVAILABLE = True
except Exception as e:
    TERABOXDL_AVAILABLE = False
    # we'll fallback to scraping / apify

# -------------------------
# ENV
# -------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")  # https://your-service.onrender.com
TERABOX_COOKIE = os.environ.get("TERABOX_COOKIE")  # optional but recommended: e.g. "lang=en; ndus=..."
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")  # optional (for Apify actor fallback)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable required")

TELEGRAM = f"https://api.telegram.org/bot{BOT_TOKEN}"
TMP = Path("/tmp/terabox")
TMP.mkdir(exist_ok=True)

app = FastAPI(title="TeraBox Downloader Bot - robust")

# -------------------------
# Helpers: Telegram
# -------------------------
def send_msg(cid, text):
    try:
        requests.post(f"{TELEGRAM}/sendMessage", json={"chat_id": cid, "text": text, "parse_mode": "HTML"}, timeout=20)
    except Exception:
        pass

def send_video(cid, path, caption=None):
    try:
        with open(path, "rb") as f:
            requests.post(f"{TELEGRAM}/sendVideo", data={"chat_id": cid, "caption": caption or ""}, files={"video": f}, timeout=300)
    except Exception:
        pass

def cleanup_tmp():
    try:
        if TMP.exists():
            shutil.rmtree(TMP)
        TMP.mkdir(exist_ok=True)
    except Exception:
        pass

# -------------------------
# Normalize any mirror -> terabox.com/s/<id>
# -------------------------
VALID_DOMAINS = [
    "terabox", "terasharelink", "teraboxapp", "1024terabox", "tibobox", "teraboxdown", "teraboxshare"
]

def normalize_link(url: str):
    url = url.strip()
    if "/s/" not in url:
        return url
    # Extract the part after /s/
    try:
        parts = url.split("/s/")[1]
        file_id = parts.split("?")[0].split("#")[0]
        return f"https://terabox.com/s/{file_id}"
    except Exception:
        return url

# -------------------------
# Layer A: Try TeraboxDL library (best if cookie provided)
# Usage example from package docs: from TeraboxDL import TeraboxDL
# -------------------------
def teraboxdl_get(link):
    """
    Returns dict of qualities -> direct_url, or None on failure.
    Requires TERABOX_COOKIE env (optional but increases success).
    """
    if not TERABOXDL_AVAILABLE:
        return None
    try:
        cookie = TERABOX_COOKIE or ""
        client = TeraboxDL(cookie)
        # get_file_info returns metadata; `direct_url=True` requests direct links
        info = client.get_file_info(link, direct_url=True)
        # info structure varies; try common keys
        # expected: info['urls'] or info['download_url'] etc.
        # Normalize to dict
        res = {}
        # try 'urls' -> {'High':..., 'Normal':..., 'Original':...}
        if isinstance(info, dict):
            if "urls" in info and isinstance(info["urls"], dict):
                for k,v in info["urls"].items():
                    # map keys to nicer names
                    if k.lower().find("high")!=-1 or "720" in k:
                        res["720p"] = v
                    elif k.lower().find("normal")!=-1 or "480" in k:
                        res["480p"] = v
                    else:
                        res.setdefault("Original", v)
            # sometimes direct_url or downloadUrl key
            for alt_key in ("download_url", "downloadUrl", "direct_url", "directUrl"):
                if alt_key in info and info[alt_key]:
                    res["Original"] = info[alt_key]
            # some wrappers
            if not res and "data" in info and isinstance(info["data"], dict):
                for k in ("download_url","downloadUrl","direct_url"):
                    if k in info["data"]:
                        res["Original"] = info["data"][k]
        # If client returned a list or other, try to pull first item
        if not res and isinstance(info, list) and len(info)>0 and isinstance(info[0], dict):
            first = info[0]
            if "url" in first:
                res["Original"] = first["url"]
        return res if res else None
    except Exception as e:
        # library failed
        return None

# -------------------------
# Layer B: HTML scraping fallback (robust extraction)
# -------------------------
def scrape_terabox(link):
    """
    Scrape terabox page for embedded JSON or dlink tokens and return dict qualities->url.
    """
    try:
        r = requests.get(link, timeout=20)
        html = r.text
    except Exception:
        return None

    # Try window.jsData JSON blob
    js_match = re.search(r"window\.jsData\s*=\s*(\{.*?\});", html, re.DOTALL)
    if js_match:
        try:
            js = json.loads(js_match.group(1))
            quals = {}
            # common keys
            for k in ("downloadUrl","download_url","normalDownloadUrl","lowDownloadUrl","directUrl"):
                if k in js and js[k]:
                    # map roughly
                    key = "Original"
                    if "normal" in k.lower() or "720" in k.lower():
                        key = "720p"
                    elif "low" in k.lower() or "480" in k.lower():
                        key = "480p"
                    quals[key] = js[k]
            # also check nested
            if "files" in js and isinstance(js["files"], list):
                for f in js["files"]:
                    if isinstance(f, dict) and "downloadUrl" in f:
                        quals["Original"] = f["downloadUrl"]
            if quals:
                return quals
        except Exception:
            pass

    # Try dlink token or other patterns
    dmatch = re.search(r'"dlink"\s*:\s*"(https?://[^"]+)"', html)
    if dmatch:
        return {"720p": dmatch.group(1)}

    # try generic JSON in page containing 'download' keys
    jmatch = re.search(r"(\{(?:[^{}]|(?R))*\"download\"", html, re.DOTALL)
    # fallback: try to find any http(s) URL ending with mp4 in page
    urls = re.findall(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html)
    if urls:
        # return first as Original
        return {"Original": urls[0]}

    return None

# -------------------------
# Layer C: Optional Apify actor (requires APIFY_TOKEN)
# Apify actor: Terabox Fast Video Downloader or similar (paid/free trial) — useful fallback
# docs: https://apify.com/hello.datawizards/terabox-videodownload-link-scraper
# -------------------------
def apify_get(link):
    if not APIFY_TOKEN:
        return None
    try:
        # Example: call actor run that returns JSON (this is a generic pattern)
        # NOTE: real Apify actor endpoints and input schema vary; user may need to subscribe
        actor_url = "https://api.apify.com/v2/acts/hello.datawizards~terabox-videodownload-link-scraper/runs?token=" + APIFY_TOKEN
        payload = {"link": link}
        r = requests.post(actor_url, json=payload, timeout=30)
        if r.status_code == 201 or r.status_code == 200:
            # actor started; get result items - this is simplified
            data = r.json()
            # actor-specific parsing required; try to read .output or fetch run result via API
            # for simplicity, attempt to read direct 'output' if present
            if isinstance(data, dict) and "output" in data and data["output"]:
                out = data["output"]
                if isinstance(out, dict) and "urls" in out:
                    return out["urls"]
        return None
    except Exception:
        return None

# -------------------------
# Download helper
# -------------------------
def download_file(url):
    fname = TMP / f"{uuid.uuid4()}.mp4"
    try:
        with requests.get(url, stream=True, timeout=240) as r:
            r.raise_for_status()
            with open(fname, "wb") as f:
                for chunk in r.iter_content(1024*1024):
                    if chunk:
                        f.write(chunk)
        return fname
    except Exception:
        return None

# -------------------------
# In-memory state: chat_id -> qualities map
# -------------------------
pending = {}

# -------------------------
# Main webhook handler
# -------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"ok": False, "error": "invalid token"}

    update = await request.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    cid = chat.get("id")
    text = (message.get("text") or "").strip()

    if not cid:
        return {"ok": True}

    if not text:
        send_msg(cid, "➡️ TeraBox link bhejo.")
        return {"ok": True}

    send_msg(cid, "🔎 Processing link...")

    link = normalize_link(text)

    # Try Layer A (library)
    quals = None
    if TERABOXDL_AVAILABLE:
        quals = teraboxdl_get(link)
        if quals:
            send_msg(cid, "✅ Info found via TeraboxDL library.")
    # Try Layer B (scrape)
    if not quals:
        quals = scrape_terabox(link)
        if quals:
            send_msg(cid, "✅ Info found via direct scrape.")
    # Try Layer C (Apify) if still none
    if not quals:
        quals = apify_get(link)
        if quals:
            send_msg(cid, "✅ Info found via Apify actor.")
    if not quals:
        # final fallback: try replacing domain to terabox.com and retry scrape once
        alt = normalize_link(link)
        if alt != link:
            quals = scrape_terabox(alt)
            if quals:
                send_msg(cid, "✅ Info found via alternate domain scrape.")
    if not quals:
        send_msg(cid, "❌ Video fetch fail. Link private ya inaccessible. (Tip: add TERABOX_COOKIE env if link needs login)")
        return {"ok": True}

    # store pending options
    pending[cid] = quals
    # prepare message
    qlist = "\n".join(f"• {q}" for q in quals.keys())
    send_msg(cid, f"📥 Select Quality:\n{qlist}\n\nType quality name (e.g., 720p)")

    return {"ok": True}

# -------------------------
# Choice handler (separate webhook path)
# -------------------------
@app.post("/webhook/choice/{token}")
async def choice_handler(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"ok": False, "error": "invalid token"}
    update = await request.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    cid = chat.get("id")
    text = (message.get("text") or "").strip()
    if not cid:
        return {"ok": True}
    if cid not in pending:
        send_msg(cid, "⚠️ Pehle TeraBox link bhejo.")
        return {"ok": True}
    opts = pending[cid]
    if text not in opts:
        send_msg(cid, "❌ Invalid quality. Valid:\n" + "\n".join(opts.keys()))
        return {"ok": True}
    dl = opts[text]
    send_msg(cid, f"⬇ Downloading {text}... Please wait.")
    path = download_file(dl)
    if not path:
        send_msg(cid, "❌ Download failed.")
        return {"ok": True}
    send_video(cid, path, caption=f"TeraBox Video ({text})")
    cleanup_tmp()
    # remove pending entry
    if cid in pending:
        del pending[cid]
    return {"ok": True}

# -------------------------
# Auto set webhook on start
# -------------------------
@app.on_event("startup")
async def startup_event():
    if PUBLIC_URL:
        try:
            wh = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
            requests.get(f"{TELEGRAM}/setWebhook?url={wh}", timeout=10)
            print("Webhook set:", wh)
        except Exception:
            pass

@app.get("/")
def root():
    return {"status": "running"}
