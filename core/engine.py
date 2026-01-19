import asyncio
import re
from pyrogram import Client
from pyrogram.errors import FloodWait, MessageNotModified
from .database import Database
from .utils import TextUtils
from config import Config

db = Database()

class BotEngine:
    
    @staticmethod
    async def scan_chat(client: Client, chat_id, limit=500):
        """Step 1: Scans chat and builds JSON map."""
        scanned_data = {}
        count = 0
        
        async for msg in client.get_chat_history(chat_id, limit=limit):
            if msg.caption or_(msg.text):
                content = msg.caption or msg.text
                msg_id = msg.id
                
                scanned_data[msg_id] = {
                    "original_caption": content,
                    "current_caption": content,
                    "type": "caption" if msg.caption else "text",
                    "hash": TextUtils.compute_hash(content),
                    "entities": list(TextUtils.extract_entities(content))
                }
                count += 1
                
        db.save_scan_data(chat_id, scanned_data)
        return count

    @staticmethod
    async def process_cleaning(client: Client, dry_run=False):
        """Removes unlocked links/usernames."""
        data = db.load_data()
        chat_id = data.get("chat_id")
        messages = data.get("messages", {})
        locks = db.get_locks()
        
        logs = []
        edited_count = 0

        for msg_id, info in messages.items():
            current_text = info["current_caption"]
            # Apply cleaning logic
            new_text = TextUtils.clean_text_logic(current_text, locks)
            
            if new_text != current_text:
                if dry_run:
                    logs.append(f"[DRY] Msg {msg_id}: \nOLD: {current_text}\nNEW: {new_text}\n---")
                else:
                    success = await BotEngine._safe_edit(client, chat_id, int(msg_id), new_text)
                    if success:
                        db.update_message_state(msg_id, new_text)
                        edited_count += 1
                        logs.append(f"[EDIT] Msg {msg_id} cleaned.")
                        
        return logs, edited_count

    @staticmethod
    async def process_replacement(client: Client, target, replacement, dry_run=False):
        """Replaces specific string A with string B."""
        data = db.load_data()
        chat_id = data.get("chat_id")
        messages = data.get("messages", {})
        
        logs = []
        edited_count = 0

        for msg_id, info in messages.items():
            current_text = info["current_caption"]
            
            if target in current_text:
                new_text = current_text.replace(target, replacement)
                
                if dry_run:
                    logs.append(f"[DRY] Msg {msg_id}: Replaced '{target}' -> '{replacement}'")
                else:
                    success = await BotEngine._safe_edit(client, chat_id, int(msg_id), new_text)
                    if success:
                        db.update_message_state(msg_id, new_text)
                        edited_count += 1
                        logs.append(f"[EDIT] Msg {msg_id} replaced.")

        return logs, edited_count

    @staticmethod
    async def process_duplicates(client: Client, dry_run=False):
        """Deletes duplicate movies based on Hash."""
        data = db.load_data()
        chat_id = data.get("chat_id")
        messages = data.get("messages", {})
        
        # Group by hash
        hash_map = {}
        for msg_id, info in messages.items():
            h = info["hash"]
            if not h: continue
            if h not in hash_map: hash_map[h] = []
            hash_map[h].append(int(msg_id))

        logs = []
        deleted_count = 0
        
        for h, ids in hash_map.items():
            if len(ids) > 1:
                # Sort IDs (Keep the oldest/smallest ID)
                ids.sort()
                to_delete = ids[1:] # Keep index 0, delete rest
                
                for mid in to_delete:
                    if dry_run:
                        logs.append(f"[DRY] Would DELETE Msg {mid} (Duplicate of {ids[0]})")
                    else:
                        try:
                            await client.delete_messages(chat_id, mid)
                            deleted_count += 1
                            logs.append(f"[DEL] Deleted {mid}")
                            await asyncio.sleep(Config.EDIT_SLEEP)
                        except Exception as e:
                            logs.append(f"[ERR] Failed to delete {mid}: {e}")

        return logs, deleted_count

    @staticmethod
    async def restore_originals(client: Client):
        """UNDO: Restores original captions from JSON."""
        data = db.load_data()
        chat_id = data.get("chat_id")
        messages = data.get("messages", {})
        count = 0
        
        for msg_id, info in messages.items():
            if info["current_caption"] != info["original_caption"]:
                success = await BotEngine._safe_edit(client, chat_id, int(msg_id), info["original_caption"])
                if success:
                    db.update_message_state(msg_id, info["original_caption"])
                    count += 1
        return count

    @staticmethod
    async def _safe_edit(client, chat_id, msg_id, text):
        try:
            await client.edit_message_caption(chat_id, msg_id, caption=text)
            await asyncio.sleep(Config.EDIT_SLEEP)
            return True
        except MessageNotModified:
            return True # Technically a success since it matches
        except FloodWait as e:
            print(f"Sleeping for {e.value}s...")
            await asyncio.sleep(e.value)
            return await BotEngine._safe_edit(client, chat_id, msg_id, text)
        except Exception as e:
            print(f"Error editing {msg_id}: {e}")
            return False
