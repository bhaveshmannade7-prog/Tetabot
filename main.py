import json, asyncio, os, random, logging
from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, PeerFlood, UserPrivacyRestricted, 
    PeerIdInvalid, UserIsBlocked, InputUserDeactivated, 
    UsernameNotOccupied, UserBannedInChannel
)
from flask import Flask
from threading import Thread

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Workers initialization
workers = []
boss_client = None

# Load up to 10 workers
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

# Flask for Render/Uptime
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Worker Engine V15 (Fixed) is Active! ⚡"

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Database Files
USERS_DB, SENT_DB = "scraped_users.json", "sent_history.json"
SETTINGS = {
    "is_running": False,
    "speed": 10, # Default speed slightly faster
    "msgs": ["Hi!", "Hello!", "Hey!", "Greetings!", "Yo!"],
    "success": 0
}

# --- HELPERS ---
def save_json(file, data):
    try:
        with open(file, "w") as f:
            json.dump(list(data), f, indent=4)
    except Exception as e:
        logger.error(f"Save Error: {e}")

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
    """
    Fixed Function: Handles PeerIdInvalid by switching to Username.
    Prevents loop stalling.
    """
    scraped = list(load_json(USERS_DB))
    sent = load_json(SENT_DB)
    
    # Filter only non-sent users
    pending = [u for u in scraped if u[0] not in sent]
    
    if not pending:
        SETTINGS["is_running"] = False
        await c.send_message(ADMIN_ID, "❌ Bhejne ke liye koi naya data nahi mila.")
        return

    await c.send_message(ADMIN_ID, f"🚀 **Campaign Started!**\n🎯 Target: {len(pending)} users\n🤖 Workers: {len(workers)}")
    
    w_idx = 0
    consecutive_errors = 0 # To detect if all workers are dead

    for user in pending:
        if not SETTINGS["is_running"]: 
            break
        
        user_id = user[0]
        username = user[1]
        user_name = user[2]
        
        # Check sending history again to be safe
        if user_id in sent:
            continue

        try:
            worker = workers[w_idx]
            msg_text = f"{random.choice(SETTINGS['msgs'])}\n\nUser: {user_name}"
            
            # --- SENDING LOGIC WITH FALLBACK ---
            try:
                # 1. Try sending by ID (Fastest)
                await worker.send_message(user_id, msg_text)
            
            except PeerIdInvalid:
                # 2. Worker doesn't know ID -> Try Username
                if username and username != "N/A":
                    # logger.info(f"Retrying with Username for {user_id}")
                    await worker.send_message(username, msg_text)
                else:
                    # No username to fallback on
                    raise Exception("PeerIdInvalid & No Username")
            
            # --- SUCCESS ---
            SETTINGS["success"] += 1
            sent.add(user_id)
            save_json(SENT_DB, sent)
            consecutive_errors = 0 # Reset error count
            
            # Rotate worker
            w_idx = (w_idx + 1) % len(workers)
            
            # Dynamic Delay
            delay = SETTINGS["speed"] + random.uniform(2, 5)
            await asyncio.sleep(delay)

        except FloodWait as e:
            logger.warning(f"⏳ FloodWait: {e.value}s on Worker {w_idx}. Sleeping...")
            await asyncio.sleep(e.value + 2) # Wait out the flood
            w_idx = (w_idx + 1) % len(workers) # Switch worker
            continue
            
        except (UserPrivacyRestricted, UserIsBlocked, InputUserDeactivated, UserBannedInChannel):
            # These are dead ends, mark as sent to avoid retrying
            sent.add(user_id)
            # logger.info(f"Skipping Dead/Restricted User: {user_id}")
            continue

        except Exception as e:
            # Generic error (PeerIdInvalid final fallback, etc)
            logger.error(f"❌ Failed User {user_id}: {e}")
            consecutive_errors += 1
            
            # If too many errors in a row, slightly increase delay to protect account
            if consecutive_errors > 5:
                await asyncio.sleep(5)
            
            # Rotate worker and continue loop
            w_idx = (w_idx + 1) % len(workers)
            continue

    SETTINGS["is_running"] = False
    save_json(SENT_DB, sent) # Final save
    await c.send_message(ADMIN_ID, f"🏁 **Campaign Finished!**\n✅ Successfully Sent: {SETTINGS['success']}")

