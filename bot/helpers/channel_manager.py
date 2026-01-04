import asyncio
from datetime import datetime
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, UserAlreadyParticipant
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

class ChannelManager:
    @staticmethod
    async def manage_capacity(new_channel_id):
        """Manage helper channel capacity"""
        active_count = await Database.get_active_channel_count()
        if active_count < Config.MAX_USER_CHANNELS:
            return
        
        LOGGER.warning(f"Helper at limit ({active_count}/{Config.MAX_USER_CHANNELS})")
        oldest = await Database.get_oldest_channel(exclude_id=new_channel_id)
        
        if oldest:
            try:
                await Clients.user_app.leave_chat(oldest["channel_id"])
                await Database.update_channel_membership(oldest["channel_id"], False)
                LOGGER.info(f"Left oldest channel {oldest['channel_id']}")
                await asyncio.sleep(2)
            except Exception as e:
                LOGGER.error(f"Failed to leave channel: {e}")
    
    @staticmethod
    async def add_helper_to_channel(chat_id):
        """Add helper account to channel"""
        helper_id = await Clients.get_helper_user_id()
        if not helper_id:
            raise RuntimeError("Helper user ID unavailable")
        
        # Check if already member
        try:
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            if member.status in ("member", "administrator", "creator"):
                await Database.update_channel_membership(chat_id, True)
                LOGGER.info(f"Helper already in channel {chat_id}")
                return True
        except UserNotParticipant:
            pass
        
        # Manage capacity before joining
        await ChannelManager.manage_capacity(chat_id)
        
        # Add helper
        try:
            await Clients.bot.add_chat_members(chat_id, helper_id)
        except UserAlreadyParticipant:
            LOGGER.info(f"Helper already in channel {chat_id}")
        except ChatAdminRequired:
            LOGGER.error(f"Bot lacks rights to add helper")
            raise
        except Exception as e:
            LOGGER.error(f"Error adding helper: {e}")
            raise
        
        await Database.update_channel_membership(chat_id, True, datetime.utcnow())
        LOGGER.info(f"Helper added to channel {chat_id}")
        await asyncio.sleep(2)
        return True
    
    @staticmethod
    async def check_helper_membership(chat_id):
        """Check if helper is in channel"""
        try:
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            return member.status in ("member", "administrator", "creator")
        except UserNotParticipant:
            return False
        except Exception as e:
            LOGGER.error(f"Error checking membership: {e}")
            return False
