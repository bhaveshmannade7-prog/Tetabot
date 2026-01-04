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

app = Client("antiban_v4", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
web_app = Flask(__name__)

@web_app.route('/')
def home(): return "Bot is Running with Personalization! ⚡"

USERS_DB = "scraped_users.json"
SENT_DB = "sent_history.json"
SETTINGS = {"link": "", "is_running": False}

# Random greetings to confuse Telegram Spam Filter
GREETINGS = ["Hey", "Hello", "Hi", "Namaste", "Yo", "Dear"]

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(list(data), f, indent=4)

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            try:
                data = json.load(f)
                return set(tuple(x) if isinstance(x, list) else x for x in data)
            except: return set()
    return set()

@app.on_message(filters.command("start") & filters.user(ADMIN_ID))
async def start_msg(client, message):
    await message.reply(
        "🔥 **UserBot V4: Personalization Enabled**\n\n"
        "Commands: `/scrape`, `/link`, `/sync`, `/send`, `/stop`, `/download`, `/delete_data`"
    )

@app.on_message(filters.command("scrape") & filters.user(ADMIN_ID))
async def scrape_cmd(client, message):
    try:
        parts = message.text.split()
        target, limit = parts[1], int(parts[2])
        await message.reply(f"🔍 Scrape chalu hai {target} se...")
        
        scraped_data = load_json(USERS_DB)
        count = 0
        async for member in client.get_chat_members(target):
            if count >= limit: break
            if not member.user.is_bot:
                # Store: (ID, Username, First_Name)
                user_info = (member.user.id, member.user.username or "NoUser", member.user.first_name or "User")
                
                exists = any(u[0] == member.user.id for u in scraped_data)
                if not exists:
                    scraped_data.add(user_info)
                    count += 1
        
        save_json(USERS_DB, scraped_data)
        await message.reply(f"✅ Scraped: {count}. Total: {len(scraped_data)}")
    except Exception as e: await message.reply(f"❌ Error: {e}")

@app.on_message(filters.command("send") & filters.user(ADMIN_ID))
async def send_cmd(client, message):
    if not SETTINGS["link"]: return await message.reply("❌ Link set karein!")
    if SETTINGS["is_running"]: return await message.reply("⚠️ Running...")
    
    scraped = load_json(USERS_DB)
    sent = load_json(SENT_DB)
    
    pending = [u for u in scraped if u[0] not in sent]
    if not pending: return await message.reply("❌ No new users!")
    
    SETTINGS["is_running"] = True
    await message.reply(f"🚀 Sending to {len(pending)} users (Speed: 12s + Random Delay)")

    success = 0
    for user_data in pending:
        if not SETTINGS["is_running"]: break
        
        u_id, u_username, u_name = user_data
        try:
            # Har message alag hoga: "Hello Rahul! Link: ..."
            greet = random.choice(GREETINGS)
            msg_text = f"{greet} {u_name}! 👋\n\nAapke liye special access link yahan hai: {SETTINGS['link']}"
            
            await client.send_message(u_id, msg_text)
            success += 1
            sent.add(u_id)
            save_json(SENT_DB, sent)
            
            # Timer: 12 seconds + 2-5 sec random variation
            await asyncio.sleep(12 + random.randint(2, 5))

            # Long Break after 30 messages
            if success % 30 == 0:
                await asyncio.sleep(180) # 3 min break

        except FloodWait as e: await asyncio.sleep(e.value + 10)
        except (PeerFlood, UserPrivacyRestricted): continue
        except Exception: continue

    SETTINGS["is_running"] = False
    await message.reply(f"🏁 Done! Sent: {success}")

# (Baki commands download, sync, delete_data pehle jaise hi rahenge...)
# ... [Paste previous sync, download, delete commands here] ...

def run_web():
    web_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    Thread(target=run_web).start()
    app.run()
