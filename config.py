import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Telegram API
    API_ID = int(os.environ.get("API_ID", 0))
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    USER_SESSION = os.environ.get("USER_SESSION", "")
    
    # MongoDB
    MONGO_URL = os.environ.get("MONGO_URL", "")
    
    # Owner
    OWNER_ID = int(os.environ.get("OWNER_ID", 0))
    
    # Bot Configuration
    BOTS_TO_ADD = [b.strip() for b in os.environ.get("BOTS_TO_ADD", "").split(",") if b.strip()]
    
    # Safety & Limits
    SYNC_CHANNEL_DELAY = int(os.environ.get("SYNC_CHANNEL_DELAY", 15))
    SYNC_ACTION_DELAY = 4
    MAX_USER_CHANNELS = int(os.environ.get("MAX_USER_CHANNELS", 300))
    
    # Web Server
    PORT = int(os.environ.get("PORT", 8080))
    URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")
    
    # GitHub Update Configuration
    GITHUB_REPO = os.environ.get("GITHUB_REPO", "")  # e.g., "https://github.com/username/linkerx"
    GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
    
    # --- NEW: Permission Guide Image ---
    # File ID or URL of the image showing all permissions enabled.
    # Replace the default URL with the File ID of the screenshot you uploaded to Telegram.
    PERM_GUIDE_PIC = os.environ.get("PERM_GUIDE_PIC", "https://telegra.ph/file/YOUR_IMAGE_URL_HERE.jpg")
    
    @staticmethod
    def validate_bot_usernames(bots):
        """Ensure bot usernames have @ prefix"""
        normalized = []
        for bot in bots:
            bot = bot.strip()
            if not bot:
                continue
            if not bot.startswith("@"):
                bot = f"@{bot}"
            normalized.append(bot)
        return normalized
    
    @staticmethod
    def validate():
        """Validate required environment variables"""
        required = {
            "API_ID": Config.API_ID,
            "API_HASH": Config.API_HASH,
            "BOT_TOKEN": Config.BOT_TOKEN,
            "USER_SESSION": Config.USER_SESSION,
            "MONGO_URL": Config.MONGO_URL,
        }
        missing = [k for k, v in required.items() if not v or (isinstance(v, int) and v == 0)]
        if missing:
            raise ValueError(f"❌ Missing environment variables: {', '.join(missing)}")
        
        if Config.OWNER_ID == 0:
            from bot.utils.logger import LOGGER
            LOGGER.warning("⚠️ OWNER_ID not set - /sync, /stats, and /restart will be disabled")
        
        # Normalize bot usernames
        Config.BOTS_TO_ADD = Config.validate_bot_usernames(Config.BOTS_TO_ADD)
        
        if not Config.BOTS_TO_ADD:
            from bot.utils.logger import LOGGER
            LOGGER.warning("⚠️ No bots configured in BOTS_TO_ADD")
