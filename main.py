import json, asyncio, os, random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted
from flask import Flask
from threading import Thread

# --- CONFIGURATION (Environment Variables) ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SESSION_LIST = [s.strip() for s in os.getenv("SESSIONS", "").split(",") if s.strip()]
PROMOTE_LINK = os.getenv("LINK", "https://t.me/YourBot")

# Multiple Clients Initialization
clients = []
for i, session in enumerate(SESSION_LIST):
    cli = Client(f"bot_acc_{i}", session_string=session, api_id=API_ID, api_hash=API_HASH)
    clients.append(cli)

# Primary client for handling admin commands
app = clients[0]

web_app = Flask(__name__)
@web_app.reply('/')
def home(): return "Multi-Account Pro Bot is Running! ⚡"

# Database & State
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"
STATUS = {"is_running": False, "success": 0, "failed": 0}

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
        "🚀 **Multi-Account UserBot V6**\n\n"
        f"✅ **Accounts Loaded:** {len(clients)}\n"
        "📍 **Scraping:**\n"
        "• `/scrape @username 5000` (By Username)\n"
        "• `/scrape -100xxxx 5000` (By Numeric ID)\n\n"
        "📍 **Execution:**\n"
        "• `/send` - Multi-account rotation messaging\n"
        "• `/status` - Detailed progress\n"
        "• `/sync` - Update sent history\n\n"
        "📍 **Management:**\n"
        "• `/download` | `/delete_data` | `/stop`"
    )
    await message.reply(text)

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_cmd(client, message):
    try:
        parts = message.text.split()
        if len(parts) < 3:
            return await message.reply("❌ **Format:** `/scrape @chat 1000` ya `/scrape -100xxx 1000`")
        
        target = parts[1]
        # Agar target numeric hai (ID), toh use integer mein badlein
        if target.startswith("-") or target.isdigit():
            target = int(target)
            
        limit = int(parts[2])
        await message.reply(f"🔍 `{target}` se data nikal raha hoon... Thoda intezar karein.")
        
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
        await message.reply(f"✅ **Scraping Done!**\nNaye Users: `{count}`\nTotal Database: `{len(scraped)}`")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

@app.on_message(filters.command("status") & filters.user(ADMIN_ID))
async def stats_cmd(client, message):
    scraped = len(load_json(USERS_DB))
    sent = len(load_json(SENT_DB))
    text = (
        "📊 **Bot Status Report**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📁 **Total Scraped:** `{scraped}`\n"
        f"✅ **Total Sent:** `{sent}`\n"
        f"⏳ **Pending:** `{scraped - sent}`\n"
        f"🤖 **Connected Accounts:** `{len(clients)}`\n"
        f"🚀 **Session Success:** `{STATUS['success']}`\n"
        f"⚠️ **Session Active:** `{'YES' if STATUS['is_running'] else 'NO'}`"
    )
    await message.reply(text)

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    if STATUS["is_running"]: 
        return await message.reply("⚠️ Messaging pehle se chal rahi hai!")
    
    scraped_list = list(load_json(USERS_DB))
    sent_ids = load_json(SENT_DB)
    pending = [u for u in scraped_list if u[0] not in sent_ids]
    
    if not pending: 
        return await message.reply("❌ Bhejne ke liye koi naya data nahi hai.")

    STATUS["is_running"] = True
    STATUS["success"] = 0
    await message.reply(f"🚀 **Messaging Started!**\nTarget: `{len(pending)}` users\nDelay: `8-15s` (Auto-Rotation)")

    cli_idx = 0
    for user_data in pending:
        if not STATUS["is_running"]: break
        
        u_id, _, u_name = user_data
        curr_cli = clients[cli_idx]

        try:
            msg = f"Hello {u_name}! 👋\n\nCheckout this special link: {PROMOTE_LINK}"
            await curr_cli.send_message(u_id, msg)
            
            STATUS["success"] += 1
            sent_ids.add(u_id)
            save_json(SENT_DB, sent_ids)
            
            # Rotate to next account
            cli_idx = (cli_idx + 1) % len(clients)
            
            # Fast but safe delay due to multi-account rotation
            await asyncio.sleep(random.randint(8, 15))

            # Long break after 50 messages total
            if STATUS["success"] % 50 == 0:
                await asyncio.sleep(300) # 5 min break

        except FloodWait as e:
            cli_idx = (cli_idx + 1) % len(clients)
            await asyncio.sleep(5) # Switch account and continue
        except (PeerFlood, UserPrivacyRestricted):
            continue
        except Exception:
            continue

    STATUS["is_running"] = False
    await message.reply(f"🏁 **Task Completed!**\nTotal Messages Sent: `{STATUS['success']}`")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_cmd(client, message):
    await message.reply("🔄 **Syncing History...** (Purane chats scan ho rahe hain)")
    sent = load_json(SENT_DB)
    async for dialog in client.get_dialogs():
        if dialog.chat.type == enums.ChatType.PRIVATE:
            sent.add(dialog.chat.id)
    save_json(SENT_DB, sent)
    await message.reply(f"✅ **Sync Done!** Total Sent History: `{len(sent)}`")

@app.on_message(filters.command("download") & filters.user(ADMIN_ID))
async def dl_cmd(client, message):
    if os.path.exists(USERS_DB): await message.reply_document(USERS_DB)
    if os.path.exists(SENT_DB): await message.reply_document(SENT_DB)

@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(client, message):
    STATUS["is_running"] = False
    await message.reply("🛑 **Process Stopped by Admin!**")

@app.on_message(filters.command("delete_data") & filters.user(ADMIN_ID))
async def del_cmd(client, message):
    if os.path.exists(USERS_DB): os.remove(USERS_DB)
    if os.path.exists(SENT_DB): os.remove(SENT_DB)
    await message.reply("🗑️ **Database cleared successfully!**")

# --- EXECUTION ---
def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

async def init_accounts():
    for cli in clients:
        try:
            await cli.start()
        except Exception as e:
            print(f"Error starting account: {e}")

if __name__ == "__main__":
    Thread(target=run_web).start()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_accounts())
    print(">>> Bot and Web Server Started Successfully!")
    app.run()
