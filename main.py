import json, asyncio, os, random, logging
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted
from flask import Flask
from threading import Thread

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
# Multiple sessions comma separated: "string1,string2..."
SESSIONS = [s.strip() for s in os.getenv("SESSIONS", "").split(",") if s.strip()]

# Global DB & Settings
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"
SETTINGS = {
    "messages": [], # 5 Messages store honge
    "is_running": False,
    "speed": 12, # Default 12s
    "total_sent": 0
}

# Clients Setup
workers = []
for i, s in enumerate(SESSIONS):
    cli = Client(f"worker_{i}", session_string=s, api_id=API_ID, api_hash=API_HASH)
    workers.append(cli)

# Primary Admin App
app = workers[0]

# Flask for Render
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Worker Bot is Active! ⚡"

# --- JSON UTILS ---
def save_json(f, d):
    with open(f, "w") as file: json.dump(list(d), file, indent=4)

def load_json(f):
    if os.path.exists(f):
        with open(f, "r") as file:
            try: return set(tuple(x) for x in json.load(file))
            except: return set()
    return set()

# --- ADMIN COMMANDS ---
@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_msg(c, m):
    text = (
        "🚀 **Ultimate Multi-Worker UserBot**\n\n"
        f"✅ **Workers:** {len(workers)}\n"
        f"⏱ **Speed:** {SETTINGS['speed']}s\n\n"
        "1️⃣ `/setmsgs` [Msg1 | Msg2 | Msg3 | Msg4 | Msg5]\n"
        "2️⃣ `/scrape` @group 1000 (Direct list)\n"
        "3️⃣ `/scrape_active` @group 1000 (History scan)\n"
        "4️⃣ `/setspeed` 15 (Change speed)\n"
        "5️⃣ `/send` | `/status` | `/stop` | `/sync`"
    )
    await m.reply(text)

@app.on_message(filters.command("setmsgs") & filters.user(ADMIN_ID))
async def set_msgs(c, m):
    try:
        content = m.text.split(None, 1)[1]
        msgs = [msg.strip() for msg in content.split("|")]
        if len(msgs) < 5: return await m.reply("❌ Kam se kam 5 messages '|' se separate karke likhein.")
        SETTINGS["messages"] = msgs[:5]
        await m.reply("✅ 5 Messages set ho gaye hain. Ab bot random use karega.")
    except: await m.reply("❌ Usage: `/setmsgs Message 1 | Message 2 | Message 3...` ")

@app.on_message(filters.command("setspeed") & filters.user(ADMIN_ID))
async def set_speed(c, m):
    try:
        sp = int(m.text.split()[1])
        SETTINGS["speed"] = sp
        info = "🟢 Safe: 15s+ | 🟡 Risk: 10-12s | 🔴 High Risk: <8s"
        await m.reply(f"⏱ Speed set to {sp}s.\n\n{info}")
    except: await m.reply("Usage: `/setspeed 12` ")

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_direct(c, m):
    try:
        _, target, limit = m.text.split()
        if target.startswith("-") or target.isdigit(): target = int(target)
        await m.reply("🔍 Scraping direct members list...")
        scraped = load_json(USERS_DB)
        count = 0
        async for mem in c.get_chat_members(target):
            if count >= int(limit): break
            if not mem.user.is_bot:
                scraped.add((mem.user.id, mem.user.username or "N/A"))
                count += 1
        save_json(USERS_DB, scraped)
        await m.reply(f"✅ Scraped: {count}. File created. Use `/download` to get it.")
        # Data delete logic after sending file to admin
        await c.send_document(m.chat.id, USERS_DB, caption="Backup. Data is now cleared from server.")
        os.remove(USERS_DB) # Memory bachane ke liye delete
    except Exception as e: await m.reply(f"Error: {e}")

@app.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
async def scrape_active(c, m):
    try:
        _, target, limit = m.text.split()
        await m.reply("🔍 Scanning history for active users...")
        scraped = load_json(USERS_DB)
        count = 0
        async for msg in c.get_chat_history(target, limit=int(limit)):
            if msg.from_user and not msg.from_user.is_bot:
                scraped.add((msg.from_user.id, msg.from_user.username or "N/A"))
                count += 1
        save_json(USERS_DB, scraped)
        await m.reply(f"✅ Active Users: {count}")
    except Exception as e: await m.reply(f"Error: {e}")

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_worker(c, m):
    if not SETTINGS["messages"]: return await m.reply("❌ Pehle `/setmsgs` karein!")
    if SETTINGS["is_running"]: return await m.reply("⚠️ Already running!")
    
    scraped, sent = list(load_json(USERS_DB)), load_json(SENT_DB)
    pending = [u for u in scraped if u[0] not in sent]
    if not pending: return await m.reply("❌ No data! Upload USERS_DB first.")

    SETTINGS["is_running"] = True
    await m.reply(f"🚀 Messaging started using {len(workers)} workers...")

    worker_idx = 0
    for user_id, username in pending:
        if not SETTINGS["is_running"]: break
        
        # Select Worker
        worker = workers[worker_idx]
        try:
            msg = random.choice(SETTINGS["messages"])
            await worker.send_message(user_id, msg)
            sent.add(user_id)
            save_json(SENT_DB, sent)
            
            # Rotate worker
            worker_idx = (worker_idx + 1) % len(workers)
            await asyncio.sleep(SETTINGS["speed"])

        except FloodWait as e:
            worker_idx = (worker_idx + 1) % len(workers)
            await asyncio.sleep(5)
        except Exception: continue

    SETTINGS["is_running"] = False
    await m.reply("🏁 Finished!")

@app.on_message(filters.command("status") & filters.user(ADMIN_ID))
async def status_cmd(c, m):
    sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
    await m.reply(f"📊 Stats:\nDatabase: {sc}\nSent Already: {sn}\nWorkers: {len(workers)}")

@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(c, m):
    SETTINGS["is_running"] = False
    await m.reply("🛑 Stopped!")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_cmd(c, m):
    sent = load_json(SENT_DB)
    async for dialog in c.get_dialogs():
        if dialog.chat.type == enums.ChatType.PRIVATE:
            sent.add(dialog.chat.id)
    save_json(SENT_DB, sent)
    await m.reply("✅ Sent History Synced!")

@app.on_message(filters.document & filters.user(ADMIN_ID))
async def import_data(c, m):
    await m.download(m.document.file_name)
    await m.reply(f"✅ Imported {m.document.file_name}")

def run_web(): web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    Thread(target=run_web).start()
    for w in workers: w.start()
    app.run()
