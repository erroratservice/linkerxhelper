import asyncio
from pyrogram.types import ChatPrivileges
from pyrogram.errors import FloodWait, ChatAdminRequired, UserAlreadyParticipant
from bot.client import Clients
from config import Config
from bot.utils.logger import LOGGER

class BotManager:
    @staticmethod
    async def process_bots(chat_id, action, bots_list, status_msg=None):
        """Add or remove bots from channel with guaranteed safety delays"""
        if not bots_list:
            return [], []
        
        success, failed = [], []
        
        # Minimal privileges for bots - ONLY message management
        privileges = ChatPrivileges(
            can_post_messages=True,      # Post messages in channel
            can_edit_messages=True,       # Edit messages
            can_delete_messages=True      # Delete messages
        )
        
        LOGGER.info(f"[BOT_MANAGER] Processing {len(bots_list)} bots with {Config.SYNC_ACTION_DELAY}s delay between each")
        
        for i, username in enumerate(bots_list):
            # Update status message
            if status_msg:
                try:
                    await status_msg.edit(
                        f"⚙️ **Processing...**\n"
                        f"🤖 Bot: `{username}`\n"
                        f"📊 Progress: {i+1}/{len(bots_list)}"
                    )
                except:
                    pass
            
            # Flag to track if we need to apply delay
            delay_applied = False
            
            try:
                if action == "add":
                    LOGGER.info(f"[BOT_MANAGER] [{i+1}/{len(bots_list)}] Adding {username} to {chat_id}")
                    
                    # Step 1: Try to add bot as member first
                    try:
                        await Clients.user_app.add_chat_members(chat_id, username)
                        LOGGER.info(f"[BOT_MANAGER] Added {username} as member")
                        await asyncio.sleep(0.5)  # Small delay between add and promote
                    except UserAlreadyParticipant:
                        LOGGER.info(f"[BOT_MANAGER] {username} already in channel")
                    except Exception as e:
                        LOGGER.debug(f"[BOT_MANAGER] add_chat_members({username}) error: {e}")
                        # Bot might already be in channel, continue to promote
                    
                    # Step 2: Promote to admin with minimal privileges
                    LOGGER.info(f"[BOT_MANAGER] Promoting {username} with message management rights")
                    await Clients.user_app.promote_chat_member(
                        chat_id, 
                        username, 
                        privileges=privileges
                    )
                    success.append(username)
                    LOGGER.info(f"[BOT_MANAGER] ✅ {username} promoted successfully")
                
                elif action == "remove":
                    LOGGER.info(f"[BOT_MANAGER] [{i+1}/{len(bots_list)}] Removing {username} from {chat_id}")
                    
                    # Demote to regular member (removes all admin rights)
                    await Clients.user_app.promote_chat_member(
                        chat_id, 
                        username, 
                        privileges=ChatPrivileges()  # Empty = no permissions
                    )
                    
                    # Remove from channel
                    await Clients.user_app.ban_chat_member(chat_id, username)
                    await Clients.user_app.unban_chat_member(chat_id, username)
                    success.append(username)
                    LOGGER.info(f"[BOT_MANAGER] ✅ {username} removed successfully")
            
            except FloodWait as fw:
                LOGGER.warning(f"[BOT_MANAGER] ⏳ FloodWait {fw.value}s for {username}")
                await asyncio.sleep(fw.value + 1)
                delay_applied = True  # FloodWait delay counts as our delay
                
                # Retry after FloodWait
                try:
                    if action == "add":
                        await Clients.user_app.promote_chat_member(
                            chat_id, 
                            username, 
                            privileges=privileges
                        )
                        success.append(username)
                        LOGGER.info(f"[BOT_MANAGER] ✅ {username} promoted after FloodWait")
                    else:
                        await Clients.user_app.promote_chat_member(
                            chat_id, 
                            username, 
                            privileges=ChatPrivileges()
                        )
                        success.append(username)
                        LOGGER.info(f"[BOT_MANAGER] ✅ {username} demoted after FloodWait")
                except Exception as e:
                    LOGGER.error(f"[BOT_MANAGER] ❌ Retry failed for {username}: {e}")
                    failed.append(username)
            
            except ChatAdminRequired as e:
                LOGGER.error(f"[BOT_MANAGER] ❌ ChatAdminRequired for {username}: {e}")
                failed.append(username)
            
            except Exception as e:
                LOGGER.error(f"[BOT_MANAGER] ❌ Failed {username}: {type(e).__name__} - {e}")
                failed.append(username)
            
            finally:
                # CRITICAL: Always apply delay between bots (unless FloodWait already applied one)
                if not delay_applied and i < len(bots_list) - 1:  # Skip delay after last bot
                    LOGGER.info(f"[BOT_MANAGER] ⏳ Safety delay: {Config.SYNC_ACTION_DELAY}s before next bot")
                    await asyncio.sleep(Config.SYNC_ACTION_DELAY)
        
        LOGGER.info(f"[BOT_MANAGER] ✅ Completed: {len(success)} success, {len(failed)} failed")
        return success, failed
