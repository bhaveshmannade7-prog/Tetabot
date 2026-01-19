import asyncio
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Running")

def start_web():
    server = HTTPServer(('0.0.0.0', 8080), SimpleHandler)
    server.serve_forever()

Thread(target=start_web).start()

from pyrogram import Client, filters, idle
from config import Config
from core.database import Database
from core.engine import BotEngine

# Initialize Client
app = Client(
    "manager_session",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    session_string=Config.SESSION_STRING
)

db = Database()

# --- Authorization Check ---
def is_admin(_, __, message):
    return message.from_user and message.from_user.id == Config.ADMIN_ID

admin_filter = filters.create(is_admin)

# --- 1. SCANNING ---
@app.on_message(filters.command("scan_target") & admin_filter)
async def scan_target(client, message):
    try:
        if len(message.command) < 2:
            return await message.reply("Usage: `/scan_target @channel_username limit`")
        
        target = message.command[1]
        limit = int(message.command[2]) if len(message.command) > 2 else 200
        
        status = await message.reply(f"🔍 Scanning {target} for {limit} msgs...")
        
        # Resolve ID
        chat = await client.get_chat(target)
        count = await BotEngine.scan_chat(client, chat.id, limit)
        
        await status.edit(f"✅ Scan Complete.\nSaved {count} messages to JSON database.\nReady for operations.")
        
    except Exception as e:
        await message.reply(f"❌ Error: {e}")

# --- 2. LOCK SYSTEM ---
@app.on_message(filters.command("lock_item") & admin_filter)
async def lock_item(client, message):
    if len(message.command) < 2:
        return await message.reply("Usage: `/lock_item @username` or `domain.com`")
    
    item = message.command[1]
    db.add_lock(item)
    await message.reply(f"🔒 **LOCKED:** `{item}`\nIt will strictly NOT be removed/edited.")

@app.on_message(filters.command("view_locks") & admin_filter)
async def view_locks(client, message):
    locks = db.get_locks()
    text = "\n".join([f"- `{l}`" for l in locks])
    await message.reply(f"**Current Locks:**\n{text}")

# --- 3. CLEANER ---
@app.on_message(filters.command("clean_links") & admin_filter)
async def clean_links(client, message):
    status = await message.reply("🧹 Starting Cleanup...")
    logs, count = await BotEngine.process_cleaning(client, dry_run=False)
    
    # Save log
    with open("clean_log.txt", "w") as f: f.write("\n".join(logs))
    await message.reply_document("clean_log.txt", caption=f"✅ Cleaned {count} messages.")

# --- 4. REPLACER ---
@app.on_message(filters.command("replace_item") & admin_filter)
async def replace_item(client, message):
    # Usage: /replace_item @old @new
    if len(message.command) < 3:
        return await message.reply("Usage: `/replace_item @old @new`")
    
    old = message.command[1]
    new = message.command[2]
    
    status = await message.reply(f"🔄 Replacing `{old}` with `{new}`...")
    logs, count = await BotEngine.process_replacement(client, old, new, dry_run=False)
    
    await message.reply(f"✅ Replaced in {count} messages.")

# --- 5. DUPLICATE REMOVER ---
@app.on_message(filters.command("remove_dupes") & admin_filter)
async def remove_dupes(client, message):
    status = await message.reply("♻️ Analyzing duplicates based on Hash...")
    logs, count = await BotEngine.process_duplicates(client, dry_run=False)
    
    with open("dupe_log.txt", "w") as f: f.write("\n".join(logs))
    await message.reply_document("dupe_log.txt", caption=f"✅ Deleted {count} duplicates.")

# --- PRO FEATURES ---

# Pro 1: Dry Run (Preview)
@app.on_message(filters.command("dry_run") & admin_filter)
async def dry_run_cmd(client, message):
    """Simulate cleaning to see what would happen."""
    status = await message.reply("🧪 Running Simulation (No edits will be made)...")
    logs, _ = await BotEngine.process_cleaning(client, dry_run=True)
    
    with open("dry_run_report.txt", "w") as f: f.write("\n".join(logs))
    await message.reply_document("dry_run_report.txt", caption="📋 Dry Run Report generated.")

# Pro 2: Undo Last (Restore)
@app.on_message(filters.command("undo_all") & admin_filter)
async def undo_all(client, message):
    """Restores ALL messages to original state from JSON."""
    msg = await message.reply("⚠️ Restoring originals... This might take time.")
    count = await BotEngine.restore_originals(client)
    await msg.edit(f"✅ Restored {count} messages to original state.")

# Pro 3: Stats Report
@app.on_message(filters.command("stats") & admin_filter)
async def stats(client, message):
    data = db.load_data()
    total = len(data.get("messages", {}))
    locks = len(db.get_locks())
    chat_id = data.get("chat_id", "None")
    
    stats_text = (
        f"📊 **Database Stats**\n\n"
        f"**Target ID:** `{chat_id}`\n"
        f"**Tracked Msgs:** {total}\n"
        f"**Active Locks:** {locks}\n"
        f"**DB Size:** {os.path.getsize(Config.DB_FILE) / 1024:.2f} KB"
    )
    await message.reply(stats_text)

# Pro 4: Whitelist Mode Toggle (Logic stub)
@app.on_message(filters.command("ping") & admin_filter)
async def ping_pong(client, message):
    await message.reply(f"🏓 Pong! Running on Pyrogram v{Client.__version__}")

if __name__ == "__main__":
    print("🤖 Bot Started on Render/Termux...")
    app.start()
    idle()
    app.stop()
