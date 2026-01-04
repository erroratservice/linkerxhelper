import asyncio
from datetime import datetime
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, UserAlreadyParticipant, InviteRequestSent
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

class ChannelManager:
    @staticmethod
    async def manage_capacity(new_channel_id):
        """Manage helper channel capacity to avoid spam flags"""
        active_count = await Database.get_active_channel_count()
        if active_count < Config.MAX_USER_CHANNELS:
            return
        
        LOGGER.warning(f"⚠️ Helper at limit ({active_count}/{Config.MAX_USER_CHANNELS}), freeing oldest")
        oldest = await Database.get_oldest_channel(exclude_id=new_channel_id)
        
        if oldest:
            try:
                await Clients.user_app.leave_chat(oldest["channel_id"])
                await Database.update_channel_membership(oldest["channel_id"], False)
                LOGGER.info(f"✅ Left oldest channel {oldest['channel_id']}")
                await asyncio.sleep(2)
            except Exception as e:
                LOGGER.error(f"Failed to leave channel {oldest['channel_id']}: {e}")
    
    @staticmethod
    async def add_helper_to_channel(chat_id):
        """Add helper account to channel using invite link method"""
        helper_id = await Clients.get_helper_user_id()
        if not helper_id:
            raise RuntimeError("Helper user ID unavailable")
        
        # Check if already member
        try:
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            if member.status in ("member", "administrator", "creator"):
                await Database.update_channel_membership(chat_id, True)
                LOGGER.info(f"✅ Helper already in channel {chat_id}")
                return True
        except UserNotParticipant:
            pass
        
        # Manage capacity before joining (spam protection)
        await ChannelManager.manage_capacity(chat_id)
        
        # Method 1: Try direct add first
        try:
            await Clients.bot.add_chat_members(chat_id, helper_id)
            await Database.update_channel_membership(chat_id, True, datetime.utcnow())
            LOGGER.info(f"✅ Helper added to channel {chat_id} via direct add")
            await asyncio.sleep(2)
            return True
        except UserAlreadyParticipant:
            LOGGER.info(f"Helper already in channel {chat_id}")
            await Database.update_channel_membership(chat_id, True, datetime.utcnow())
            return True
        except (ChatAdminRequired, Exception) as e:
            LOGGER.warning(f"Direct add failed: {e}. Trying invite link method...")
        
        # Method 2: Create invite link and join via user account
        try:
            # Create an invite link
            invite_link = await Clients.bot.export_chat_invite_link(chat_id)
            LOGGER.info(f"Created invite link for {chat_id}")
            
            # Helper joins via the link
            try:
                await Clients.user_app.join_chat(invite_link)
                await Database.update_channel_membership(chat_id, True, datetime.utcnow())
                LOGGER.info(f"✅ Helper joined channel {chat_id} via invite link")
                await asyncio.sleep(2)
                return True
            except InviteRequestSent:
                raise RuntimeError(
                    "Channel requires join approval. Please manually approve the join request "
                    "from @HelpingYouSetup or disable join approval for this setup."
                )
        except ChatAdminRequired:
            raise RuntimeError(
                "Bot lacks permission to create invite links. "
                "Please ensure the bot has 'Invite Users via Link' permission."
            )
        except Exception as e:
            LOGGER.error(f"Invite link method failed: {e}")
            raise RuntimeError(
                f"Could not add helper to channel. Error: {str(e)}\n"
                f"Please ensure:\n"
                f"1. Bot has 'Add New Admins' permission\n"
                f"2. Bot has 'Invite Users via Link' permission\n"
                f"3. Channel doesn't require join approval"
            )
    
    @staticmethod
    async def check_helper_membership(chat_id):
        """Check if helper is in channel"""
        try:
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            return member.status in ("member", "administrator", "creator")
        except UserNotParticipant:
            return False
        except Exception as e:
            LOGGER.error(f"Error checking membership in {chat_id}: {e}")
            return False
