import asyncio
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
    """Main setup logic - executed by queue worker"""
    try:
        # Step 1: Ensure helper is in channel
        is_member = await ChannelManager.check_helper_membership(chat_id)
        if not is_member:
            await message.edit("➕ **Preparing helper account...**")
            await ChannelManager.add_helper_to_channel(chat_id)
            await asyncio.sleep(2)
        else:
            LOGGER.info(f"Helper already in channel {chat_id}")
        
        # Step 2: Add bots
        await message.edit("🤖 **Adding bots...**")
        successful, failed = await BotManager.process_bots(
            chat_id, "add", Config.BOTS_TO_ADD, message
        )
        
        # Step 3: Save to database
        await Database.save_setup(chat_id, owner_id, successful)
        
        # Step 4: Get stats and send completion message
        active_count = await Database.get_active_channel_count()
        text = (
            f"✅ **Setup complete!**\n\n"
            f"📢 Channel: `{chat_id}`\n"
            f"🤖 Added: {len(successful)}/{len(Config.BOTS_TO_ADD)}\n"
            f"📊 Helper active in: {active_count}/{Config.MAX_USER_CHANNELS} channels\n"
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
        
        await message.edit(text)
        LOGGER.info(f"✅ Setup completed for channel {chat_id}")
    
    except Exception as e:
        LOGGER.error(f"Setup error in {chat_id}: {e}")
        raise

@Clients.bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    """Setup command handler"""
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Invalid format**\n\n"
            "Usage: `/setup <channel_id>`\n\n"
            "💡 Tip: Use @username_to_id_bot to get your channel ID"
        )
        return
    
    raw_id = message.command[1]
    try:
        target_chat = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
    except:
        await message.reply_text("❌ Invalid channel ID format. Please check and try again.")
        return
    
    status = await message.reply_text("🔍 **Checking bot permissions...**")
    
    # Check bot permissions with debug logging
    try:
        member = await Clients.bot.get_chat_member(target_chat, "me")
        LOGGER.info(f"Bot member in {target_chat}: status={member.status}, privileges={member.privileges}")
        
        is_admin = member.status in ("administrator", "creator")
        has_promote_flag = bool(getattr(member.privileges, "can_promote_members", False))
        
        # Relaxed check - any admin is allowed
        can_promote = is_admin
        
        if is_admin and not has_promote_flag:
            LOGGER.warning(
                f"Bot is admin in {target_chat} but can_promote_members is False/None; "
                "continuing, real rights will be validated on add/promote."
            )
        
        if not can_promote:
            bot_username = await Clients.get_bot_username()
            bot_mention = f"@{bot_username}" if bot_username else "the bot"
            await status.edit(
                f"⚠️ **Missing permissions!**\n\n"
                f"{bot_mention} must be an admin in the channel with **Add New Admins** permission enabled.\n\n"
                f"Please update permissions and try again."
            )
            return
    
    except UserNotParticipant:
        bot_username = await Clients.get_bot_username()
        bot_mention = f"@{bot_username}" if bot_username else "the bot"
        await status.edit(
            f"⚠️ **Bot not in channel!**\n\n"
            f"Please add {bot_mention} to the channel first,\n"
            f"then promote it to admin with **Add New Admins** permission."
        )
        return
    
    except Exception as e:
        LOGGER.error(f"Bot permission check error: {e}")
        await status.edit(f"❌ **Error checking permissions:**\n`{e}`")
        return
    
    # Add to queue for processing
    await queue_manager.add_to_queue(status, target_chat, message.from_user.id, setup_logic)
