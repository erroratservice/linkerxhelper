import asyncio
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import (
    UserNotParticipant, 
    ChatAdminRequired, 
    ChannelInvalid, 
    PeerIdInvalid,
    UsernameInvalid,
    FloodWait,
    UserAlreadyParticipant  # <--- Added this missing import
)
from bot.client import Clients
from bot.utils.logger import LOGGER
from config import Config

class ChannelManager:
    @staticmethod
    async def check_helper_membership(chat_id):
        """Check if helper is in the channel"""
        try:
            # We check if the 'me' (helper) is a participant
            await Clients.user_app.get_chat_member(chat_id, "me")
            return True
        except (UserNotParticipant, ChannelInvalid, PeerIdInvalid, UsernameInvalid):
            # These errors all imply the helper is NOT in the channel
            return False
        except Exception as e:
            LOGGER.error(f"[CHECK_MEMBERSHIP] Error for {chat_id}: {e}")
            return False

    @staticmethod
    async def add_helper_to_channel(chat_id):
        """Add helper to channel using invite link with FloodWait handling"""
        LOGGER.info(f"[ADD_HELPER] Starting for channel {chat_id}")
        
        # Get helper's user ID
        helper_me = await Clients.user_app.get_me()
        helper_id = helper_me.id
        
        # 1. Add via Invite Link (The only reliable method for Bots -> Userbots in Channels)
        try:
            LOGGER.info(f"[ADD_HELPER] Creating invite link...")
            invite_link = await Clients.bot.export_chat_invite_link(chat_id)
            LOGGER.info(f"[ADD_HELPER] Invite link created, joining...")
            
            # FloodWait safe joining
            while True:
                try:
                    await Clients.user_app.join_chat(invite_link)
                    LOGGER.info(f"[ADD_HELPER] ✅ Helper joined via invite link")
                    break
                except FloodWait as e:
                    LOGGER.warning(f"[ADD_HELPER] ⏳ FloodWait {e.value}s during join. Sleeping...")
                    await asyncio.sleep(e.value + 2)
                except UserAlreadyParticipant:
                    LOGGER.info("[ADD_HELPER] Helper already in channel.")
                    break
                except Exception as e:
                    LOGGER.error(f"[ADD_HELPER] Join failed: {e}")
                    raise e
            
        except Exception as e:
            LOGGER.error(f"[ADD_HELPER] ❌ Failed to join via link: {e}")
            raise e

        # 2. Promote Helper
        LOGGER.info(f"[PROMOTE_HELPER] Getting bot's privileges to match")
        try:
            # Get the BOT'S privileges to mirror them to the Helper
            bot_member = await Clients.bot.get_chat_member(chat_id, "me")
            
            if not bot_member.privileges:
                raise ChatAdminRequired("Bot is not an admin or has no privileges!")

            LOGGER.info(f"[PROMOTE_HELPER] Bot privileges: {bot_member.privileges}")
            
            # Promote helper with same privileges
            await Clients.bot.promote_chat_member(
                chat_id,
                helper_id,
                privileges=bot_member.privileges
            )
            LOGGER.info(f"[PROMOTE_HELPER] ✅ Helper promoted")
            
            # Verification wait
            await asyncio.sleep(2)
            
            # Verify
            LOGGER.info(f"[PROMOTE_HELPER] Verifying promotion")
            helper_member = await Clients.bot.get_chat_member(chat_id, helper_id)
            can_promote = getattr(helper_member.privileges, "can_promote_members", False) if helper_member.privileges else False
            
            LOGGER.info(f"[PROMOTE_HELPER] Helper can_promote_members after promotion: {can_promote}")
            
            if not can_promote:
                # Try to patch it specifically if missing
                LOGGER.warning("[PROMOTE_HELPER] Promote permission missing, trying to force update...")
                bot_member.privileges.can_promote_members = True
                await Clients.bot.promote_chat_member(chat_id, helper_id, privileges=bot_member.privileges)
            
        except Exception as e:
            LOGGER.error(f"[PROMOTE_HELPER] ❌ Failed to promote helper: {e}")
            raise e
        
        LOGGER.info(f"[ADD_HELPER] ✅ Complete - helper is admin with promote rights")
