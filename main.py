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
                logger.info(f"👑 Worker {i} assigned as Boss (Controller).")
        except Exception as e:
            logger.error(f"❌ Worker {i} failed: {e}")

# Flask for Render
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Worker Engine V12 is Online! ⚡"

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
            "🚀 **Ultimate Multi-Worker V12 (FIXED)**\n\n"
            f"👑 **Controller:** Active\n"
            f"✅ **Total Workers:** {len(workers)}\n"
            f"⏱ **Speed:** {SETTINGS['speed']}s\n\n"
            "📍 `/scrape @group 1000` | `/scrape_active @group 1000`\n"
            "📍 `/setmsg1 Text...` (Upto 5) | `/speed 15`\n"
            "📍 `/send` | `/status` | `/stop` | `/dump` | `/sync`"
        )
        await m.reply(text)

    @boss_client.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
    async def set_msgs(c, m):
        idx = int(m.command[0][-1]) - 1
        if len(m.text.split()) < 2: return await m.reply("Kripya message likhein.")
        SETTINGS["msgs"][idx] = m.text.split(None, 1)[1]
        await m.reply(f"✅ Slot {idx+1} Updated!")

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
            parts = m.text.split()
            if len(parts) < 3: return await m.reply("❌ Format: `/scrape_active @group 1000` ya `/scrape_active -100xxx 1000` ")
            target = parts[1]
            if target.startswith("-") or target.isdigit(): target = int(target)
            limit = int(parts[2])

            await m.reply(f"🔍 `{target}` history scan kar raha hoon...")
            data = load_json(USERS_DB)
            count = 0
            # Numeric ID support fix
            async for msg in c.get_chat_history(target, limit=limit):
                if msg.from_user and not msg.from_user.is_bot:
                    u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                    if not any(u[0] == msg.from_user.id for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Scraped Active: {count} | Total: {len(data)}")
        except Exception as e: 
            await m.reply(f"❌ Error: {str(e)}\n\n*Tip: Agar ID invalid hai toh pehle group join karein.*")

    @boss_client.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_normal(c, m):
        try:
            parts = m.text.split()
            target, limit = parts[1], int(parts[2])
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 `{target}` members list scrape kar raha hoon...")
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
            await m.reply(f"✅ Scraped: {count} | Total: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_worker(c, m):
        if SETTINGS["is_running"]: return await m.reply("⚠️ Running...")
        scraped, sent = list(load_json(USERS_DB)), load_json(SENT_DB)
        pending = [u for u in scraped if u[0] not in sent]
        if not pending: return await m.reply("❌ No data!")
        
        SETTINGS["is_running"], SETTINGS["success"] = True, 0
        await m.reply(f"🚀 Workers started ({len(workers)} accounts)")

        w_idx = 0
        for user in pending:
            if not SETTINGS["is_running"]: break
            try:
                worker = workers[w_idx]
                msg = f"{random.choice(SETTINGS['msgs'])}\n\nUser: {user[2]}"
                await worker.send_message(user[0], msg)
                
                SETTINGS["success"] += 1
                sent.add(user[0]); save_json(SENT_DB, sent)
                
                w_idx = (w_idx + 1) % len(workers)
                await asyncio.sleep(SETTINGS["speed"])
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
                w_idx = (w_idx + 1) % len(workers)
            except Exception: continue

        SETTINGS["is_running"] = False
        await m.reply(f"🏁 Finish! Sent: {SETTINGS['success']}")

    @boss_client.on_message(filters.command("dump") & filters.user(ADMIN_ID))
    async def dump_cmd(c, m):
        for db in [USERS_DB, SENT_DB]:
            if os.path.exists(db):
                await m.reply_document(db)
                os.remove(db)
        await m.reply("🗑️ Server files deleted.")

    @boss_client.on_message(filters.command("sync") & filters.user(ADMIN_ID))
    async def sync_cmd(c, m):
        await m.reply("🔄 Syncing history...")
        sent = load_json(SENT_DB)
        async for dialog in c.get_dialogs():
            if dialog.chat.type == enums.ChatType.PRIVATE:
                sent.add(dialog.chat.id)
        save_json(SENT_DB, sent)
        await m.reply(f"✅ Sync Done! History size: {len(sent)}")

    @boss_client.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        await m.reply(f"📊 Scraped: {sc} | Sent: {sn} | Workers: {len(workers)}")

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
    
    logger.info(">>> Bot is fully started and listening.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
