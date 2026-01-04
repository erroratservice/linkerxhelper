import asyncio
import time
from pyrogram.types import ChatPrivileges
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter
from pyrogram.errors import (
    FloodWait, 
    ChatAdminRequired, 
    UserAlreadyParticipant, 
    RightForbidden,
    UserNotParticipant
)
from bot.client import Clients
from config import Config
from bot.utils.logger import LOGGER

class BotManager:
    @staticmethod
    async def process_bots(chat_id, action, bots_list, status_msg=None):
        """
        Add/Remove bots using Hybrid Approach:
        - READ (Fetch Admins): Done by BOT (Save User limits)
        - WRITE (Add/Promote): Done by USERBOT (Bot API restrictions)
        """
        if not bots_list:
            return [], []
        
        success, failed = [], []
        
        privileges = ChatPrivileges(
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True
        )
        
        # --- OPTIMIZATION: Fetch existing admins using BOT client ---
        # We offload this 'Read' operation to the Bot to save Userbot limits
        existing_admins = set()
        if action == "add":
            try:
                LOGGER.info(f"[BOT_MANAGER] Fetching existing admins (via Bot)...")
                async for member in Clients.bot.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                    if member.user and member.user.username:
                        existing_admins.add(member.user.username.lower())
                LOGGER.info(f"[BOT_MANAGER] Found {len(existing_admins)} existing admins.")
            except Exception as e:
                LOGGER.warning(f"[BOT_MANAGER] Could not fetch admins: {e}")
        # -------------------------------------------------------------

        LOGGER.info(f"[BOT_MANAGER] Processing {len(bots_list)} bots with {Config.SYNC_ACTION_DELAY}s delay")
        
        last_update_time = 0
        
        for i, username in enumerate(bots_list):
            # 1. Update Status Message (Every 15s)
            current_time = time.time()
            if status_msg and (current_time - last_update_time >= 15):
                try:
                    await status_msg.edit(
                        f"⚙️ **Processing...**\n"
                        f"🤖 Bot: `{username}`\n"
                        f"📊 Progress: {i+1}/{len(bots_list)}"
                    )
                    last_update_time = current_time
                except Exception:
                    pass

            # --- SMART SKIP CHECK ---
            if action == "add":
                clean_name = username.lstrip("@").lower()
                if clean_name in existing_admins:
                    LOGGER.info(f"[BOT_MANAGER] ⏩ {username} is already admin. Skipping.")
                    success.append(username)
                    continue  # Skip API calls completely
            # ------------------------

            # 2. Process Bot (Using USERBOT)
            delay_applied = False
            
            try:
                if action == "add":
                    LOGGER.info(f"[BOT_MANAGER] [{i+1}/{len(bots_list)}] Adding {username}")
                    
                    # Step A: Try Adding (Must be Userbot)
                    try:
                        await Clients.user_app.add_chat_members(chat_id, username)
                        await asyncio.sleep(0.5)
                    except UserAlreadyParticipant:
                        pass
                    except Exception as e:
                        LOGGER.debug(f"Add member failed ({username}): {e}")

                    # Step B: Try Promoting (Must be Userbot)
                    max_retries = 6
                    for attempt in range(max_retries):
                        try:
                            await Clients.user_app.promote_chat_member(
                                chat_id, 
                                username, 
                                privileges=privileges
                            )
                            success.append(username)
                            LOGGER.info(f"[BOT_MANAGER] ✅ {username} promoted")
                            break # Success
                            
                        except RightForbidden:
                            # 403: Bot likely already admin (protected)
                            success.append(username)
                            break
                            
                        except ChatAdminRequired:
                            # 400: Helper not recognized as admin yet
                            if attempt < max_retries - 1:
                                LOGGER.warning(f"[BOT_MANAGER] 🔄 ChatAdminRequired, retrying... ({attempt+1}/{max_retries})")
                                await asyncio.sleep(5)
                            else:
                                LOGGER.error(f"[BOT_MANAGER] ❌ Failed {username} after retries")
                                failed.append(username)
                        
                        except FloodWait as fw:
                            LOGGER.warning(f"[BOT_MANAGER] ⏳ FloodWait {fw.value}s")
                            await asyncio.sleep(fw.value + 2)
                            delay_applied = True
                            
                        except Exception as e:
                            LOGGER.error(f"[BOT_MANAGER] ❌ Error {username}: {e}")
                            failed.append(username)
                            break

                elif action == "remove":
                    LOGGER.info(f"[BOT_MANAGER] Removing {username}")
                    try:
                        await Clients.user_app.promote_chat_member(
                            chat_id, username, privileges=ChatPrivileges()
                        )
                        await Clients.user_app.ban_chat_member(chat_id, username)
                        await Clients.user_app.unban_chat_member(chat_id, username)
                        success.append(username)
                        LOGGER.info(f"[BOT_MANAGER] ✅ {username} removed")
                    except Exception as e:
                        LOGGER.error(f"Remove failed {username}: {e}")
                        failed.append(username)

            finally:
                # Safety Delay
                if not delay_applied and i < len(bots_list) - 1:
                    await asyncio.sleep(Config.SYNC_ACTION_DELAY)

        return success, failed
