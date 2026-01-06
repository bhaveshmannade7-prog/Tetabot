import json, asyncio, os, random, logging
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted, PeerIdInvalid
from flask import Flask
from threading import Thread

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Workers initialization (Up to 10 strings)
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
            logger.error(f"❌ Worker {i} failed: {e}")

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

# --- HELPERS ---
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

# --- STABLE MESSAGING ENGINE ---
async def persistent_sender(c):
    """Ye function loop ko kabhi rukne nahi dega"""
    scraped = list(load_json(USERS_DB))
    sent = load_json(SENT_DB)
    # Filter only non-sent users
    pending = [u for u in scraped if u[0] not in sent]
    
    if not pending:
        SETTINGS["is_running"] = False
        await c.send_message(ADMIN_ID, "❌ Bhejne ke liye koi naya data nahi mila.")
        return

    await c.send_message(ADMIN_ID, f"🚀 Campaign Started! Target: {len(pending)} users.")
    
    w_idx = 0
    for user in pending:
        if not SETTINGS["is_running"]: 
            break
        
        try:
            worker = workers[w_idx]
            # Personalization & Random Msg selection
            msg_text = f"{random.choice(SETTINGS['msgs'])}\n\nUser: {user[2]}"
            
            await worker.send_message(user[0], msg_text)
            
            # Update DB immediately
            SETTINGS["success"] += 1
            sent.add(user[0])
            save_json(SENT_DB, sent)
            
            # Rotate worker for next message
            w_idx = (w_idx + 1) % len(workers)
            
            # Dynamic Delay to break patterns
            delay = SETTINGS["speed"] + random.uniform(1, 4)
            await asyncio.sleep(delay)

        except FloodWait as e:
            logger.warning(f"FloodWait: {e.value}s. Switching worker.")
            await asyncio.sleep(5) # Chota break
            w_idx = (w_idx + 1) % len(workers)
            continue
        except (PeerFlood, UserPrivacyRestricted, PeerIdInvalid):
            logger.info(f"Skipping restricted user: {user[0]}")
            continue
        except Exception as e:
            logger.error(f"Error: {e}")
            await asyncio.sleep(2)
            continue

    SETTINGS["is_running"] = False
    await c.send_message(ADMIN_ID, f"🏁 Campaign Finished!\n✅ Sent: {SETTINGS['success']}")

# --- COMMAND HANDLERS ---
if boss_client:
    @boss_client.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(c, m):
        text = (
            "🔥 **Multi-Worker V14 (PRO-BUILD)**\n\n"
            f"👑 **Boss:** STRING_1\n"
            f"✅ **Workers:** {len(workers)}\n"
            f"⏱ **Speed:** {SETTINGS['speed']}s\n\n"
            "📍 `/scrape @group 1000` | `/scrape_active @group 1000`\n"
            "📍 `/setmsg1 Text...` (Upto 5 Slots)\n"
            "📍 `/speed 15` (Change delay)\n"
            "📍 `/send` | `/status` | `/stop` | `/dump` | `/sync`"
        )
        await m.reply(text)

    @boss_client.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
    async def set_msgs(c, m):
        try:
            idx = int(m.command[0][-1]) - 1
            SETTINGS["msgs"][idx] = m.text.split(None, 1)[1]
            await m.reply(f"✅ Slot {idx+1} Updated.")
        except: await m.reply("❌ Format: `/setmsg1 Hello User` ")

    @boss_client.on_message(filters.command("speed") & filters.user(ADMIN_ID))
    async def speed_cmd(c, m):
        try:
            s = int(m.command[1])
            SETTINGS["speed"] = s
            await m.reply(f"⏱ Speed: {s}s")
        except: pass

    @boss_client.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
    async def scrape_history(c, m):
        try:
            _, target, limit = m.text.split()
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 `{target}` Scanning History...")
            data = load_json(USERS_DB)
            count = 0
            async for msg in c.get_chat_history(target, limit=int(limit)):
                if msg.from_user and not msg.from_user.is_bot:
                    u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                    if not any(u[0] == msg.from_user.id for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Scraped Active: {count} | Total DB: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_normal(c, m):
        try:
            _, target, limit = m.text.split()
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 `{target}` Scraping Members...")
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
            await m.reply(f"✅ Scraped: {count} | Total DB: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_worker_cmd(c, m):
        if SETTINGS["is_running"]: return await m.reply("⚠️ Campaign already running!")
        SETTINGS["is_running"] = True
        SETTINGS["success"] = 0
        # Background task trigger
        asyncio.create_task(persistent_sender(c))

    @boss_client.on_message(filters.command("dump") & filters.user(ADMIN_ID))
    async def dump_cmd(c, m):
        sent_any = False
        for db in [USERS_DB, SENT_DB]:
            if os.path.exists(db):
                await m.reply_document(db)
                os.remove(db)
                sent_any = True
        if sent_any: await m.reply("🗑️ Server space cleared.")
        else: await m.reply("❌ No files found.")

    @boss_client.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        await m.reply(f"📊 **Live Stats:**\nScraped: {sc}\nSent: {sn}\nPending: {sc-sn}\nWorkers: {len(workers)}\nRunning: {SETTINGS['is_running']}")

    @boss_client.on_message(filters.command("sync") & filters.user(ADMIN_ID))
    async def sync_cmd(c, m):
        await m.reply("🔄 Updating Sent History...")
        sent = load_json(SENT_DB)
        async for dialog in c.get_dialogs():
            if dialog.chat.type == enums.ChatType.PRIVATE:
                sent.add(dialog.chat.id)
        save_json(SENT_DB, sent)
        await m.reply(f"✅ Sync Done! History size: {len(sent)}")

    @boss_client.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        SETTINGS["is_running"] = False
        await m.reply("🛑 Stopping messaging task...")

# --- RUNNER ---
async def main():
    Thread(target=run_web).start()
    if not workers:
        logger.error("No strings found. Check Environment Variables.")
        return

    # Start all workers concurrently
    start_tasks = [cli.start() for cli in workers]
    await asyncio.gather(*start_tasks)
    
    # Startup Alert
    try: await boss_client.send_message(ADMIN_ID, "🚀 **Bot is Online & Ready!**\nSend `/start` to begin.")
    except: pass
    
    logger.info(">>> Engine Started.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
