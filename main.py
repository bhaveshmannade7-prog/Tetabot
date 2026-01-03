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
def home(): return "Bot is Active!"

# Database Files
USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"

# In-memory sets to avoid duplicates
scraped_ids = set()
sent_ids = set()
SETTINGS = {"link": "", "is_running": False}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(list(data), f)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return set(json.load(f))
    return set()

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_msg(client, message):
    text = (
        "🤖 **Pro UserBot Dashboard**\n\n"
        "**Step 1:** `/scrape @channel 10000` (Users list banayein)\n"
        "**Step 2:** `/link [aapka_link]` (Message set karein)\n"
        "**Step 3:** `/sync` (Purane sent messages check karein)\n"
        "**Step 4:** `/send` (Messaging shuru karein)\n\n"
        "💡 **Status:** `/status` | **Stop:** `/stop`"
    )
    await message.reply(text)

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_cmd(client, message):
    try:
        parts = message.text.split()
        target = parts[1]
        limit = int(parts[2])
        await message.reply(f"🔍 {target} se unique users nikal raha hoon...")

        global scraped_ids
        scraped_ids = load_json(USERS_DB)
        count = 0

        async for member in client.get_chat_members(target):
            if count >= limit: break
            if member.user.id not in scraped_ids and not member.user.is_bot:
                scraped_ids.add(member.user.id)
                count += 1
        
        save_json(USERS_DB, scraped_ids)
        await message.reply(f"✅ Scraping Done! {count} naye users mile.\nTotal: {len(scraped_ids)}")
    except Exception as e:
        await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("link") & filters.user(ADMIN_ID))
async def link_cmd(client, message):
    link = message.text.split(None, 1)[1]
    SETTINGS["link"] = link
    await message.reply(f"🔗 Link set ho gaya: {link}")

@app.on_message(filters.command("sync") & filters.user(ADMIN_ID))
async def sync_cmd(client, message):
    await message.reply("🔄 History scan kar raha hoon... Isme thoda time lag sakta hai.")
    global sent_ids
    sent_ids = load_json(SENT_DB)
    count = 0

    async for dialog in client.get_dialogs():
        if dialog.chat.type in ["private"]: # Sirf private chats check karein
            sent_ids.add(dialog.chat.id)
            count += 1
    
    save_json(SENT_DB, sent_ids)
    await message.reply(f"✅ Sync Complete! {count} purane chats mile jinhe dobara message nahi jayega.")

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    if not SETTINGS["link"]: return await message.reply("❌ Pehle `/link` set karein!")
    
    global sent_ids, scraped_ids
    sent_ids = load_json(SENT_DB)
    scraped_ids = load_json(USERS_DB)

    if SETTINGS["is_running"]: return await message.reply("⚠️ Process chal raha hai!")
    SETTINGS["is_running"] = True
    
    pending_ids = list(scraped_ids - sent_ids)
    await message.reply(f"🚀 {len(pending_ids)} logon ko message bhej raha hoon...")

    success = 0
    for user_id in pending_ids:
        if not SETTINGS["is_running"]: break
        try:
            await client.send_message(user_id, f"Hello! Check this link: {SETTINGS["link"]}")
            success += 1
            sent_ids.add(user_id)
            save_json(SENT_DB, sent_ids)

            # --- ANTI BAN DELAY (Very Important) ---
            # Random delay 45-90 seconds per message
            wait = random.randint(45, 90)
            
            # Har 15 messages ke baad 15 minute ka break
            if success % 15 == 0:
                print("Taking long break...")
                await asyncio.sleep(900) 
            else:
                await asyncio.sleep(wait)

        except FloodWait as e:
            await asyncio.sleep(e.value + 20)
        except (PeerFlood, UserPrivacyRestricted):
            continue
        except Exception as e:
            print(f"Error: {e}")

    SETTINGS["is_running"] = False
    await message.reply(f"🏁 Kaam khatam! Total Sent: {success}")

@app.on_message(filters.command("status") & filters.user(ADMIN_ID))
async def status_cmd(client, message):
    total = len(load_json(USERS_DB))
    done = len(load_json(SENT_DB))
    await message.reply(f"📊 **Progress:**\nScraped: {total}\nSent/Sync: {done}\nPending: {total - done}")

@app.on_message(filters.command("stop") & filters.user(ADMIN_ID))
async def stop_cmd(client, message):
    SETTINGS["is_running"] = False
    await message.reply("🛑 Process rok diya gaya.")

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    Thread(target=run_web).start()
    app.run()
