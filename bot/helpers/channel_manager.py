import asyncio
from datetime import datetime
from pyrogram.types import ChatPrivileges
from pyrogram.errors import (
    UserNotParticipant, 
    ChatAdminRequired, 
    UserAlreadyParticipant, 
    InviteRequestSent
)
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

class ChannelManager:
    @staticmethod
    async def manage_capacity(new_channel_id):
        """Manage helper channel capacity to avoid spam flags"""
        active_count = await Database.get_active_channel_count()
        LOGGER.info(f"[CAPACITY] Current active channels: {active_count}/{Config.MAX_USER_CHANNELS}")
        
        if active_count < Config.MAX_USER_CHANNELS:
            LOGGER.info(f"[CAPACITY] Capacity OK, no need to free channels")
            return
        
        LOGGER.warning(f"[CAPACITY] At limit, freeing oldest channel")
        oldest = await Database.get_oldest_channel(exclude_id=new_channel_id)
        
        if oldest:
            try:
                LOGGER.info(f"[CAPACITY] Leaving oldest channel: {oldest['channel_id']}")
                await Clients.user_app.leave_chat(oldest["channel_id"])
                await Database.update_channel_membership(oldest["channel_id"], False)
                LOGGER.info(f"[CAPACITY] ✅ Left channel {oldest['channel_id']}")
                await asyncio.sleep(2)
            except Exception as e:
                LOGGER.error(f"[CAPACITY] ❌ Failed to leave {oldest['channel_id']}: {e}")
    
    @staticmethod
    async def add_helper_to_channel(chat_id):
        """Add helper account to channel and promote to admin"""
        LOGGER.info(f"[ADD_HELPER] Starting for channel {chat_id}")
        
        helper_id = await Clients.get_helper_user_id()
        if not helper_id:
            LOGGER.error(f"[ADD_HELPER] ❌ Helper user ID unavailable")
            raise RuntimeError("Helper user ID unavailable")
        
        LOGGER.info(f"[ADD_HELPER] Helper user ID: {helper_id}")
        
        # Check if already member with proper permissions
        is_already_member = False
        needs_promotion = False
        
        try:
            LOGGER.info(f"[ADD_HELPER] Checking existing membership")
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            LOGGER.info(f"[ADD_HELPER] Existing status: {member.status}")
            LOGGER.info(f"[ADD_HELPER] Existing privileges: {member.privileges}")
            
            if member.status in ("administrator", "creator"):
                is_already_member = True
                can_promote = getattr(member.privileges, "can_promote_members", False) if member.privileges else False
                LOGGER.info(f"[ADD_HELPER] Is admin, can_promote: {can_promote}")
                
                if can_promote or member.status == "creator":
                    # Already has all needed permissions
                    await Database.update_channel_membership(chat_id, True)
                    LOGGER.info(f"[ADD_HELPER] ✅ Already admin with promote rights, done")
                    return True
                else:
                    # Admin but needs promote permission
                    needs_promotion = True
                    LOGGER.info(f"[ADD_HELPER] Is admin but needs promote permission")
            
            elif member.status == "member":
                is_already_member = True
                needs_promotion = True
                LOGGER.info(f"[ADD_HELPER] Is regular member, needs promotion")
        
        except UserNotParticipant:
            LOGGER.info(f"[ADD_HELPER] Not in channel, needs to join")
        except Exception as e:
            LOGGER.error(f"[ADD_HELPER] Error checking membership: {e}")
        
        # If not a member at all, need to join first
        if not is_already_member:
            # Manage capacity
            await ChannelManager.manage_capacity(chat_id)
            
            # Try to join the channel
            LOGGER.info(f"[ADD_HELPER] Attempting to join channel")
            
            # Method 1: Direct add via bot
            try:
                LOGGER.info(f"[ADD_HELPER] Trying bot.add_chat_members()")
                await Clients.bot.add_chat_members(chat_id, helper_id)
                LOGGER.info(f"[ADD_HELPER] ✅ Direct add successful")
                is_already_member = True
                needs_promotion = True
                await asyncio.sleep(2)
            except UserAlreadyParticipant:
                LOGGER.info(f"[ADD_HELPER] Helper already in channel")
                is_already_member = True
                needs_promotion = True
            except Exception as e:
                LOGGER.warning(f"[ADD_HELPER] Direct add failed: {type(e).__name__} - {e}")
                LOGGER.info(f"[ADD_HELPER] Trying invite link method...")
                
                # Method 2: Invite link
                try:
                    invite_link = await Clients.bot.export_chat_invite_link(chat_id)
                    LOGGER.info(f"[ADD_HELPER] Invite link created: {invite_link}")
                    
                    try:
                        await Clients.user_app.join_chat(invite_link)
                        LOGGER.info(f"[ADD_HELPER] ✅ Helper joined via invite link")
                        is_already_member = True
                        needs_promotion = True
                        await asyncio.sleep(2)
                    except UserAlreadyParticipant:
                        LOGGER.info(f"[ADD_HELPER] Helper already participant")
                        is_already_member = True
                        needs_promotion = True
                    except InviteRequestSent:
                        LOGGER.error(f"[ADD_HELPER] ❌ Join request sent (approval required)")
                        raise RuntimeError(
                            "⚠️ Channel requires join approval.\n"
                            "Please manually approve @HelpingYouSetup or disable join approval."
                        )
                except ChatAdminRequired:
                    LOGGER.error(f"[ADD_HELPER] ❌ Bot can't create invite links")
                    raise RuntimeError(
                        "Bot lacks permission to create invite links.\n"
                        "Ensure bot has 'Invite Users via Link' permission."
                    )
                except Exception as e:
                    LOGGER.error(f"[ADD_HELPER] ❌ Invite link method failed: {type(e).__name__} - {e}")
                    raise RuntimeError(f"Could not add helper to channel: {str(e)}")
        
        # Now promote helper to admin (if needed)
        if needs_promotion:
            LOGGER.info(f"[PROMOTE_HELPER] Getting bot's privileges to match")
            try:
                bot_member = await Clients.bot.get_chat_member(chat_id, "me")
                bot_privs = bot_member.privileges
                LOGGER.info(f"[PROMOTE_HELPER] Bot privileges: {bot_privs}")
                
                # Match bot's privileges
                helper_privileges = ChatPrivileges(
                    can_manage_chat=getattr(bot_privs, "can_manage_chat", False),
                    can_delete_messages=getattr(bot_privs, "can_delete_messages", False),
                    can_restrict_members=getattr(bot_privs, "can_restrict_members", False),
                    can_promote_members=getattr(bot_privs, "can_promote_members", False),
                    can_change_info=getattr(bot_privs, "can_change_info", False),
                    can_invite_users=getattr(bot_privs, "can_invite_users", False),
                    can_pin_messages=getattr(bot_privs, "can_pin_messages", False),
                    can_post_messages=getattr(bot_privs, "can_post_messages", False),
                    can_edit_messages=getattr(bot_privs, "can_edit_messages", False),
                    can_manage_video_chats=getattr(bot_privs, "can_manage_video_chats", False)
                )
                
                LOGGER.info(f"[PROMOTE_HELPER] Promoting helper with bot-matched privileges")
                
                await Clients.bot.promote_chat_member(
                    chat_id, 
                    helper_id, 
                    privileges=helper_privileges
                )
                LOGGER.info(f"[PROMOTE_HELPER] ✅ Helper promoted")
                await asyncio.sleep(2)
                
                # Verify promotion
                LOGGER.info(f"[PROMOTE_HELPER] Verifying promotion")
                helper_member = await Clients.user_app.get_chat_member(chat_id, "me")
                can_promote = getattr(helper_member.privileges, "can_promote_members", False) if helper_member.privileges else False
                LOGGER.info(f"[PROMOTE_HELPER] Helper can_promote_members after promotion: {can_promote}")
                
                if not can_promote:
                    LOGGER.error(f"[PROMOTE_HELPER] ❌ Helper promoted but lacks promote permission")
                    raise RuntimeError(
                        "Helper promoted but doesn't have 'Add New Admins' permission.\n"
                        "Please manually give @HelpingYouSetup this permission."
                    )
                
                await Database.update_channel_membership(chat_id, True, datetime.utcnow())
                LOGGER.info(f"[ADD_HELPER] ✅ Complete - helper is admin with promote rights")
                return True
                
            except ChatAdminRequired as e:
                LOGGER.error(f"[PROMOTE_HELPER] ❌ ChatAdminRequired: {e}")
                raise RuntimeError(
                    "Bot cannot promote helper to admin.\n"
                    "Ensure bot has 'Add New Admins' permission enabled."
                )
            except Exception as e:
                LOGGER.error(f"[PROMOTE_HELPER] ❌ Failed: {type(e).__name__} - {e}")
                raise RuntimeError(f"Failed to promote helper: {str(e)}")
        
        # If we reach here with is_already_member but no promotion needed
        if is_already_member:
            await Database.update_channel_membership(chat_id, True)
            LOGGER.info(f"[ADD_HELPER] ✅ Helper already properly configured")
            return True
        
        return False
    
    @staticmethod
    async def check_helper_membership(chat_id):
        """Check if helper is in channel"""
        try:
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            is_member = member.status in ("member", "administrator", "creator")
            LOGGER.debug(f"[CHECK_MEMBERSHIP] Channel {chat_id}: {is_member} (status: {member.status})")
            return is_member
        except UserNotParticipant:
            LOGGER.debug(f"[CHECK_MEMBERSHIP] Channel {chat_id}: Not a participant")
            return False
        except Exception as e:
            LOGGER.error(f"[CHECK_MEMBERSHIP] Error for {chat_id}: {e}")
            return False
