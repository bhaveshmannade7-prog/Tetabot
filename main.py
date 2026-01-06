import json, asyncio, os, random, logging
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted, PeerIdInvalid
from flask import Flask
from threading import Thread

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "12345678")) # Apni ID dalien

# Workers initialization (10 Strings Support)
workers = []
boss_client = None

for i in range(1, 11):
    session = os.getenv(f"STRING_{i}")
    if session:
        try:
            cli = Client(f"worker_{i}", session_string=session.strip(), api_id=API_ID, api_hash=API_HASH)
            workers.append(cli)
            if boss_client is None:
                boss_client = cli
        except Exception as e:
            logger.error(f"Worker {i} error: {e}")

# Flask for Render
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Worker Engine V14 is Active! ⚡"

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Database Files
USERS_DB, SENT_DB = "scraped_users.json", "sent_history.json"
SETTINGS = {
    "is_running": False,
    "speed": 12,
    "msgs": ["Hi!", "Hello!", "Hey!", "Greetings!", "Yo!"],
    "success": 0
}

# --- JSON HELPERS ---
def save_json(file, data):
    with open(file, "w") as f:
        json.dump(list(data), f, indent=4) # Clean formatting

def load_json(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:
                d = json.load(f)
                return set(tuple(x) for x in d)
            except: return set()
    return set()

# --- MESSAGING CORE LOOP ---
async def start_messaging_task():
    """Alag task jo messaging handle karega bina bot ko block kiye"""
    scraped = list(load_json(USERS_DB))
    sent = load_json(SENT_DB)
    pending = [u for u in scraped if u[0] not in sent]
    
    if not pending:
        SETTINGS["is_running"] = False
        try: await boss_client.send_message(ADMIN_ID, "❌ Bhejne ke liye koi naya data nahi mila.")
        except: pass
        return

    w_idx = 0
    for user in pending:
        if not SETTINGS["is_running"]: break
        
        try:
            worker = workers[w_idx]
            msg_text = f"{random.choice(SETTINGS['msgs'])}\n\nUser: {user[2]}"
            
            # Send message
            await worker.send_message(user[0], msg_text)
            
            # Update data
            SETTINGS["success"] += 1
            sent.add(user[0])
            save_json(SENT_DB, sent)
            
            # Rotate worker
            w_idx = (w_idx + 1) % len(workers)
            
            # Speed + Random Buffer
            await asyncio.sleep(SETTINGS["speed"] + random.uniform(1, 4))

        except FloodWait as e:
            logger.info(f"FloodWait: {e.value}s. Skipping this worker.")
            w_idx = (w_idx + 1) % len(workers)
            await asyncio.sleep(2)
        except (PeerFlood, UserPrivacyRestricted, PeerIdInvalid):
            continue
        except Exception as e:
            logger.error(f"General error: {e}")
            continue

    SETTINGS["is_running"] = False
    try: await boss_client.send_message(ADMIN_ID, f"🏁 Campaign Finished! Total: {SETTINGS['success']}")
    except: pass

# --- BOT COMMANDS (Boss Only) ---
if boss_client:
    @boss_client.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(c, m):
        await m.reply(f"🚀 **Multi-Worker V14 Active**\nWorkers: {len(workers)}\n\n`/scrape`, `/scrape_active`, `/send`, `/status`, `/speed`, `/dump`")

    @boss_client.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
    async def set_msgs(c, m):
        try:
            idx = int(m.command[0][-1]) - 1
            SETTINGS["msgs"][idx] = m.text.split(None, 1)[1]
            await m.reply(f"✅ Slot {idx+1} Updated.")
        except: await m.reply("Usage: `/setmsg1 Hello User`")

    @boss_client.on_message(filters.command("speed") & filters.user(ADMIN_ID))
    async def speed_cmd(c, m):
        try:
            s = int(m.command[1])
            SETTINGS["speed"] = s
            await m.reply(f"⏱ Speed: {s}s")
        except: pass

    @boss_client.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_cmd(c, m):
        if SETTINGS["is_running"]: return await m.reply("⚠️ Already running!")
        SETTINGS["is_running"] = True
        SETTINGS["success"] = 0
        await m.reply(f"🚀 Workers started with {len(workers)} accounts.")
        asyncio.create_task(start_messaging_task()) # Task creation fix

    @boss_client.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
    async def scrape_active_cmd(c, m):
        try:
            parts = m.text.split()
            target, limit = parts[1], int(parts[2])
            if target.startswith("-") or target.isdigit(): target = int(target)
            
            await m.reply(f"🔍 `{target}` scanning history...")
            data = load_json(USERS_DB)
            count = 0
            async for msg in c.get_chat_history(target, limit=limit):
                if msg.from_user and not msg.from_user.is_bot:
                    u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                    if not any(u[0] == msg.from_user.id for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Scraped Active: {count}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_normal(c, m):
        try:
            parts = m.text.split()
            target, limit = parts[1], int(parts[2])
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 `{target}` scraping members...")
            data = load_json(USERS_DB)
            count = 0
            async for member in c.get_chat_members(target):
                if count >= limit: break
                if not member.user.is_bot:
                    u_info = (member.user.id, member.user.username or "N/A", member.user.first_name or "User")
                    if not any(u[0] == member.user.id for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Scraped: {count}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("dump") & filters.user(ADMIN_ID))
    async def dump_cmd(c, m):
        for db in [USERS_DB, SENT_DB]:
            if os.path.exists(db):
                await m.reply_document(db)
                os.remove(db)
        await m.reply("🗑️ Server space cleared.")

    @boss_client.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        await m.reply(f"📊 Stats:\nScraped: {sc}\nSent: {sn}\nWorkers: {len(workers)}\nRunning: {SETTINGS['is_running']}")

    @boss_client.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        SETTINGS["is_running"] = False
        await m.reply("🛑 Stopped.")

# --- RUNNER ---
async def main():
    Thread(target=run_web).start()
    if not workers:
        logger.error("No strings found.")
        return

    for cli in workers:
        await cli.start()
        try: await cli.send_message(ADMIN_ID, f"✅ Worker {cli.name} is Online!")
        except: pass
    
    logger.info(">>> Bot is fully started.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