# --- COMMAND HANDLERS ---
if boss_client:
    @boss_client.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(c, m):
        text = (
            "🔥 **Multi-Worker V15 (FIXED)**\n\n"
            f"👑 **Boss:** STRING_1\n"
            f"✅ **Workers:** {len(workers)}\n"
            f"⏱ **Speed:** {SETTINGS['speed']}s\n\n"
            "📍 `/scrape @group 1000`\n"
            "📍 `/scrape_active @group 1000`\n"
            "📍 `/setmsg1 Text...` (Upto 5 Slots)\n"
            "📍 `/speed 10`\n"
            "📍 `/send` - Start Campaign\n"
            "📍 `/status` - Check Progress\n"
            "📍 `/stop` - Stop Campaign"
        )
        await m.reply(text)

    @boss_client.on_message(filters.command(["setmsg1", "setmsg2", "setmsg3", "setmsg4", "setmsg5"]) & filters.user(ADMIN_ID))
    async def set_msgs(c, m):
        try:
            if len(m.command) < 2: return await m.reply("❌ Text required!")
            idx = int(m.command[0][-1]) - 1
            SETTINGS["msgs"][idx] = m.text.split(None, 1)[1]
            await m.reply(f"✅ Slot {idx+1} Updated.")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("speed") & filters.user(ADMIN_ID))
    async def speed_cmd(c, m):
        try:
            s = int(m.command[1])
            SETTINGS["speed"] = s
            await m.reply(f"⏱ Speed set to: {s}s")
        except: await m.reply("❌ Use: `/speed 10`")

    @boss_client.on_message(filters.command("scrape_active") & filters.user(ADMIN_ID))
    async def scrape_history(c, m):
        try:
            _, target, limit = m.text.split()
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 `{target}` Scanning Active Users...")
            data = load_json(USERS_DB)
            count = 0
            async for msg in c.get_chat_history(target, limit=int(limit)):
                if msg.from_user and not msg.from_user.is_bot:
                    # Save: ID, Username, FirstName
                    u_info = (msg.from_user.id, msg.from_user.username or "N/A", msg.from_user.first_name or "User")
                    # Check duplication by ID
                    if not any(u[0] == u_info[0] for u in data):
                        data.add(u_info)
                        count += 1
            save_json(USERS_DB, data)
            await m.reply(f"✅ Active Scraped: {count} | Total DB: {len(data)}")
        except Exception as e: await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_normal(c, m):
        try:
            _, target, limit = m.text.split()
            if target.startswith("-") or target.isdigit(): target = int(target)
            await m.reply(f"🔍 `{target}` Scraping Members...")
            data = load_json(USERS_DB)
            count = 0
            async for member in c.get_chat_members(target, limit=int(limit)):
                if not member.user.is_bot:
                    u_info = (member.user.id, member.user.username or "N/A", member.user.first_name or "User")
                    if not any(u[0] == u_info[0] for u in data):
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
        asyncio.create_task(persistent_sender(c))
        await m.reply("✅ Task Started in Background!")

    @boss_client.on_message(filters.command("dump") & filters.user(ADMIN_ID))
    async def dump_cmd(c, m):
        for db in [USERS_DB, SENT_DB]:
            if os.path.exists(db):
                await m.reply_document(db)
        await m.reply("📂 Database Dumped.")

    @boss_client.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        msg = (
            f"📊 **Status Report**\n"
            f"🟢 Running: {SETTINGS['is_running']}\n"
            f"👥 Total Scraped: {sc}\n"
            f"✅ Total Sent: {sn}\n"
            f"⏳ Pending: {sc - sn}\n"
            f"👷 Workers Active: {len(workers)}"
        )
        await m.reply(msg)

    @boss_client.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        SETTINGS["is_running"] = False
        await m.reply("🛑 Stopping... (Allow few seconds to finish current tasks)")

    @boss_client.on_message(filters.command("clean") & filters.user(ADMIN_ID))
    async def clean_db(c, m):
        try:
            if os.path.exists(USERS_DB): os.remove(USERS_DB)
            if os.path.exists(SENT_DB): os.remove(SENT_DB)
            await m.reply("🗑️ All Databases Cleared!")
        except: await m.reply("❌ Error cleaning.")

# --- RUNNER ---
async def main():
    Thread(target=run_web).start()
    if not workers:
        logger.error("❌ No Strings Found! Add STRING_1, STRING_2 etc in Vars.")
        return

    logger.info(">>> Starting Workers...")
    await asyncio.gather(*[cli.start() for cli in workers])
    
    try: await boss_client.send_message(ADMIN_ID, "🚀 **Bot Restarted & Ready!**")
    except: pass
    
    logger.info(">>> Engine Active.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
