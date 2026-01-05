import json, asyncio, os, random, logging
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted, PeerIdInvalid
from flask import Flask
from threading import Thread

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Workers initialization
workers = []
boss_client = None

for i in range(1, 11):
    session = os.getenv(f"STRING_{i}")
    if session:
        try:
            name = f"worker_{i}"
            cli = Client(name, session_string=session.strip(), api_id=API_ID, api_hash=API_HASH)
            workers.append(cli)
            logger.info(f"✅ Worker {i} loaded.")
            if boss_client is None:
                boss_client = cli
                logger.info(f"👑 Worker {i} assigned as Boss.")
        except Exception as e:
            logger.error(f"❌ Worker {i} failed: {e}")

# Flask for Render
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Worker Engine V13 is Online! ⚡"

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Database
USERS_DB, SENT_DB = "scraped_users.json", "sent_history.json"
SETTINGS = {
    "is_running": False,
    "speed": 12,
    "msgs": ["Hi!", "Hello!", "Hey!", "Greetings!", "Yo!"],
    "success": 0
}

def save_json(file, data):
    # Convert set of tuples to list of lists for JSON
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

# --- BOT LOGIC (Boss Only) ---
if boss_client:
    @boss_client.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(c, m):
        text = (
            "🚀 **Ultimate Multi-Worker V13**\n\n"
            f"✅ **Total Workers:** {len(workers)}\n"
            f"⏱ **Current Speed:** {SETTINGS['speed']}s\n\n"
            "📍 `/scrape` | `/scrape_active` | `/send`\n"
            "📍 `/status` | `/stop` | `/dump` | `/sync`"
        )
        await m.reply(text)

    @boss_client.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
    async def set_msgs(c, m):
        idx = int(m.command[0][-1]) - 1
        if len(m.text.split()) < 2: return await m.reply("Kripya message likhein.")
        SETTINGS["msgs"][idx] = m.text.split(None, 1)[1]
        await m.reply(f"✅ Slot {idx+1} Updated!")

    @boss_client.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
    async def scrape_history(c, m):
        try:
            parts = m.text.split()
            target, limit = parts[1], int(parts[2])
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 Scrape chalu: `{target}`")
            data = load_json(USERS_DB)
            count = 0
            async for msg in c.get_chat_history(target, limit=limit):
                if msg.from_user and not msg.from_user.is_bot:
                    u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                    if not any(u[0] == msg.from_user.id for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Scraped: {count} | Total: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_worker(c, m):
        if SETTINGS["is_running"]: return await m.reply("⚠️ Already running!")
        
        scraped = load_json(USERS_DB)
        sent_history = load_json(SENT_DB)
        # Extract user IDs for easy checking
        sent_ids = {u[0] for u in sent_history}
        pending = [u for u in scraped if u[0] not in sent_ids]

        if not pending: return await m.reply("❌ No pending users to send!")

        SETTINGS["is_running"] = True
        SETTINGS["success"] = 0
        await m.reply(f"🚀 Workers started for {len(pending)} users...")

        w_idx = 0
        for user in pending:
            if not SETTINGS["is_running"]: break
            
            # Select Worker
            worker = workers[w_idx]
            user_id, user_name, first_name = user
            
            try:
                # Random Message selection
                base_msg = random.choice(SETTINGS["msgs"])
                await worker.send_message(user_id, f"{base_msg}")
                
                # Update stats and history
                SETTINGS["success"] += 1
                sent_history.add((user_id, user_name, first_name))
                save_json(SENT_DB, sent_history)
                
                # Rotate worker index
                w_idx = (w_idx + 1) % len(workers)
                
                # Delay
                await asyncio.sleep(SETTINGS["speed"])

            except FloodWait as e:
                logger.warning(f"FloodWait on {worker.name}: {e.value}s")
                await asyncio.sleep(e.value + 1)
                w_idx = (w_idx + 1) % len(workers)
            except (PeerFlood, UserPrivacyRestricted):
                w_idx = (w_idx + 1) % len(workers)
                continue
            except Exception as e:
                logger.error(f"Error sending to {user_id}: {e}")
                w_idx = (w_idx + 1) % len(workers)
                continue

        SETTINGS["is_running"] = False
        await m.reply(f"🏁 Finish! Sent to: {SETTINGS['success']} users.")

    @boss_client.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc = len(load_json(USERS_DB))
        sn = len(load_json(SENT_DB))
        await m.reply(f"📊 Stats:\n- Scraped: {sc}\n- Sent: {sn}\n- Workers: {len(workers)}")

    @boss_client.on_message(filters.command("dump") & filters.user(ADMIN_ID))
    async def dump_cmd(c, m):
        for db in [USERS_DB, SENT_DB]:
            if os.path.exists(db):
                await m.reply_document(db)
                os.remove(db)
        await m.reply("🗑️ Cleared.")

    @boss_client.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        SETTINGS["is_running"] = False
        await m.reply("🛑 Stopped!")

# --- EXECUTION ---
async def start_all():
    Thread(target=run_web).start()
    for cli in workers:
        await cli.start()
        try: await cli.send_message(ADMIN_ID, f"✅ {cli.name} Ready")
        except: pass
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(start_all())
