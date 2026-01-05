import json, asyncio, os, random, logging
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted
from flask import Flask
from threading import Thread

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RAW_SESSIONS = os.getenv("SESSIONS", "")
SESSION_LIST = [s.strip() for s in RAW_SESSIONS.split(",") if s.strip()]

# Flask for Render Port Binding
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
    "msgs": ["Hi!", "Hello!", "Hey!", "Greetings!", "Yo!"], 
    "success": 0
}

# Clients/Workers Setup
clients = []
for i, s in enumerate(SESSION_LIST):
    try:
        cli = Client(f"worker_{i}", session_string=s, api_id=API_ID, api_hash=API_HASH)
        clients.append(cli)
        logger.info(f"✅ Worker {i} initialized.")
    except Exception as e:
        logger.error(f"❌ Worker {i} error: {e}")

# Admin app (First worker is used as controller)
app = clients[0] if clients else None

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(list(data), f, indent=4)

def load_json(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:
                d = json.load(f)
                return set(tuple(x) for x in d)
            except: return set()
    return set()

# --- COMMANDS (Only if app is not None) ---
if app:
    @app.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_msg(c, m):
        text = (
            "🔥 **Ultimate Multi-Worker Bot V9**\n\n"
            f"✅ **Workers Online:** {len(clients)}\n"
            f"⏱ **Current Speed:** {SETTINGS['speed']}s\n\n"
            "**Scraping:**\n"
            "• `/scrape @group 1000` (Direct)\n"
            "• `/scrape_active @group 1000` (History Scan)\n\n"
            "**Messaging:**\n"
            "• `/setmsg1 Text...` (Set 5 slots)\n"
            "• `/speed 15` (Fast/Safe setting)\n"
            "• `/send` (Start Workers)\n\n"
            "**Data:**\n"
            "• `/dump` (Download & Delete)\n"
            "• `/status` | `/stop`"
        )
        await m.reply(text)

    @app.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
    async def set_msgs(c, m):
        idx = int(m.command[0][-1]) - 1
        if len(m.text.split()) < 2: return await m.reply("Kripya text likhein.")
        SETTINGS["msgs"][idx] = m.text.split(None, 1)[1]
        await m.reply(f"✅ Message slot {idx+1} set!")

    @app.on_message(filters.command("speed") & filters.user(ADMIN_ID))
    async def speed_cmd(c, m):
        try:
            s = int(m.command[1])
            SETTINGS["speed"] = s
            await m.reply(f"⏱ Speed set to {s}s.")
        except: await m.reply("Usage: `/speed 12`")

    @app.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
    async def scrape_history(c, m):
        try:
            _, target, limit = m.text.split()
            await m.reply(f"🔍 `{target}` history scan chalu...")
            data = load_json(USERS_DB)
            count = 0
            async for msg in c.get_chat_history(target, limit=int(limit)):
                if msg.from_user and not msg.from_user.is_bot:
                    u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                    if not any(u[0] == msg.from_user.id for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Active Users: {count} | Total: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_normal(c, m):
        try:
            _, target, limit = m.text.split()
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply("🔍 Members list scrape chalu...")
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
            await m.reply(f"✅ Scraped {count} users. Total: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @app.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_worker(c, m):
        if SETTINGS["is_running"]: return await m.reply("⚠️ Already running!")
        scraped, sent = list(load_json(USERS_DB)), load_json(SENT_DB)
        pending = [u for u in scraped if u[0] not in sent]
        if not pending: return await m.reply("❌ No data!")
        
        SETTINGS["is_running"], SETTINGS["success"] = True, 0
        await m.reply(f"🚀 Workers started with speed {SETTINGS['speed']}s")

        cli_idx = 0
        for user in pending:
            if not SETTINGS["is_running"]: break
            worker = clients[cli_idx]
            try:
                final_msg = f"{random.choice(SETTINGS['msgs'])}\n\nUser: {user[2]}"
                await worker.send_message(user[0], final_msg)
                SETTINGS["success"] += 1
                sent.add(user[0])
                save_json(SENT_DB, sent)
                cli_idx = (cli_idx + 1) % len(clients)
                await asyncio.sleep(SETTINGS["speed"])
            except FloodWait as e:
                await asyncio.sleep(e.value + 5)
                cli_idx = (cli_idx + 1) % len(clients)
            except Exception: continue

        SETTINGS["is_running"] = False
        await m.reply(f"🏁 Done! Sent: {SETTINGS['success']}")

    @app.on_message(filters.command("dump") & filters.user(ADMIN_ID))
    async def dump_cmd(c, m):
        if os.path.exists(USERS_DB):
            await m.reply_document(USERS_DB, caption="Scraped Data")
            os.remove(USERS_DB)
        if os.path.exists(SENT_DB):
            await m.reply_document(SENT_DB, caption="Sent History")
            os.remove(SENT_DB)
        await m.reply("🗑️ Server data deleted for Render safety.")

    @app.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        await m.reply(f"📊 Stats:\nScraped: {sc}\nSent: {sn}\nWorkers: {len(clients)}")

    @app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        SETTINGS["is_running"] = False
        await m.reply("🛑 Stopped!")

# --- EXECUTION ---
def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

async def start_workers():
    for cli in clients:
        try: await cli.start()
        except: pass

if __name__ == "__main__":
    Thread(target=run_web).start()
    if clients:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(start_workers())
        logger.info(">>> Workers Online.")
        app.run()
    else:
        logger.error(">>> NO SESSIONS FOUND. Bot will not start.")
