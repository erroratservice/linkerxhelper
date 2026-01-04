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
        """Add helper account to channel and promote to admin"""
        helper_id = await Clients.get_helper_user_id()
        if not helper_id:
            raise RuntimeError("Helper user ID unavailable")
        
        # Check if already member
        try:
            member = await Clients.user_app.get_chat_member(chat_id, "me")
            if member.status in ("member", "administrator", "creator"):
                # If already admin with promote rights, we're done
                if member.status in ("administrator", "creator"):
                    is_admin = True
                    can_promote = getattr(member.privileges, "can_promote_members", False) if member.privileges else False
                    if can_promote or member.status == "creator":
                        await Database.update_channel_membership(chat_id, True)
                        LOGGER.info(f"✅ Helper already admin in channel {chat_id}")
                        return True
                    else:
                        LOGGER.info(f"Helper is admin but lacks promote permission, will re-promote")
                else:
                    # Just a member, need to promote
                    LOGGER.info(f"Helper is member, needs promotion in {chat_id}")
        except UserNotParticipant:
            pass  # Need to join
        
        # Manage capacity before joining (spam protection)
        await ChannelManager.manage_capacity(chat_id)
        
        # Step 1: Try to add helper directly using bot
        helper_added = False
        try:
            await Clients.bot.add_chat_members(chat_id, helper_id)
            helper_added = True
            LOGGER.info(f"✅ Helper added to channel {chat_id} via direct add")
            await asyncio.sleep(2)
        except UserAlreadyParticipant:
            helper_added = True
            LOGGER.info(f"Helper already in channel {chat_id}")
        except (ChatAdminRequired, Exception) as e:
            LOGGER.warning(f"Direct add failed: {e}. Trying invite link method...")
        
        # Step 2: If direct add failed, use invite link
        if not helper_added:
            try:
                # Create an invite link
                invite_link = await Clients.bot.export_chat_invite_link(chat_id)
                LOGGER.info(f"Created invite link for {chat_id}")
                
                # Helper joins via the link
                try:
                    await Clients.user_app.join_chat(invite_link)
                    helper_added = True
                    LOGGER.info(f"✅ Helper joined channel {chat_id} via invite link")
                    await asyncio.sleep(2)
                except InviteRequestSent:
                    raise RuntimeError(
                        "⚠️ Channel requires join approval.\n"
                        "Please manually approve the join request from @HelpingYouSetup "
                        "or disable join approval for this setup."
                    )
            except ChatAdminRequired:
                raise RuntimeError(
                    "Bot lacks permission to create invite links.\n"
                    "Please ensure the bot has 'Invite Users via Link' permission."
                )
            except Exception as e:
                LOGGER.error(f"Invite link method failed: {e}")
                raise RuntimeError(
                    f"Could not add helper to channel. Error: {str(e)}\n\n"
                    f"Please ensure:\n"
                    f"• Bot has 'Add New Admins' permission\n"
                    f"• Bot has 'Invite Users via Link' permission\n"
                    f"• Channel doesn't require join approval"
                )
        
        # Step 3: Now promote helper to admin with necessary permissions
        if helper_added:
            try:
                # Define privileges needed for helper to do its job
                helper_privileges = ChatPrivileges(
                    can_manage_chat=True,
                    can_invite_users=True,
                    can_promote_members=True,  # Critical: needed to promote bots
                    can_restrict_members=True
                )
                
                await Clients.bot.promote_chat_member(
                    chat_id, 
                    helper_id, 
                    privileges=helper_privileges
                )
                LOGGER.info(f"✅ Helper promoted to admin in {chat_id}")
                await asyncio.sleep(2)
                
                # Update database
                await Database.update_channel_membership(chat_id, True, datetime.utcnow())
                return True
                
            except ChatAdminRequired:
                raise RuntimeError(
                    "Bot cannot promote helper to admin.\n"
                    "Ensure bot has 'Add New Admins' permission enabled."
                )
            except Exception as e:
                LOGGER.error(f"Failed to promote helper: {e}")
                raise RuntimeError(f"Failed to promote helper to admin: {str(e)}")
        
        return False
    
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
