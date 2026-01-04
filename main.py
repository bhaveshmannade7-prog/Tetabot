import json, asyncio, os, random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
# Yahan comma check handle kiya gaya hai
RAW_SESSIONS = os.getenv("SESSIONS", "")
SESSION_LIST = [s.strip() for s in RAW_SESSIONS.split(",") if s.strip()]
PROMOTE_LINK = os.getenv("LINK", "https://t.me/YourBot")

# Multiple Clients Initialization
clients = []
if not SESSION_LIST:
    print("❌ ERROR: Koi bhi SESSION_STRING nahi mila! Render Envs check karein.")
else:
    for i, session in enumerate(SESSION_LIST):
        cli = Client(f"bot_acc_{i}", session_string=session, api_id=API_ID, api_hash=API_HASH)
        clients.append(cli)

# Flask Setup (Render Port Binding Fix)
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Account Pro Bot is Online! ⚡"

def run_web():
    port = int(os.getenv("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)

# Check if clients exist before proceeding
if not clients:
    print("🛑 Bot start nahi ho sakta kyunki sessions missing hain.")
    # Keep web server alive even if bot fails so Render doesn't loop
    Thread(target=run_web).start()
else:
    app = clients[0] # Primary Admin Account

    # DB Files
    USERS_DB = "scraped_users.json"
    SENT_DB = "sent_history.json"
    STATUS = {"is_running": False, "success": 0}

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

    # --- ADMIN COMMANDS ---

    @app.on_message(filters.command("start") & filters.user(ADMIN_ID))
    async def start_cmd(client, message):
        text = (
            "🚀 **Multi-Account UserBot V7**\n\n"
            f"✅ **Accounts Loaded:** {len(clients)}\n"
            "📍 **Scraping:** `/scrape @chat 1000`\n"
            "📍 **Messaging:** `/send` | `/status` | `/stop`"
        )
        await message.reply(text)

    @app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
    async def scrape_cmd(client, message):
        try:
            parts = message.text.split()
            target = parts[1]
            if target.startswith("-") or target.isdigit(): target = int(target)
            limit = int(parts[2])
            
            await message.reply(f"🔍 `{target}` se data nikal raha hoon...")
            scraped = load_json(USERS_DB)
            count = 0
            async for member in client.get_chat_members(target):
                if count >= limit: break
                if not member.user.is_bot:
                    user_info = (member.user.id, member.user.username or "N/A", member.user.first_name or "User")
                    if not any(u[0] == member.user.id for u in scraped):
                        scraped.add(user_info)
                        count += 1
            save_json(USERS_DB, scraped)
            await message.reply(f"✅ Scraped: `{count}` | Total: `{len(scraped)}`")
        except Exception as e: await message.reply(f"❌ Error: `{e}`")

    @app.on_message(filters.command("send") & filters.user(ADMIN_ID))
    async def send_cmd(client, message):
        if STATUS["is_running"]: return await message.reply("⚠️ Already running!")
        scraped = list(load_json(USERS_DB))
        sent = load_json(SENT_DB)
        pending = [u for u in scraped if u[0] not in sent]
        
        if not pending: return await message.reply("❌ No new data!")
        STATUS["is_running"], STATUS["success"] = True, 0
        await message.reply(f"🚀 Started! Accounts: {len(clients)}")

        cli_idx = 0
        for user_data in pending:
            if not STATUS["is_running"]: break
            u_id, _, u_name = user_data
            curr_cli = clients[cli_idx]
            try:
                await curr_cli.send_message(u_id, f"Hello {u_name}! 👋\nLink: {PROMOTE_LINK}")
                STATUS["success"] += 1
                sent.add(u_id)
                save_json(SENT_DB, sent)
                cli_idx = (cli_idx + 1) % len(clients)
                await asyncio.sleep(random.randint(12, 18)) # Safe speed
            except FloodWait as e:
                cli_idx = (cli_idx + 1) % len(clients)
                await asyncio.sleep(5)
            except Exception: continue

        STATUS["is_running"] = False
        await message.reply(f"🏁 Done! Sent: {STATUS['success']}")

    # --- Additional Utils ---
    @app.on_message(filters.command("status") & filters.user(ADMIN_ID))
    async def status_cmd(client, message):
        scraped, sent = len(load_json(USERS_DB)), len(load_json(SENT_DB))
        await message.reply(f"📊 Stats:\nScraped: {scraped}\nSent: {sent}\nAccounts: {len(clients)}")

    @app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
    async def stop_cmd(client, message):
        STATUS["is_running"] = False
        await message.reply("🛑 Stopped!")

    @app.on_message(filters.command("download") & filters.user(ADMIN_ID))
    async def dl_cmd(client, message):
        if os.path.exists(USERS_DB): await message.reply_document(USERS_DB)
        if os.path.exists(SENT_DB): await message.reply_document(SENT_DB)

    # --- EXECUTION ---
    async def start_all():
        for cli in clients: await cli.start()
        print(">>> Accounts Started!")

    if __name__ == "__main__":
        Thread(target=run_web).start()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(start_all())
        app.run()
