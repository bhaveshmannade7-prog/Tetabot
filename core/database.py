import json
import os
from config import Config

class Database:
    def __init__(self):
        self.db_path = Config.DB_FILE
        self.lock_path = Config.LOCK_FILE
        self._ensure_files()

    def _ensure_files(self):
        if not os.path.exists(self.db_path):
            with open(self.db_path, 'w') as f: json.dump({}, f)
        if not os.path.exists(self.lock_path):
            with open(self.lock_path, 'w') as f: json.dump([], f)

    # --- Lock Management ---
    def get_locks(self):
        with open(self.lock_path, 'r') as f:
            return set(json.load(f))

    def add_lock(self, item):
        locks = self.get_locks()
        locks.add(item)
        with open(self.lock_path, 'w') as f:
            json.dump(list(locks), f)

    def remove_lock(self, item):
        locks = self.get_locks()
        if item in locks:
            locks.remove(item)
            with open(self.lock_path, 'w') as f:
                json.dump(list(locks), f)

    # --- Message Data Management ---
    def save_scan_data(self, chat_id, data_dict):
        """Overwrites current DB with new scan data."""
        payload = {
            "chat_id": chat_id,
            "messages": data_dict # Key: msg_id, Value: {data}
        }
        with open(self.db_path, 'w') as f:
            json.dump(payload, f, indent=2)

    def load_data(self):
        with open(self.db_path, 'r') as f:
            return json.load(f)

    def update_message_state(self, msg_id, new_caption):
        data = self.load_data()
        if str(msg_id) in data["messages"]:
            data["messages"][str(msg_id)]["current_caption"] = new_caption
            # Update local file
            self.save_scan_data(data["chat_id"], data["messages"])
