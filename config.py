import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    SESSION_STRING = os.getenv("SESSION_STRING")
    ADMIN_ID = int(os.getenv("ADMIN_ID"))
    
    # Paths
    DB_FILE = "data_store.json"
    LOCK_FILE = "locks.json"
    
    # Safety Delays (Seconds)
    EDIT_SLEEP = 1.5  # Time between edits to avoid FloodWait
