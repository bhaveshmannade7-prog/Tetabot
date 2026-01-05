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
RAW_SESSIONS = os.getenv("SESSIONS", "")
SESSION_LIST = [s.strip() for s in RAW_SESSIONS.split(",") if s.strip()]

web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Worker Bot V9 is Online! ⚡"

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Files
USERS_DB, SENT_DB = "scraped_users.json", "sent_history.json"

# In-memory Global Settings
SETTINGS = {
    "is_running": False,
    "speed": 12,
    "msgs": ["Hi!", "Hello!", "Hey!", "Greetings!", "Yo!"], # 5 Slots
    "success": 0
}

# Clients Setup
clients = []
if SESSION_LIST:
    for i, s in enumerate(SESSION_LIST):
        try:
            cli = Client(f"worker_{i}", session_string=s, api_id=API_ID, api_hash=API_HASH)
            clients.append(cli)
        except Exception as e: logger.error(f"Error: {e}")

# Admin app
app = clients[0] if clients else None

# --- HELPERS ---
def save_json(file, data):
    with open(file, "w") as f:
        json.dump(list(data), f, indent=4) # Proper formatting (line by line)

def load_json(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:
                d = json.load(f)
                return set(tuple(x) for x in d)
            except: return set()
    return set()

# --- ADMIN COMMANDS ---

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_msg(c, m):
    text = (
        "🔥 **Multi-Worker Bot V9 (Modified)**\n\n"
        f"✅ **Workers Online:** {len(clients)}\n"
        f"⏱ **Speed:** {SETTINGS['speed']}s\n\n"
        "**Scraping:**\n"
        "• `/scrape @group 1000` (Direct Members)\n"
        "• `/scrape_active @group 1000` (Search History/Hidden)\n\n"
        "**Messaging (5 Slots):**\n"
        "• `/setmsg1 Text...` (Upto 5 different messages)\n"
        "• `/speed 15` (Fast/Safe setting)\n"
        "• `/send` (Worker start karein)\n\n"
        "**Data:**\n"
        "• `/dump` (Download & Server se Delete)\n"
        "• `/status` | `/stop` | `/sync`"
    )
    await m.reply(text)

@app.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
async def set_msgs(c, m):
    idx = int(m.command[0][-1]) - 1
    if len(m.text.split()) < 2: return await m.reply("Kripya message text bhi likhein.")
    text = m.text.split(None, 1)[1]
    SETTINGS["msgs"][idx] = text
    await m.reply(f"✅ Message slot {idx+1} set ho gaya!")

@app.on_message(filters.command("speed") & filters.user(ADMIN_ID))
async def speed_cmd(c, m):
    try:
        s = int(m.command[1])
        SETTINGS["speed"] = s
        safety = "🟢 Safe" if s >= 15 else "🟡 Moderate" if s >= 10 else "🔴 Dangerous"
        await m.reply(f"⏱ Speed: {s}s\nStatus: {safety}\n(Kam speed se ID jaldi ban ho sakti hai)")
    except: await m.reply("Usage: `/speed 15`")

@app.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
async def scrape_history(c, m):
    try:
        _, target, limit = m.text.split()
        await m.reply(f"🔍 `{target}` ki history scan kar raha hoon...")
        data = load_json(USERS_DB)
        count = 0
        async for msg in c.get_chat_history(target, limit=int(limit)):
            if msg.from_user and not msg.from_user.is_bot:
                u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                if not any(u[0] == msg.from_user.id for u in data):
                    data.add(u_info)
                    count += 1
        save_json(USERS_DB, data)
        await m.reply(f"✅ History scan se {count} unique users mile. Total: {len(data)}")
    except Exception as e: await m.reply(f"❌ Error: {e}")

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_normal(c, m):
    try:
        _, target, limit = m.text.split()
        if target.startswith("-") or target.isdigit(): target = int(target)
        await m.reply(f"🔍 Members section se scrape kar raha hoon...")
        data = load_json(USERS_DB)
        count = 0
        async for member in c.get_chat_members(target):
            if count >= int(limit): break
            if not member.user.is_bot:
                u_info = (member.user.id, member.user.username or "N/A", member.user.first_name or "User")
                if not any(u[0] == member.user.id for u in data):
                    data.add(u_info)
                    count += 1
        save_json(USERS_DB, data)
        await m.reply(f"✅ {count} users mile. Total: {len(data)}")
    except Exception as e: await m.reply(f"❌ Error: {e}")

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_worker(c, m):
    if SETTINGS["is_running"]: return await m.reply("⚠️ Bot pehle se chal raha hai!")
    scraped, sent = list(load_json(USERS_DB)), load_json(SENT_DB)
    pending = [u for u in scraped if u[0] not in sent]
    if not pending: return await m.reply("❌ Naya data nahi mila. Pehle `/scrape` karein.")
    
    SETTINGS["is_running"], SETTINGS["success"] = True, 0
    await m.reply(f"🚀 Workers Active! Sending to {len(pending)} users...")

    cli_idx = 0
    for user in pending:
        if not SETTINGS["is_running"]: break
        worker = clients[cli_idx]
        try:
            # 5 mein se random message select karna
            msg_to_send = random.choice(SETTINGS["msgs"])
            await worker.send_message(user[0], f"{msg_to_send}\n\nUser: {user[2]}")
            
            SETTINGS["success"] += 1
            sent.add(user[0])
            save_json(SENT_DB, sent)
            
            # Account rotate karna (Worker Model)
            cli_idx = (cli_idx + 1) % len(clients)
            await asyncio.sleep(SETTINGS["speed"])
        except FloodWait as e:
            await asyncio.sleep(e.value + 5)
            cli_idx = (cli_idx + 1) % len(clients)
        except Exception: continue

    SETTINGS["is_running"] = False
    await m.reply(f"🏁 Task Done! Total: {SETTINGS['success']}")

@app.on_message(filters.command("dump") & filters.user(ADMIN_ID))
async def dump_cmd(c, m):
    # JSON bhejo aur server se delete karo taaki Render crash na ho
    if os.path.exists(USERS_DB):
        await m.reply_document(USERS_DB, caption="All Scraped Users (Formatted)")
        os.remove(USERS_DB)
    if os.path.exists(SENT_DB):
        await m.reply_document(SENT_DB, caption="Sent History (Formatted)")
        os.remove(SENT_DB)
    await m.reply("🗑️ Server space clear kar di gayi hai.")

@app.on_message(filters.command("status") & filters.user(ADMIN_ID))
async def status_cmd(c, m):
    sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
    await m.reply(f"📊 **Bot Status:**\nScraped: {sc}\nAlready Sent: {sn}\nWorkers: {len(clients)}\nSpeed: {SETTINGS['speed']}s")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_history(c, m):
    await m.reply("🔄 Purane chats scan karke history update kar raha hoon...")
    sent = load_json(SENT_DB)
    async for dialog in c.get_dialogs():
        if dialog.chat.type == enums.ChatType.PRIVATE:
            sent.add(dialog.chat.id)
    save_json(SENT_DB, sent)
    await m.reply(f"✅ Sync Done! History size: {len(sent)}")

@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(c, m):
    SETTINGS["is_running"] = False
    await m.reply("🛑 Process stop kar diya gaya.")

# --- EXECUTION ---
def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

async def start_workers():
    for cli in clients:
        try: await cli.start()
        except Exception as e: logger.error(f"Error: {e}")

if __name__ == "__main__":
    Thread(target=run_web).start()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_workers())
    app.run()
