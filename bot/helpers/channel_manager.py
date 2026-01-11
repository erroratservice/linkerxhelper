import asyncio
from pyrogram import enums
from pyrogram.errors import (
    UserAlreadyParticipant, 
    InviteHashExpired, 
    UsernameInvalid,
    FloodWait,
    UserNotParticipant,
    ChannelInvalid,
    PeerIdInvalid,
    ChannelPrivate
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
        Features:
        1. Auto-Cleanup with Max 3 Retries (Fast & Efficient).
        2. Handles DELETED/INVALID channels gracefully.
        3. Uses BOT SESSION for stats to save limits.
        """
        
        # =================================================================
        # 1. CLEANUP LOOP (Max 3 Retries)
        # =================================================================
        
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
                
                # --- GATHER INFO USING BOT API ---
                # We assume defaults in case channel is inaccessible/deleted
                video_count = "N/A"
                doc_count = "N/A"
                chat_title = "Unknown/Deleted Channel"
                invite_back = "Unavailable"

                # Try to fetch info. If channel is deleted, these will fail silently.
                try:
                    chat_info = await Clients.bot.get_chat(old_id)
                    chat_title = chat_info.title
                    
                    # 1. Video Count
                    try:
                        video_count = await Clients.bot.search_messages_count(
                            chat_id=old_id, 
                            filter=enums.MessagesFilter.VIDEO
                        )
                    except: pass

                    # 2. Document Count
                    try:
                        doc_count = await Clients.bot.search_messages_count(
                            chat_id=old_id, 
                            filter=enums.MessagesFilter.DOCUMENT
                        )
                    except: pass
                    
                    # 3. Invite Link
                    try:
                        invite_back = await Clients.bot.export_chat_invite_link(old_id)
                    except: pass
                    
                except (ChannelInvalid, PeerIdInvalid, ChannelPrivate):
                    LOGGER.warning(f"Old channel {old_id} seems inaccessible or deleted.")

                # --- NOTIFY BOT OWNER (You) ---
                try:
                    await Clients.bot.send_message(
                        Config.OWNER_ID,
                        f"🗑 **Auto-Cleanup Notification**\n\n"
                        f"⚠️ **Limit Reached:** `{current_count}/{Config.MAX_USER_CHANNELS}`\n"
                        f"♻️ **Leaving Oldest Channel:**\n"
                        f"📌 Name: **{chat_title}**\n"
                        f"🆔 ID: `{old_id}`\n\n"
                        f"📊 **Stats:**\n"
                        f"🎥 Videos: `{video_count}`\n"
                        f"📂 Documents: `{doc_count}`\n\n"
                        f"🔗 **Backdoor Link:**\n{invite_back}"
                    )
                except Exception as e:
                    LOGGER.error(f"Failed to send backup invite for {old_id}: {e}")

                # --- LEAVE CHANNEL (Userbot) ---
                try:
                    await Clients.user_app.leave_chat(old_id)
                    LOGGER.info(f"✅ Left {old_id}")
                except UserNotParticipant:
                    LOGGER.info(f"⚠️ Already left {old_id}")
                except (ChannelInvalid, PeerIdInvalid, ChannelPrivate):
                    LOGGER.info(f"⚠️ Channel {old_id} is deleted/invalid. Marking as left.")
                except FloodWait as e:
                    LOGGER.warning(f"⏳ FloodWait during leave: {e.value}s")
                    await asyncio.sleep(e.value)
                except Exception as e:
                    LOGGER.error(f"❌ Unknown error leaving {old_id}: {e}")
                
                # CRITICAL: Always update DB to "False" so we don't get stuck on this dead channel
                await Database.update_channel_membership(old_id, False)
                
                # Wait 5s and Loop
                LOGGER.info("⏳ Cooling down 5s...")
                await asyncio.sleep(5)
                
            except Exception as e:
                LOGGER.error(f"❌ Cleanup Loop Error: {e}")
                # Don't crash, just try next iteration or break
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
