import asyncio
import time
from pyrogram.types import ChatPrivileges
from pyrogram.enums import ChatMemberStatus
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
        """Add or remove bots from channel with retry logic and rate limiting"""
        if not bots_list:
            return [], []
        
        success, failed = [], []
        
        privileges = ChatPrivileges(
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True
        )
        
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

            # 2. Process Bot
            delay_applied = False
            
            try:
                if action == "add":
                    LOGGER.info(f"[BOT_MANAGER] [{i+1}/{len(bots_list)}] Adding {username}")
                    
                    # Try Adding
                    try:
                        await Clients.user_app.add_chat_members(chat_id, username)
                        await asyncio.sleep(0.5)
                    except UserAlreadyParticipant:
                        pass
                    except Exception as e:
                        LOGGER.debug(f"Add member failed ({username}): {e}")

                    # Try Promoting with Retry Logic
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            await Clients.user_app.promote_chat_member(
                                chat_id, 
                                username, 
                                privileges=privileges
                            )
                            success.append(username)
                            LOGGER.info(f"[BOT_MANAGER] ✅ {username} promoted")
                            break # Success, exit retry loop
                            
                        except RightForbidden:
                            # 403: We probably can't edit this bot because owner added it
                            # Check if it is ALREADY an admin
                            try:
                                m = await Clients.user_app.get_chat_member(chat_id, username)
                                if m.status == ChatMemberStatus.ADMINISTRATOR:
                                    LOGGER.warning(f"[BOT_MANAGER] ⚠️ {username} is already admin (Owner protected). Skipping.")
                                    success.append(username) # Count as success since it is there
                                    break
                            except:
                                pass
                            
                            LOGGER.error(f"[BOT_MANAGER] ❌ Permission denied for {username}")
                            failed.append(username)
                            break
                            
                        except ChatAdminRequired:
                            # 400: Helper not recognized as admin yet?
                            if attempt < max_retries - 1:
                                LOGGER.warning(f"[BOT_MANAGER] 🔄 ChatAdminRequired for {username}, retrying in 3s...")
                                await asyncio.sleep(3)
                            else:
                                LOGGER.error(f"[BOT_MANAGER] ❌ Failed {username} after retries")
                                failed.append(username)
                        
                        except FloodWait as fw:
                            LOGGER.warning(f"[BOT_MANAGER] ⏳ FloodWait {fw.value}s")
                            await asyncio.sleep(fw.value + 1)
                            delay_applied = True
                            # Don't break, retry loop continues
                            
                        except Exception as e:
                            LOGGER.error(f"[BOT_MANAGER] ❌ Error {username}: {e}")
                            failed.append(username)
                            break

                elif action == "remove":
                    # ... (Remove logic remains largely the same, usually less prone to errors)
                    try:
                        await Clients.user_app.promote_chat_member(
                            chat_id, username, privileges=ChatPrivileges()
                        )
                        await Clients.user_app.ban_chat_member(chat_id, username)
                        await Clients.user_app.unban_chat_member(chat_id, username)
                        success.append(username)
                    except Exception as e:
                        LOGGER.error(f"Remove failed {username}: {e}")
                        failed.append(username)

            finally:
                # Safety Delay
                if not delay_applied and i < len(bots_list) - 1:
                    await asyncio.sleep(Config.SYNC_ACTION_DELAY)

        return success, failed
