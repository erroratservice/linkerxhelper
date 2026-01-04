from pyrogram import filters
from pyrogram.errors import UserNotParticipant
from bot.client import Clients
from bot.helpers.queue import queue_manager
from bot.helpers.channel_manager import ChannelManager
from bot.helpers.bot_manager import BotManager
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

async def setup_logic(message, chat_id, owner_id):
    """Main setup logic"""
    try:
        # Ensure helper in channel
        is_member = await ChannelManager.check_helper_membership(chat_id)
        if not is_member:
            await message.edit("➕ **Preparing helper...**")
            await ChannelManager.add_helper_to_channel(chat_id)
        
        # Add bots
        await message.edit("🤖 **Adding bots...**")
        successful, failed = await BotManager.process_bots(
            chat_id, "add", Config.BOTS_TO_ADD, message
        )
        
        # Save to database
        await Database.save_setup(chat_id, owner_id, successful)
        
        active_count = await Database.get_active_channel_count()
        text = (
            f"✅ **Setup complete!**\n\n"
            f"📢 Channel: `{chat_id}`\n"
            f"🤖 Added: {len(successful)}/{len(Config.BOTS_TO_ADD)}\n"
            f"📊 Active: {active_count}/{Config.MAX_USER_CHANNELS}"
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
        await message.edit(text)
    
    except Exception as e:
        LOGGER.error(f"Setup error: {e}")
        raise

@Clients.bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    """Setup command handler"""
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Invalid format.\n\n"
            "Usage: `/setup <channel_id>`"
        )
        return
    
    raw_id = message.command[1]
    try:
        target_chat = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
    except:
        await message.reply_text("❌ Invalid channel ID")
        return
    
    status = await message.reply_text("🔍 **Checking permissions...**")
    
    # Check bot permissions
    try:
        member = await Clients.bot.get_chat_member(target_chat, "me")
        LOGGER.info(f"Bot in {target_chat}: {member.status}, {member.privileges}")
        
        is_admin = member.status in ("administrator", "creator")
        if not is_admin:
            await status.edit("⚠️ Bot must be admin with 'Add New Admins' permission")
            return
    
    except UserNotParticipant:
        await status.edit("⚠️ Bot not in channel. Add it first.")
        return
    except Exception as e:
        LOGGER.error(f"Permission check error: {e}")
        await status.edit(f"❌ Error: `{e}`")
        return
    
    await queue_manager.add_to_queue(status, target_chat, message.from_user.id, setup_logic)
