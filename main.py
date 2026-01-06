import json, asyncio, os, random, logging, time
from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, PeerFlood, UserPrivacyRestricted, 
    PeerIdInvalid, UserIsBlocked, InputUserDeactivated, 
    UsernameNotOccupied, UserBannedInChannel, UsernameInvalid
)
from flask import Flask
from threading import Thread

# --- LOGGING SETUP (Clean & Detailed) ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)
logger = logging.getLogger("MultiBot")

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# --- GLOBAL VARIABLES ---
workers = []
boss_client = None
USERS_DB = "users_db.json"
SENT_DB = "sent_db.json"

SETTINGS = {
    "is_running": False,
    "speed": 15,          # Safe delay for different accounts
    "batch_size": 10,     # Break after 10 messages
    "msgs": ["Hello!", "Hi there!", "Greetings!"], # Default messages
    "mode": "username",   # 'username' (Safe) or 'all' (Risky)
    "total_sent": 0
}

# --- FLASK SERVER (For Render Keep-Alive) ---
web_app = Flask(__name__)

@web_app.route('/')
def home(): 
    return "🔥 Ultimate Bot V17 (Diff-Group Edition) is Running!"

@web_app.route('/health')
def health():
    return "OK", 200

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# --- DATABASE MANAGEMENT ---
def load_db(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except: return []
    return []

def save_db(filename, data):
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Save Error: {e}")

# --- WORKER SETUP ---
async def init_clients():
    global boss_client
    logger.info("🔌 Connecting Workers...")
    
    # Load strings from Environment (STRING_1 to STRING_10)
    for i in range(1, 11):
        session = os.getenv(f"STRING_{i}")
        if session:
            try:
                cli = Client(f"worker_{i}", session_string=session.strip(), api_id=API_ID, api_hash=API_HASH)
                await cli.start()
                workers.append(cli)
                logger.info(f"✅ Worker {i} Connected")
                
                # First valid client becomes Boss (for scraping)
                if boss_client is None:
                    boss_client = cli
            except Exception as e:
                logger.error(f"❌ Worker {i} Fail: {e}")

# --- MAIN SENDER ENGINE ---
async def sender_engine():
    """
    Core Logic: 
    1. Loads Users
    2. Filters those not sent
    3. Picks a worker
    4. Sends message (Prioritizing Usernames)
    """
    logger.info("🚀 Sender Engine Started")
    
    # Load Data
    all_users = load_db(USERS_DB) # List of [id, username, name]
    sent_users = set(load_db(SENT_DB)) # Set of IDs
    
    # Filter Pending
    pending = [u for u in all_users if str(u[0]) not in sent_users]
    
    if not pending:
        SETTINGS["is_running"] = False
        if boss_client:
            await boss_client.send_message(ADMIN_ID, "⚠️ **Task Completed or Empty DB!**\nNo new users to message.")
        return

    if boss_client:
        await boss_client.send_message(ADMIN_ID, f"📢 **Campaign Started**\n👥 Target: {len(pending)}\n🤖 Workers: {len(workers)}\n🚀 Mode: {SETTINGS['mode']}")

    w_idx = 0
    batch_count = 0
    
    for user in pending:
        if not SETTINGS["is_running"]: break
        
        user_id = str(user[0])
        username = user[1]
        first_name = user[2]
        
        # Double check history
        if user_id in sent_users: continue

        worker = workers[w_idx]
        msg_text = f"{random.choice(SETTINGS['msgs'])}\n\n"
        # Optional: Add Name personalization if needed -> f"Hello {first_name},"

        try:
            sent_success = False
            
            # --- STRATEGY: USERNAME FIRST ---
            if username and username != "N/A":
                try:
                    await worker.send_message(username, msg_text)
                    sent_success = True
                    logger.info(f"✅ Sent to @{username} via Worker {w_idx+1}")
                except (PeerIdInvalid, UsernameInvalid, UsernameNotOccupied):
                    logger.warning(f"⚠️ Username @{username} invalid/changed.")
            
            # --- STRATEGY: ID FALLBACK (Only if Mode is ALL) ---
            elif SETTINGS["mode"] == "all":
                # Yeh tabhi chalega jab Worker same group me ho
                try:
                    await worker.send_message(int(user_id), msg_text)
                    sent_success = True
                    logger.info(f"✅ Sent to ID {user_id} via Worker {w_idx+1}")
                except PeerIdInvalid:
                    logger.info(f"⏩ Skipped {user_id} (Worker {w_idx+1} not in group)")
                    # Do not mark as sent, maybe another worker is in that group? 
                    # For now, we skip to avoid errors.
            
            else:
                logger.info(f"⏩ Skipped {user_id} (No Username & Safe Mode ON)")

            # --- POST SEND ACTIONS ---
            if sent_success:
                SETTINGS["total_sent"] += 1
                sent_users.add(user_id)
                # Save periodically every 5 users
                if len(sent_users) % 5 == 0:
                    save_db(SENT_DB, list(sent_users))
                
                # Small Sleep between messages
                await asyncio.sleep(random.uniform(SETTINGS["speed"], SETTINGS["speed"] + 5))
            else:
                # If failed/skipped, just wait a tiny bit
                await asyncio.sleep(1)

        except FloodWait as e:
            logger.warning(f"🌊 FloodWait {e.value}s on Worker {w_idx+1}")
            await asyncio.sleep(e.value + 5)
            w_idx = (w_idx + 1) % len(workers) # Switch worker immediately
            
        except (UserPrivacyRestricted, UserIsBlocked, InputUserDeactivated, UserBannedInChannel):
            sent_users.add(user_id) # Mark as dead to avoid retry
            
        except Exception as e:
            logger.error(f"❌ Unknown Error: {e}")
            await asyncio.sleep(5)

        # Worker Rotation
        w_idx = (w_idx + 1) % len(workers)
        
        # Batch Break (To rest accounts)
        batch_count += 1
        if batch_count >= SETTINGS["batch_size"]:
            batch_count = 0
            # logger.info("😴 Taking a short nap...")
            await asyncio.sleep(10)

    # Final Save
    save_db(SENT_DB, list(sent_users))
    SETTINGS["is_running"] = False
    if boss_client:
        await boss_client.send_message(ADMIN_ID, f"🏁 **Task Finished!**\n✅ Total Sent: {SETTINGS['total_sent']}")

# --- COMMANDS ---

async def setup_commands():
    if not boss_client: return

    @boss_client.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(c, m):
        txt = (
            "🤖 **Ultra-Bot V17 (Diff-Group Pro)**\n"
            "--------------------------------\n"
            "1️⃣ `/scrape @group 500` - (Recommends: Public Groups)\n"
            "2️⃣ `/mode username` - (Best for diff groups)\n"
            "3️⃣ `/addmsg Hello...` - (Add template)\n"
            "4️⃣ `/send` - (Start Campaign)\n"
            "5️⃣ `/status` - (Live Stats)\n"
            "6️⃣ `/stop` - (Emergency Stop)\n"
            "7️⃣ `/clean` - (Delete Data)\n"
            "--------------------------------\n"
            f"⚡ **Workers:** {len(workers)} | 🐢 **Speed:** {SETTINGS['speed']}s"
        )
        await m.reply(txt)

    @boss_client.on_message(filters.command("mode") & filters.user(ADMIN_ID))
    async def mode_cmd(c, m):
        # Switch between sending to Everyone (Risky for diff groups) or Usernames only
        try:
            mode = m.command[1].lower()
            if mode in ["username", "all"]:
                SETTINGS["mode"] = mode
                await m.reply(f"✅ Mode set to: **{mode.upper()}**\n(Username mode is safest for unconnected workers)")
            else:
                await m.reply("❌ Use: `/mode username` or `/mode all`")
        except: await m.reply(f"Current Mode: **{SETTINGS['mode']}**")

    @boss_client.on_message(filters.command("addmsg") & filters.user(ADMIN_ID))
    async def add_msg_cmd(c, m):
        if len(m.command) < 2: return await m.reply("❌ Text required!")
        text = m.text.split(None, 1)[1]
        SETTINGS["msgs"].append(text)
        await m.reply(f"✅ Message Added! Total Templates: {len(SETTINGS['msgs'])}")

    @boss_client.on_message(filters.command("viewmsgs") & filters.user(ADMIN_ID))
    async def view_msgs(c, m):
        if not SETTINGS["msgs"]: return await m.reply("❌ No messages set.")
        txt = "**Current Messages:**\n\n"
        for i, msg in enumerate(SETTINGS["msgs"]):
            txt += f"{i+1}. {msg}\n---\n"
        await m.reply(txt)

    @boss_client.on_message(filters.command("resetmsgs") & filters.user(ADMIN_ID))
    async def reset_msgs(c, m):
        SETTINGS["msgs"] = []
        await m.reply("🗑️ All messages cleared. Add new ones with `/addmsg`")

    @boss_client.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_cmd(c, m):
        try:
            if len(m.command) < 3: return await m.reply("❌ Usage: `/scrape @group 1000`")
            _, target, limit = m.text.split()
            
            await m.reply(f"🔍 Scraping `{target}`... (Prioritizing Usernames)")
            
            current_db = load_db(USERS_DB)
            # Convert to dict to prevent duplicates efficiently
            existing_ids = {u[0] for u in current_db}
            new_users = []
            
            count = 0
            limit = int(limit)
            
            async for member in c.get_chat_members(target, limit=limit):
                if member.user.is_bot: continue
                
                uid = member.user.id
                uname = member.user.username or "N/A"
                fname = member.user.first_name or "User"
                
                # Check duplication
                if uid not in existing_ids:
                    # Append logic: [id, username, name]
                    # Important: We prioritize Usernames. 
                    if SETTINGS["mode"] == "username" and uname == "N/A":
                        continue # Skip users without username in Strict mode
                        
                    new_users.append([uid, uname, fname])
                    existing_ids.add(uid)
                    count += 1
            
            # Merge and Save
            final_db = current_db + new_users
            save_db(USERS_DB, final_db)
            
            await m.reply(f"✅ **Scrape Done!**\n🆕 New Found: {count}\n📚 Total DB: {len(final_db)}")
            
        except Exception as e:
            await m.reply(f"❌ Error: {e}")

    @boss_client.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_cmd(c, m):
        if SETTINGS["is_running"]: return await m.reply("⚠️ Already running!")
        if not os.path.exists(USERS_DB): return await m.reply("❌ No Database! Scrape first.")
        
        SETTINGS["is_running"] = True
        SETTINGS["total_sent"] = 0
        asyncio.create_task(sender_engine())
        await m.reply("🚀 **Campaign Started in Background!**")

    @boss_client.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        SETTINGS["is_running"] = False
        await m.reply("🛑 Stopping Engine...")

    @boss_client.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        total = len(load_db(USERS_DB))
        sent = len(load_db(SENT_DB))
        txt = (
            f"📊 **Live Status**\n"
            f"🟢 Running: `{SETTINGS['is_running']}`\n"
            f"👥 DB Size: `{total}`\n"
            f"✅ Sent History: `{sent}`\n"
            f"⏳ Pending: `{total - sent}`\n"
            f"⚙️ Mode: `{SETTINGS['mode']}`\n"
            f"🤖 Workers: `{len(workers)}`"
        )
        await m.reply(txt)

    @boss_client.on_message(filters.command("clean") & filters.user(ADMIN_ID))
    async def clean_cmd(c, m):
        if os.path.exists(USERS_DB): os.remove(USERS_DB)
        if os.path.exists(SENT_DB): os.remove(SENT_DB)
        await m.reply("🗑️ **Database Wiped!** Ready for new group.")

# --- RUNNER ---
async def main():
    # Start Web Server
    Thread(target=run_web).start()
    
    # Start Clients
    await init_clients()
    
    if not workers:
        logger.error("❌ No Workers found! Check ENV variables.")
        return

    # Setup Commands
    await setup_commands()
    
    logger.info(">>> Bot is Ready and Idle.")
    try:
        await boss_client.send_message(ADMIN_ID, "✅ **Bot V17 Online!**\nUse `/start`")
    except: pass
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
