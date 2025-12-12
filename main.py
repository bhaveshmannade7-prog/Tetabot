# main.py
import os
import re
import json
import time
import requests
import shutil
import uuid
from pathlib import Path
from typing import Optional, Dict
from fastapi import FastAPI, Request

# -------------------------
# ENVIRONMENT
# -------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")  # e.g. https://your-service.onrender.com
TERABOX_COOKIE = os.environ.get("TERABOX_COOKIE", "")  # optional for private links
# Optional fallback public APIs (comma separated) - you can set this env if you want
EXTRA_APIS = os.environ.get("EXTRA_APIS", "")  # e.g. https://tb.rip/api?url=,https://teraboxdownloader.com/api?url=

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable required")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TMP_DIR = Path("/tmp/terabox_bot")
TMP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="TeraBox Triple-Protection Downloader Bot")

# -------------------------
# Utilities: Telegram helpers
# -------------------------
def tg_send_message(chat_id: int, text: str):
    try:
        resp = requests.post(f"{TELEGRAM_API}/sendMessage",
                             json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                             timeout=20)
        return resp.ok
    except Exception as e:
        print("tg_send_message error:", e)
        return False

def tg_send_video(chat_id: int, file_path: Path, caption: Optional[str] = None):
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(f"{TELEGRAM_API}/sendVideo",
                                 data={"chat_id": chat_id, "caption": caption or ""},
                                 files={"video": f},
                                 timeout=600)
        return resp.ok
    except Exception as e:
        print("tg_send_video error:", e)
        return False

def cleanup_temp():
    try:
        if TMP_DIR.exists():
            shutil.rmtree(TMP_DIR)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("cleanup error:", e)

def download_to_file(url: str, chunk_size: int = 1024*1024, timeout: int = 240) -> Optional[Path]:
    try:
        fname = TMP_DIR / f"{uuid.uuid4()}.mp4"
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(fname, "wb") as f:
                for chunk in r.iter_content(chunk_size):
                    if chunk:
                        f.write(chunk)
        return fname
    except Exception as e:
        print("download_to_file error:", e)
        return None

# -------------------------
# Normalize mirror links -> extract id and canonicalize
# -------------------------
def normalize_link(url: str) -> str:
    url = url.strip()
    if "/s/" in url:
        try:
            id_part = url.split("/s/")[1].split("?")[0].split("#")[0]
            # keep only the id (some sites add extra path)
            return f"https://terabox.com/s/{id_part}"
        except Exception:
            return url
    return url

