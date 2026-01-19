import re
import hashlib

class TextUtils:
    # Regex for Usernames (@name) and Links (http, https, t.me, www)
    REGEX_USERNAME = r'(@\w+)'
    REGEX_LINK = r'((?:https?://|www\.|t\.me/)[\w\-\./\?=&]+)'

    @staticmethod
    def extract_entities(text):
        """Returns set of usernames and links found in text."""
        if not text: return set()
        usernames = set(re.findall(TextUtils.REGEX_USERNAME, text))
        links = set(re.findall(TextUtils.REGEX_LINK, text))
        return usernames.union(links)

    @staticmethod
    def clean_text_logic(text, locked_items):
        """Removes Usernames/Links unless they are in locked_items."""
        if not text: return text
        
        # Helper to decide if match should be kept
        def replace_func(match):
            item = match.group(0)
            # If item is locked, keep it. Else remove it.
            return item if item in locked_items else ""

        # Remove unlocked links
        text = re.sub(TextUtils.REGEX_LINK, replace_func, text)
        # Remove unlocked usernames
        text = re.sub(TextUtils.REGEX_USERNAME, replace_func, text)
        
        # Clean up double spaces resulting from removal
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def compute_hash(text):
        """Creates a fingerprint of the caption to detect duplicates."""
        if not text: return None
        # Normalize: lowercase, remove spaces to match content accurately
        clean = re.sub(r'\s+', '', text.lower())
        return hashlib.sha256(clean.encode()).hexdigest()
