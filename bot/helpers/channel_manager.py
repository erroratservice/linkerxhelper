import asyncio
from pyrogram import enums
from pyrogram.errors import (
    UserAlreadyParticipant, 
    InviteHashExpired, 
    UsernameInvalid,
    FloodWait,
    UserNotParticipant
)
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

class ChannelManager:
    
    @staticmethod
    async def check_helper_membership(chat_id):
        """Check if helper is part of the chat"""
        try:
            await Clients.user_app.get_chat_member(chat_id, "me")
            return True
        except:
            return False

    @staticmethod
    async def add_helper_to_channel(chat_id, status_message=None):
        """
        Add helper to channel with ROBUST LIMIT ENFORCEMENT.
        Loops until space is available before joining.
        """
        
        # =================================================================
        # 1. THE "RECHECK" CLEANUP LOOP
        # =================================================================
        while True:
            # Re-check count from DB every iteration
            current_count = await Database.get_active_channel_count()
            
            # If we are safely under the limit, break the loop and join
            if current_count < Config.MAX_USER_CHANNELS:
                break
            
            LOGGER.info(f"⚠️ Limit Hit: ({current_count}/{Config.MAX_USER_CHANNELS}). Cleaning oldest...")
            
            # Fetch oldest channel (Exclude the one we are trying to set up!)
            oldest_channel = await Database.get_oldest_channel(exclude_id=chat_id)
            
            if not oldest_channel:
                LOGGER.warning("🚨 Limit reached but DB returned no eligible channel to leave!")
                break

            old_id = oldest_channel.get("channel_id")
            
            # --- NOTIFY BOT OWNER (You) BEFORE LEAVING ---
            try:
                # 1. Generate Invite Link
                invite_back = await Clients.bot.export_chat_invite_link(old_id)
                
                # 2. Get Basic Info
                chat_info = await Clients.bot.get_chat(old_id)
                chat_title = chat_info.title if chat_info else "Unknown Channel"
                
                # 3. Get Stats (Videos & Documents)
                try:
                    video_count = await Clients.bot.search_messages_count(
                        chat_id=old_id, 
                        filter=enums.MessagesFilter.VIDEO
                    )
                    doc_count = await Clients.bot.search_messages_count(
                        chat_id=old_id, 
                        filter=enums.MessagesFilter.DOCUMENT
                    )
                except Exception:
                    video_count = "N/A"
                    doc_count = "N/A"

                # 4. Send Report to Owner
                await Clients.bot.send_message(
                    Config.OWNER_ID,
                    f"🗑 **Auto-Cleanup Notification**\n\n"
                    f"⚠️ **Limit Reached:** `{current_count}/{Config.MAX_USER_CHANNELS}`\n"
                    f"♻️ **Leaving Oldest Channel:**\n"
                    f"📌 Name: **{chat_title}**\n"
                    f"🆔 ID: `{old_id}`\n\n"
                    f"📊 **Channel Stats:**\n"
                    f"🎥 Videos: `{video_count}`\n"
                    f"📂 Documents: `{doc_count}`\n\n"
                    f"🔗 **Invite Link (Saved):**\n{invite_back}"
                )
            except Exception as e:
                LOGGER.error(f"Failed to send backup invite to Owner for {old_id}: {e}")

            # --- ATTEMPT TO LEAVE ---
            try:
                LOGGER.info(f"♻️ Leaving oldest channel: {old_id}")
                await Clients.user_app.leave_chat(old_id)
                LOGGER.info(f"✅ Left {old_id}")
            except UserNotParticipant:
                LOGGER.info(f"⚠️ Already left {old_id} (Syncing DB...)")
            except FloodWait as e:
                LOGGER.warning(f"⏳ FloodWait during cleanup: {e.value}s")
                await asyncio.sleep(e.value)
            except Exception as e:
                LOGGER.error(f"❌ Failed to leave {old_id}: {e}")

            # CRITICAL: Always update DB to "False" so the count decreases
            await Database.update_channel_membership(old_id, False)
            
            # Safety Cooldown (5s)
            LOGGER.info("⏳ Cooling down 5s before next check...")
            await asyncio.sleep(5)

        # =================================================================
        # 2. JOIN NEW CHANNEL (Now safe to proceed)
        # =================================================================
        invite_link = None
        try:
            invite_link = await Clients.bot.export_chat_invite_link(chat_id)
        except Exception as e:
            LOGGER.error(f"Failed to create invite link: {e}")
            raise e

        try:
            # Handle "+" style links
            if "+" in invite_link:
                try:
                    await Clients.user_app.join_chat(invite_link)
                except UserAlreadyParticipant:
                    pass
            else:
                # Handle t.me/joinchat/... style links
                hash_part = invite_link.split("/")[-1]
                try:
                    await Clients.user_app.join_chat(hash_part)
                except UserAlreadyParticipant:
                    pass
            
            LOGGER.info(f"✅ Helper joined {chat_id}")
            
            # Promote Helper
            try:
                bot_me = await Clients.bot.get_chat_member(chat_id, "me")
                helper_me = await Clients.user_app.get_me()
                
                if bot_me.privileges:
                    await Clients.bot.promote_chat_member(
                        chat_id=chat_id,
                        user_id=helper_me.id,
                        privileges=bot_me.privileges
                    )
                    LOGGER.info(f"✅ Helper promoted in {chat_id}")
            except Exception as e:
                LOGGER.warning(f"Failed to promote helper (might not be admin): {e}")

            # Mark as Active in DB
            await Database.update_channel_membership(chat_id, True, joined_at=None)

        except FloodWait as e:
            LOGGER.warning(f"FloodWait joining {chat_id}: {e.value}s")
            if status_message:
                await status_message.edit(f"⏳ **Rate Limited.** Waiting {e.value}s...")
            await asyncio.sleep(e.value)
            raise e
        except Exception as e:
            LOGGER.error(f"Failed to join {chat_id}: {e}")
            raise e
