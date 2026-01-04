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
PROMOTE_LINK = os.getenv("LINK", "https://t.me/YourBot")

# Flask for Render Port Binding
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Account Bot is Online! ⚡"

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# DB Management
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(list(data), f, indent=4)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try:
                d = json.load(f)
                return set(tuple(x) for x in d)
            except: return set()
    return set()

# Initialize Clients
SESSION_LIST = [s.strip() for s in RAW_SESSIONS.split(",") if s.strip()]
clients = []

if not SESSION_LIST:
    logger.error("❌ SESSIONS ENV KHALI HAI! Render mein variable check karein.")
else:
    for i, session in enumerate(SESSION_LIST):
        try:
            cli = Client(f"bot_acc_{i}", session_string=session, api_id=API_ID, api_hash=API_HASH)
            clients.append(cli)
            logger.info(f"✅ Client {i} ready.")
        except Exception as e:
            logger.error(f"❌ Client {i} initialization error: {e}")

# Main Logic
if clients:
    app = clients[0]
    STATUS = {"is_running": False, "success": 0}

    @app.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(c, m):
        await m.reply(
            f"🚀 **Multi-Account UserBot V7**\n\n"
            f"✅ **Accounts:** {len(clients)}\n"
            f"📍 `/scrape @chat 1000` - Username ya ID se scrape karein\n"
            f"📍 `/send` - Messaging start karein\n"
            f"📍 `/status` - Progress dekhein\n"
            f"📍 `/sync` - Purani chats scan karein\n"
            f"📍 `/download` - Backup file lein\n"
            f"📍 `/delete_data` - Data saaf karein\n"
            f"📍 `/stop` - Messaging rokein"
        )

    @app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_cmd(c, m):
        try:
            parts = m.text.split()
            if len(parts) < 3: return await m.reply("❌ Format: `/scrape @chat 500` ya `/scrape -100xxx 500` ")
            target = parts[1]
            if target.startswith("-") or target.isdigit(): target = int(target)
            limit = int(parts[2])
            
            await m.reply(f"🔍 `{target}` se data nikal raha hoon...")
            scraped = load_json(USERS_DB)
            count = 0
            async for member in c.get_chat_members(target):
                if count >= limit: break
                if not member.user.is_bot:
                    u_info = (member.user.id, member.user.username or "N/A", member.user.first_name or "User")
                    if not any(u[0] == member.user.id for u in scraped):
                        scraped.add(u_info)
                        count += 1
            save_json(USERS_DB, scraped)
            await m.reply(f"✅ Done! Scraped: `{count}` | Total: `{len(scraped)}`")
        except Exception as e: await m.reply(f"❌ Error: `{e}`")

    @app.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_cmd(c, m):
        if STATUS["is_running"]: return await m.reply("⚠️ Already running!")
        scraped = list(load_json(USERS_DB))
        sent = load_json(SENT_DB)
        pending = [u for u in scraped if u[0] not in sent]
        
        if not pending: return await m.reply("❌ No new data!")
        STATUS["is_running"], STATUS["success"] = True, 0
        await m.reply(f"🚀 Started using {len(clients)} accounts!")

        cli_idx = 0
        for user in pending:
            if not STATUS["is_running"]: break
            curr_cli = clients[cli_idx]
            try:
                await curr_cli.send_message(user[0], f"Hi {user[2]}! 👋\n\nCheckout this: {PROMOTE_LINK}")
                STATUS["success"] += 1
                sent.add(user[0])
                save_json(SENT_DB, sent)
                cli_idx = (cli_idx + 1) % len(clients)
                await asyncio.sleep(random.randint(12, 18))
            except FloodWait as e:
                cli_idx = (cli_idx + 1) % len(clients)
                await asyncio.sleep(5)
            except Exception: continue

        STATUS["is_running"] = False
        await m.reply(f"🏁 Finished! Sent: `{STATUS['success']}`")

    @app.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(c, m):
        sc, sn = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        await m.reply(f"📊 **Bot Stats**\n\n📁 Scraped: {sc}\n✅ Sent: {sn}\n🤖 Active Accounts: {len(clients)}")

    @app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(c, m):
        STATUS["is_running"] = False
        await m.reply("🛑 Stopped!")

    @app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
    async def sync_cmd(c, m):
        await m.reply("🔄 Syncing...")
        sent = load_json(SENT_DB)
        async for dialog in c.get_dialogs():
            if dialog.chat.type == enums.ChatType.PRIVATE:
                sent.add(dialog.chat.id)
        save_json(SENT_DB, sent)
        await m.reply(f"✅ Sync Done! History: {len(sent)}")

    @app.on_message(filters.command("download") & filters.user(ADMIN_ID))
    async def dl(c, m):
        if os.path.exists(USERS_DB): await m.reply_document(USERS_DB)
        if os.path.exists(SENT_DB): await m.reply_document(SENT_DB)

    @app.on_message(filters.command("delete_data") & filters.user(ADMIN_ID))
    async def del_data(c, m):
        if os.path.exists(USERS_DB): os.remove(USERS_DB)
        if os.path.exists(SENT_DB): os.remove(SENT_DB)
        await m.reply("🗑️ All data deleted!")

    async def start_all():
        for cli in clients:
            try: await cli.start()
            except: pass
        logger.info(">>> All accounts online.")

    if __name__ == "__main__":
        Thread(target=run_web).start()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(start_all())
        app.run()
else:
    if __name__ == "__main__":
        run_web() # Fallback to keep Render happy
