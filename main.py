import json
import asyncio
import os
import random
from pyrogram import Client, filters, errors
from pyrogram.errors import FloodWait, PeerFlood, UserPrivacyRestricted
from flask import Flask
from threading import Thread

# --- CONFIGURATION (Environment Variables) ---
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

app = Client("antiban_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
web_app = Flask(__name__)

@web_app.route('/')
def home(): return "Bot is Active and Secure!"

# Database Files
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"

# In-memory data
SETTINGS = {"link": "", "is_running": False}

# Helper Functions
def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(list(data), f)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try: return set(json.load(f))
            except: return set()
    return set()

# --- COMMANDS (Admin Only) ---

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_msg(client, message):
    text = (
        "🤖 **Pro UserBot Dashboard (Admin Only)**\n\n"
        "**Main Commands:**\n"
        "1️⃣ `/scrape @channel 10000` - Users nikalne ke liye\n"
        "2️⃣ `/link [link]` - Apna message link set karein\n"
        "3️⃣ `/sync` - Purane chats scan karke duplicate rokein\n"
        "4️⃣ `/send` - Messaging shuru karein\n\n"
        "**Data Management:**\n"
        "📥 `/download` - Dono JSON files ka backup lein\n"
        "📤 **Import** - Bus `.json` file bot ko bhein (caption: /import)\n\n"
        "💡 `/status` | `/stop` | `/help`"
    )
    await message.reply(text)

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_cmd(client, message):
    try:
        parts = message.text.split()
        if len(parts) < 3: return await message.reply("Format: `/scrape @username 5000`")
        target, limit = parts[1], int(parts[2])
        
        await message.reply(f"🔍 {target} se unique users nikal raha hoon...")
        scraped_ids = load_json(USERS_DB)
        count = 0
        async for member in client.get_chat_members(target):
            if count >= limit: break
            if member.user.id not in scraped_ids and not member.user.is_bot:
                scraped_ids.add(member.user.id)
                count += 1
        save_json(USERS_DB, scraped_ids)
        await message.reply(f"✅ Scraping Done! Total: {len(scraped_ids)}")
    except Exception as e: await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("link") & filters.user(ADMIN_ID))
async def link_cmd(client, message):
    if len(message.text.split()) < 2: return await message.reply("Link bhi likhein!")
    SETTINGS["link"] = message.text.split(None, 1)[1]
    await message.reply(f"🔗 Link set ho gaya: {SETTINGS['link']}")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_cmd(client, message):
    await message.reply("🔄 History scan ho rahi hai... Wait karein.")
    sent_ids = load_json(SENT_DB)
    async for dialog in client.get_dialogs():
        if dialog.chat.type in [enums.ChatType.PRIVATE]:
            sent_ids.add(dialog.chat.id)
    save_json(SENT_DB, sent_ids)
    await message.reply(f"✅ Sync Complete! Total Sent History: {len(sent_ids)}")

@app.on_message(filters.command("download") & filters.user(ADMIN_ID))
async def download_cmd(client, message):
    if os.path.exists(USERS_DB):
        await message.reply_document(USERS_DB, caption="📂 Scraped Users Backup")
    if os.path.exists(SENT_DB):
        await message.reply_document(SENT_DB, caption="📂 Sent History Backup")
    if not os.path.exists(USERS_DB) and not os.path.exists(SENT_DB):
        await message.reply("❌ Koi backup file nahi mili.")

@app.on_message(filters.document & filters.user(ADMIN_ID))
async def import_handler(client, message):
    # Agar admin file bhejta hai aur caption me '/import' likhta hai
    if message.caption and "/import" in message.caption:
        file_name = message.document.file_name
        if file_name not in [USERS_DB, SENT_DB]:
            return await message.reply(f"❌ File ka naam sirf `{USERS_DB}` ya `{SENT_DB}` hona chahiye.")
        
        await message.download(file_name)
        await message.reply(f"✅ {file_name} successfully import ho gayi!")

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    if not SETTINGS["link"]: return await message.reply("❌ Pehle `/link` set karein!")
    if SETTINGS["is_running"]: return await message.reply("⚠️ Pehle se chal raha hai!")
    
    scraped = load_json(USERS_DB)
    sent = load_json(SENT_DB)
    pending = list(scraped - sent)
    
    if not pending: return await message.reply("❌ Bhejne ke liye koi naya user nahi hai.")
    
    SETTINGS["is_running"] = True
    await message.reply(f"🚀 {len(pending)} users ko bhej raha hoon. (Delay: 45-90s)")

    success = 0
    for user_id in pending:
        if not SETTINGS["is_running"]: break
        try:
            await client.send_message(user_id, f"Hello! Ye raha link: {SETTINGS['link']}")
            success += 1
            sent.add(user_id)
            save_json(SENT_DB, sent) # Har message ke baad save karein

            # Anti-Ban Strategy
            wait = random.randint(45, 90)
            if success % 15 == 0: await asyncio.sleep(900) # 15 min break
            else: await asyncio.sleep(wait)

        except FloodWait as e: await asyncio.sleep(e.value + 10)
        except (PeerFlood, UserPrivacyRestricted): continue
        except Exception: continue

    SETTINGS["is_running"] = False
    await message.reply(f"🏁 Finish! Sent: {success}")

@app.on_message(filters.command("status") & filters.user(ADMIN_ID))
async def status_cmd(client, message):
    scraped = len(load_json(USERS_DB))
    sent = len(load_json(SENT_DB))
    await message.reply(f"📊 **Status:**\nTotal: {scraped}\nSent: {sent}\nPending: {scraped-sent}")

@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(client, message):
    SETTINGS["is_running"] = False
    await message.reply("🛑 Process stopped.")

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    Thread(target=run_web).start()
    app.run()
