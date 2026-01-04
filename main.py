import json, asyncio, os, random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted, SessionPasswordNeeded
from flask import Flask
from threading import Thread

# --- CONFIG ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SESSION_LIST = [s.strip() for s in os.getenv("SESSIONS", "").split(",") if s.strip()]

# Multi-Client Setup
clients = []
for i, session in enumerate(SESSION_LIST):
    cli = Client(f"bot_acc_{i}", session_string=session, api_id=API_ID, api_hash=API_HASH)
    clients.append(cli)

# Primary app for commands
app = clients[0]

web_app = Flask(__name__)
@web_app.route('/')
def home(): return "Multi-Account Engine is Online! ⚡"

# DB Files
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"
SETTINGS = {"is_running": False, "success": 0, "failed": 0}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(list(data), f, indent=4)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try:
                d = json.load(f)
                return set(tuple(x) if isinstance(x, list) else x for x in d)
            except: return set()
    return set()

# --- ADMIN COMMANDS ---

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_cmd(client, message):
    text = (
        "🚀 **Multi-Account UserBot V5**\n\n"
        f"✅ **Connected Accounts:** {len(clients)}\n"
        "📍 **Commands:**\n"
        "1️⃣ `/scrape @group 5000` - Sabhi accounts se data nikalne ke liye\n"
        "2️⃣ `/sync` - Purani history check karein\n"
        "3️⃣ `/send` - High-speed multi-account messaging\n"
        "4️⃣ `/stats` - Real-time progress dekhne ke liye\n"
        "5️⃣ `/stop` | `/download` | `/delete_data`"
    )
    await message.reply(text)

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_cmd(client, message):
    try:
        parts = message.text.split()
        target, limit = parts[1], int(parts[2])
        await message.reply(f"🔍 {target} se data nikal raha hoon...")
        
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
        await message.reply(f"✅ Scraped: {count}\nTotal Database: {len(scraped)}")
    except Exception as e: await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID))
async def stats_cmd(client, message):
    scraped = len(load_json(USERS_DB))
    sent = len(load_json(SENT_DB))
    text = (
        "📊 **Current Statistics**\n\n"
        f"📁 Total Scraped: {scraped}\n"
        f"✅ Total Sent: {sent}\n"
        f"⏳ Pending: {scraped - sent}\n"
        f"🤖 Active Accounts: {len(clients)}\n"
        f"📈 Session Success: {SETTINGS['success']}\n"
        f"📉 Session Failed: {SETTINGS['failed']}"
    )
    await message.reply(text)

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    link = os.getenv("LINK")
    if not link: return await message.reply("❌ Env me LINK set karein!")
    if SETTINGS["is_running"]: return await message.reply("⚠️ Pehle se chal raha hai!")
    
    scraped = list(load_json(USERS_DB))
    sent = load_json(SENT_DB)
    pending = [u for u in scraped if u[0] not in sent]
    
    if not pending: return await message.reply("❌ No new users to send!")

    SETTINGS["is_running"] = True
    SETTINGS["success"] = 0
    await message.reply(f"🚀 High-Speed Messaging Start!\nTarget: {len(pending)} users\nAccounts: {len(clients)}")

    cli_idx = 0
    for user_data in pending:
        if not SETTINGS["is_running"]: break
        
        u_id, _, u_name = user_data
        curr_cli = clients[cli_idx]

        try:
            # Random Greeting
            msg = f"Hi {u_name}! 👋 Check this out: {link}"
            await curr_cli.send_message(u_id, msg)
            
            SETTINGS["success"] += 1
            sent.add(u_id)
            save_json(SENT_DB, sent)
            
            # Account Rotation
            cli_idx = (cli_idx + 1) % len(clients)
            
            # Smart Delay (5-8s kyunki accounts rotate ho rahe hain)
            await asyncio.sleep(random.randint(5, 8))

        except FloodWait as e:
            # Agar ek account par flood aaye, toh use skip karke agle par jayein
            cli_idx = (cli_idx + 1) % len(clients)
            continue
        except (PeerFlood, UserPrivacyRestricted):
            continue
        except Exception:
            continue

    SETTINGS["is_running"] = False
    await message.reply(f"🏁 Campaign Finished! Total Sent: {SETTINGS['success']}")

# --- Utility Commands ---
@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(client, message):
    SETTINGS["is_running"] = False
    await message.reply("🛑 Stopped!")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_cmd(client, message):
    await message.reply("🔄 Syncing history...")
    sent = load_json(SENT_DB)
    async for dialog in client.get_dialogs():
        if dialog.chat.type == enums.ChatType.PRIVATE:
            sent.add(dialog.chat.id)
    save_json(SENT_DB, sent)
    await message.reply(f"✅ Sync Done! History: {len(sent)}")

@app.on_message(filters.command("download") & filters.user(ADMIN_ID))
async def dl_cmd(client, message):
    if os.path.exists(USERS_DB): await message.reply_document(USERS_DB)
    if os.path.exists(SENT_DB): await message.reply_document(SENT_DB)

@app.on_message(filters.command("delete_data") & filters.user(ADMIN_ID))
async def del_cmd(client, message):
    if os.path.exists(USERS_DB): os.remove(USERS_DB)
    if os.path.exists(SENT_DB): os.remove(SENT_DB)
    await message.reply("🗑️ Data Deleted!")

# Flask & Client Runner
def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

async def start_clients():
    for cli in clients:
        try:
            await cli.start()
            print(f"Account started!")
        except Exception as e:
            print(f"Error starting account: {e}")

if __name__ == "__main__":
    Thread(target=run_web).start()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(start_clients())
    app.run()
