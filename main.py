import json
import asyncio
import os
import random
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

app = Client("antiban_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
web_app = Flask(__name__)

@web_app.route('/')
def home(): return "Bot is Active! ⚡"

# Database Files
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"

# In-memory settings
SETTINGS = {"link": "", "is_running": False}

# --- HELPER FUNCTIONS ---
def save_json(filename, data):
    with open(filename, "w") as f:
        # indent=4 se data ek line ki jagah list format mein dikhega
        json.dump(list(data), f, indent=4)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try:
                data = json.load(f)
                # Agar data list of lists hai (ID, Username), toh use set of tuples banayein
                return set(tuple(x) if isinstance(x, list) else x for x in data)
            except: return set()
    return set()

# --- ADMIN COMMANDS ---

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_msg(client, message):
    text = (
        "🤖 **Pro UserBot V3 (Admin Access)**\n\n"
        "1️⃣ `/scrape @channel 5000` - Username ke saath scrape karein\n"
        "2️⃣ `/link [link]` - Apna message link set karein\n"
        "3️⃣ `/sync` - Purane chats scan karein\n"
        "4️⃣ `/send` - Messaging (12s delay)\n"
        "5️⃣ `/stop` - Turant messaging rokne ke liye\n\n"
        "**Data Management:**\n"
        "📥 `/download` - Properly formatted JSON backup\n"
        "🗑️ `/delete_data` - Server se sara data delete karein\n"
        "📤 **Import** - File bhein + caption `/import`"
    )
    await message.reply(text)

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_cmd(client, message):
    try:
        parts = message.text.split()
        if len(parts) < 3: return await message.reply("❌ Format: `/scrape @username 1000`")
        target, limit = parts[1], int(parts[2])
        
        await message.reply(f"🔍 {target} se users aur usernames nikal raha hoon...")
        scraped_data = load_json(USERS_DB) # Format: {(id, username), ...}
        count = 0
        
        async for member in client.get_chat_members(target):
            if count >= limit: break
            user_id = member.user.id
            username = member.user.username or "No_Username"
            
            # Check if ID already exists
            exists = any(u[0] == user_id if isinstance(u, tuple) else u == user_id for u in scraped_data)
            
            if not exists and not member.user.is_bot:
                scraped_data.add((user_id, username))
                count += 1
        
        save_json(USERS_DB, scraped_data)
        await message.reply(f"✅ Scraping Done! Total: {len(scraped_data)}\nNaye mile: {count}")
    except Exception as e: await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_cmd(client, message):
    await message.reply("🔄 Scan chalu hai...")
    sent_ids = load_json(SENT_DB)
    async for dialog in client.get_dialogs():
        if dialog.chat.type == enums.ChatType.PRIVATE:
            sent_ids.add(dialog.chat.id)
    save_json(SENT_DB, sent_ids)
    await message.reply(f"✅ Sync Done! History Size: {len(sent_ids)}")

@app.on_message(filters.command("delete_data") & filters.user(ADMIN_ID))
async def delete_data_cmd(client, message):
    if os.path.exists(USERS_DB): os.remove(USERS_DB)
    if os.path.exists(SENT_DB): os.remove(SENT_DB)
    await message.reply("🗑️ Sara JSON data server se delete kar diya gaya hai.")

@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(client, message):
    SETTINGS["is_running"] = False
    await message.reply("🛑 Messaging ko rok diya gaya hai.")

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    if not SETTINGS["link"]: return await message.reply("❌ Pehle `/link` set karein!")
    if SETTINGS["is_running"]: return await message.reply("⚠️ Already running!")
    
    scraped = load_json(USERS_DB)
    sent = load_json(SENT_DB)
    
    # Filter pending users
    pending = []
    for u in scraped:
        u_id = u[0] if isinstance(u, tuple) else u
        if u_id not in sent:
            pending.append(u)

    if not pending: return await message.reply("❌ Bhejne ke liye koi naya user nahi hai.")
    
    SETTINGS["is_running"] = True
    await message.reply(f"🚀 {len(pending)} logon ko bhej raha hoon.\nDelay: 12 seconds.")

    success = 0
    for user_data in pending:
        if not SETTINGS["is_running"]: break
        
        user_id = user_data[0] if isinstance(user_data, tuple) else user_data
        try:
            await client.send_message(user_id, f"Hello! Check this: {SETTINGS['link']}")
            success += 1
            sent.add(user_id)
            save_json(SENT_DB, sent)
            
            # Aapka naya timer (12 seconds)
            await asyncio.sleep(12)

        except FloodWait as e: await asyncio.sleep(e.value + 5)
        except (PeerFlood, UserPrivacyRestricted): continue
        except Exception: continue

    SETTINGS["is_running"] = False
    await message.reply(f"🏁 Finish! Total Sent: {success}")

@app.on_message(filters.command("download") & filters.user(ADMIN_ID))
async def download_cmd(client, message):
    if os.path.exists(USERS_DB):
        await message.reply_document(USERS_DB, caption="📂 Scraped Data (Formatted)")
    if os.path.exists(SENT_DB):
        await message.reply_document(SENT_DB, caption="📂 Sent History (Formatted)")

@app.on_message(filters.document & filters.user(ADMIN_ID))
async def import_handler(client, message):
    if message.caption and "/import" in message.caption:
        file_name = message.document.file_name
        await message.download(file_name)
        await message.reply(f"✅ {file_name} successfully import ho gayi!")

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    Thread(target=run_web).start()
    app.run()
