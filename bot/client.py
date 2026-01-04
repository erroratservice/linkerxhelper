from pyrogram import Client
from config import Config

class Clients:
    bot = None
    user_app = None
    _bot_username_cache = None
    _helper_username_cache = None
    
    @staticmethod
    def initialize():
        """Initialize Pyrogram clients"""
        Clients.bot = Client(
            "bot_client",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=Config.BOT_TOKEN,
            in_memory=True
        )
        Clients.user_app = Client(
            "user_client",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.USER_SESSION,
            in_memory=True
        )
    
    @staticmethod
    async def get_bot_username():
        """Get cached bot username"""
        if Clients._bot_username_cache:
            return Clients._bot_username_cache
        try:
            me = await Clients.bot.get_me()
            Clients._bot_username_cache = me.username
            return Clients._bot_username_cache
        except Exception:
            return None
    
    @staticmethod
    async def get_helper_username():
        """Get cached helper username"""
        if Clients._helper_username_cache:
            return Clients._helper_username_cache
        try:
            me = await Clients.user_app.get_me()
            Clients._helper_username_cache = me.username
            return Clients._helper_username_cache
        except Exception:
            return None
    
    @staticmethod
    async def get_helper_user_id():
        """Get helper user ID"""
        try:
            me = await Clients.user_app.get_me()
            return me.id
        except Exception:
            return None