# -------------------------
# 1) Backend decoder function (primary) - domain-specific attempts
#    This tries to detect shareid/uk or other parameters from HTML and
#    call the mirror's internal API endpoints to get a signed download URL.
# -------------------------
def backend_decode(link: str) -> Optional[Dict[str, str]]:
    """
    Return dict of quality->direct_url or None.
    This tries multiple domain-specific patterns & endpoints.
    """
    session = requests.Session()
    headers = {}
    if TERABOX_COOKIE:
        headers["Cookie"] = TERABOX_COOKIE
    try:
        r = session.get(link, headers=headers, timeout=15)
        html = r.text
    except Exception as e:
        print("backend_decode: page fetch failed:", e)
        return None

    # Try patterns to find shareid/uk or file id tokens used by mirrors
    # Accept several patterns (numeric or alnum)
    shareid = None
    uk = None

    # Common patterns in some mirror pages
    m = re.search(r'["\']shareid["\']\s*[:=]\s*["\']?([\w-]+)', html)
    if m:
        shareid = m.group(1)
    m2 = re.search(r'["\']uk["\']\s*[:=]\s*["\']?([\w-]+)', html)
    if m2:
        uk = m2.group(1)

    # Another pattern names sometimes used
    if not shareid:
        m = re.search(r'/s/([A-Za-z0-9_-]{6,})', link)
        if m:
            shareid = m.group(1)

    # If both found, try domain-specific API endpoints
    # Build several candidate API URLs based on the link's host
    try:
        host = re.search(r"https?://([^/]+)/", link)
        host_domain = host.group(1) if host else "terabox.com"
    except:
        host_domain = "terabox.com"

    candidate_apis = []

    # Standard candidate endpoints used by many mirrors (try multiple formats)
    if shareid and uk:
        candidate_apis.append(f"https://{host_domain}/api/share/presign?shareid={shareid}&uk={uk}")
        candidate_apis.append(f"https://{host_domain}/api/share/download?shareid={shareid}&uk={uk}")
        candidate_apis.append(f"https://{host_domain}/interface/presign_download?shareid={shareid}&uk={uk}")
        candidate_apis.append(f"https://{host_domain}/share/link/download?shareid={shareid}&uk={uk}")

    # If we have only shareid
    if shareid and not uk:
        candidate_apis.append(f"https://{host_domain}/api/share/get?shareid={shareid}")
        candidate_apis.append(f"https://{host_domain}/share/get?shareid={shareid}")
        candidate_apis.append(f"https://{host_domain}/api/share/info?shareid={shareid}")

    # Add a generic /api/file endpoint fallback
    candidate_apis.append(f"https://{host_domain}/api/file/info?s={shareid or ''}")
    candidate_apis.append(f"https://{host_domain}/api/info?shareid={shareid or ''}")

    # De-duplicate
    seen = set()
    candidate_apis_filtered = []
    for u in candidate_apis:
        if u and u not in seen:
            candidate_apis_filtered.append(u)
            seen.add(u)

    # Try each candidate API
    for api in candidate_apis_filtered:
        try:
            resp = session.get(api, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            j = None
            try:
                j = resp.json()
            except:
                # if response is string, try to find JSON inside
                txt = resp.text
                jj = re.search(r'(\{.*\})', txt, re.DOTALL)
                if jj:
                    try:
                        j = json.loads(jj.group(1))
                    except:
                        j = None
            if not j:
                continue
            # Attempt to extract download URL(s) from returned JSON
            # Look for common keys (urls, data.download_url, url, download_url)
            candidates = {}
            if isinstance(j, dict):
                # common wrapper keys
                for key in ("urls", "data", "result", "file", "output"):
                    if key in j and isinstance(j[key], dict):
                        d = j[key]
                        # map known name patterns
                        for k2, v2 in d.items():
                            if isinstance(v2, str) and v2.startswith("http"):
                                candidates.setdefault("Original", v2)
                            elif isinstance(v2, dict):
                                # nested
                                for kk, vv in v2.items():
                                    if isinstance(vv, str) and vv.startswith("http"):
                                        candidates.setdefault("Original", vv)
                # direct keys
                for alt in ("download_url", "downloadUrl", "direct_url", "url", "file_url"):
                    if alt in j and isinstance(j[alt], str) and j[alt].startswith("http"):
                        candidates.setdefault("Original", j[alt])
                # sometimes list of files
                if "files" in j and isinstance(j["files"], list):
                    for f in j["files"]:
                        if isinstance(f, dict):
                            for alt in ("downloadUrl", "download_url", "url"):
                                if alt in f and isinstance(f[alt], str) and f[alt].startswith("http"):
                                    candidates.setdefault("Original", f[alt])
            if candidates:
                # try to map multiple qualities if available
                result = {}
                # If returned j contains keys like High/Normal/Original map them
                if isinstance(j, dict) and "urls" in j and isinstance(j["urls"], dict):
                    for kq, vq in j["urls"].items():
                        if isinstance(vq, str) and vq.startswith("http"):
                            label = kq
                            # normalize label
                            lk = kq.lower()
                            if "high" in lk or "720" in lk:
                                label = "720p"
                            elif "normal" in lk or "480" in lk:
                                label = "480p"
                            else:
                                label = "Original"
                            result[label] = vq
                # else fallback to single Original
                if not result and "Original" in candidates:
                    result["Original"] = candidates["Original"]
                if result:
                    return result
        except Exception as e:
            print("backend api try error for", api, e)
            continue

    return None

# -------------------------
# 2) Multi-API fallback (public downloader APIs)
# -------------------------
def multi_api_fallback(link: str) -> Optional[Dict[str, str]]:
    apis = []
    if EXTRA_APIS:
        apis = [a.strip() for a in EXTRA_APIS.split(",") if a.strip()]
    # default candidate public APIs (may be unstable)
    apis += [
        "https://tb.rip/api?url=",
        "https://api.terabox-link-downloader.xyz/?url=",
        "https://teraboxdownloader.com/api?url="
    ]
    for base in apis:
        try:
            url = base + requests.utils.requote_uri(link)
            r = requests.get(url, timeout=20)
            if r.status_code != 200:
                continue
            j = None
            try:
                j = r.json()
            except:
                continue
            # expect j['urls'] or j['download_url']
            if isinstance(j, dict):
                if "urls" in j and isinstance(j["urls"], dict):
                    res = {}
                    for k, v in j["urls"].items():
                        if isinstance(v, str) and v.startswith("http"):
                            # map keys
                            lk = k.lower()
                            if "high" in lk or "720" in lk:
                                res["720p"] = v
                            elif "normal" in lk or "480" in lk:
                                res["480p"] = v
                            else:
                                res["Original"] = v
                    if res:
                        return res
                for alt in ("download_url", "downloadUrl", "url"):
                    if alt in j and isinstance(j[alt], str) and j[alt].startswith("http"):
                        return {"Original": j[alt]}
        except Exception as e:
            print("multi_api_fallback error:", e)
            continue
    return None

# -------------------------
# 3) Safe HTML scraping fallback (no recursive regex)
# -------------------------
def scrape_fallback(link: str) -> Optional[Dict[str, str]]:
    headers = {}
    if TERABOX_COOKIE:
        headers["Cookie"] = TERABOX_COOKIE
    try:
        r = requests.get(link, headers=headers, timeout=15)
        html = r.text
    except Exception as e:
        print("scrape_fallback fetch error:", e)
        return None

    # 1) try window.jsData
    m = re.search(r"window\.jsData\s*=\s*(\{.*?\});", html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            out = {}
            for k in ("downloadUrl", "download_url", "normalDownloadUrl", "lowDownloadUrl", "directUrl"):
                if k in data and isinstance(data[k], str) and data[k].startswith("http"):
                    lk = k.lower()
                    if "normal" in lk:
                        out["720p"] = data[k]
                    elif "low" in lk:
                        out["480p"] = data[k]
                    else:
                        out["Original"] = data[k]
            if out:
                return out
        except Exception as e:
            print("jsData parse err:", e)

    # 2) look for dlink:"URL" style
    m2 = re.search(r'dlink"\s*:\s*"(https?://[^"]+)"', html)
    if m2:
        return {"720p": m2.group(1)}

    # 3) last fallback: any .mp4 found
    mp4s = re.findall(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html)
    if mp4s:
        return {"Original": mp4s[0]}

    return None

# -------------------------
# Top-level resolver that chains the three methods
# -------------------------
def resolve_terabox(link: str) -> Optional[Dict[str, str]]:
    link_norm = normalize_link(link)
    print("Resolving link:", link, "=>", link_norm)
    # 1) Backend decoder (best)
    res = backend_decode(link_norm)
    if res:
        print("Resolved via backend_decode")
        return res
    # 2) Multi-API fallback
    res = multi_api_fallback(link_norm)
    if res:
        print("Resolved via multi_api_fallback")
        return res
    # 3) Scrape fallback
    res = scrape_fallback(link_norm)
    if res:
        print("Resolved via scrape_fallback")
        return res
    return None

# -------------------------
# In-memory pending map (chat_id -> qualities)
# -------------------------
PENDING: Dict[int, Dict[str, str]] = {}

# -------------------------
# Webhook endpoints
# -------------------------
@app.post("/webhook/{token}")
async def webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"ok": False, "error": "invalid token"}

    body = await request.json()
    message = body.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    if not text:
        tg_send_message(chat_id, "➡️ TeraBox ka link bhejo (mirror ya original).")
        return {"ok": True}

    tg_send_message(chat_id, "🔎 Processing link... (this may take a few seconds)")

    try:
        qualities = resolve_terabox(text)
    except Exception as e:
        print("resolve_terabox exception:", e)
        qualities = None

    if not qualities:
        tg_send_message(chat_id, "❌ Video fetch fail. Shayad link private ya inaccessible ho. (Try adding TERABOX_COOKIE env)")
        return {"ok": True}

    # Store pending and ask user to choose quality
    PENDING[chat_id] = qualities
    qtext = "\n".join(f"• {k}" for k in qualities.keys())
    tg_send_message(chat_id, f"📥 Select Quality:\n{qtext}\n\nType quality name (e.g., 720p).")

    return {"ok": True}

@app.post("/webhook/choice/{token}")
async def webhook_choice(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"ok": False, "error": "invalid token"}

    body = await request.json()
    message = body.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id:
        return {"ok": True}

    if chat_id not in PENDING:
        tg_send_message(chat_id, "⚠️ Pehle TeraBox link bhejo.")
        return {"ok": True}

    choices = PENDING[chat_id]
    if text not in choices:
        tg_send_message(chat_id, "❌ Invalid quality. Valid:\n" + "\n".join(choices.keys()))
        return {"ok": True}

    dl_url = choices[text]
    tg_send_message(chat_id, f"⬇ Downloading ({text})... Please wait.")

    # download file
    path = download_to_file(dl_url)
    if not path:
        tg_send_message(chat_id, "❌ Download failed. Try another quality or add TERABOX_COOKIE.")
        return {"ok": True}

    # send video
    sent = tg_send_video(chat_id, path, caption=f"TeraBox Video ({text})")
    # cleanup
    try:
        path.unlink()
    except:
        pass
    if chat_id in PENDING:
        del PENDING[chat_id]

    if sent:
        tg_send_message(chat_id, "✅ Video delivered.")
    else:
        tg_send_message(chat_id, "❌ Failed to send video. File may be too large for Telegram or server issue.")

    return {"ok": True}

# Auto set webhook
@app.on_event("startup")
async def set_webhook():
    if PUBLIC_URL:
        try:
            wh = f"{PUBLIC_URL}/webhook/{BOT_TOKEN}"
            r = requests.get(f"{TELEGRAM_API}/setWebhook?url={wh}", timeout=10)
            print("setWebhook response:", r.status_code, r.text)
        except Exception as e:
            print("setWebhook error:", e)

@app.get("/")
def root():
    return {"status": "running"}
