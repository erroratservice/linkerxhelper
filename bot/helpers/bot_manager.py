import asyncio
from pyrogram.types import ChatPrivileges
from pyrogram.errors import FloodWait, ChatAdminRequired
from bot.client import Clients
from config import Config
from bot.utils.logger import LOGGER

class BotManager:
    @staticmethod
    async def process_bots(chat_id, action, bots_list, status_msg=None):
        """Add or remove bots from channel"""
        if not bots_list:
            return [], []
        
        success, failed = [], []
        privileges = ChatPrivileges(
            can_manage_chat=True,
            can_delete_messages=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_invite_users=True,
            can_pin_messages=True,
            can_post_messages=True,
            can_edit_messages=True,
        )
        
        for i, username in enumerate(bots_list):
            if status_msg:
                try:
                    await status_msg.edit(
                        f"⚙️ **Processing...**\n"
                        f"🤖 Bot: `{username}`\n"
                        f"📊 Progress: {i+1}/{len(bots_list)}"
                    )
                except:
                    pass
            
            try:
                if action == "add":
                    try:
                        await Clients.user_app.add_chat_members(chat_id, username)
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
                    await Clients.user_app.promote_chat_member(chat_id, username, privileges=privileges)
                    success.append(username)
                
                elif action == "remove":
                    await Clients.user_app.promote_chat_member(
                        chat_id, username, privileges=ChatPrivileges(can_manage_chat=False)
                    )
                    await Clients.user_app.ban_chat_member(chat_id, username)
                    await Clients.user_app.unban_chat_member(chat_id, username)
                    success.append(username)
                
                await asyncio.sleep(Config.SYNC_ACTION_DELAY)
            
            except FloodWait as fw:
                LOGGER.warning(f"FloodWait {fw.value}s for {username}")
                await asyncio.sleep(fw.value + 1)
                try:
                    if action == "add":
                        await Clients.user_app.promote_chat_member(chat_id, username, privileges=privileges)
                        success.append(username)
                except Exception as e:
                    LOGGER.error(f"Retry failed: {e}")
                    failed.append(username)
            
            except ChatAdminRequired as e:
                LOGGER.error(f"ChatAdminRequired for {username}: {e}")
                failed.append(username)
            
            except Exception as e:
                LOGGER.error(f"Failed {username}: {e}")
                failed.append(username)
        
        return success, failed
