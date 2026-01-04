from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from bot.helpers.queue import queue_manager
from config import Config
from bot.utils.logger import LOGGER

@Clients.bot.on_message(filters.command("stats") & filters.user(Config.OWNER_ID))
async def stats_handler(client, message):
    """Show global statistics (Owner only)"""
    if Config.OWNER_ID == 0:
        await message.reply_text("❌ This command is disabled (OWNER_ID not set)")
        return
    
    try:
        # Get all statistics
        stats = await Database.get_total_stats()
        if not stats:
            await message.reply_text("❌ Failed to retrieve statistics")
            return
        
        active_memberships = await Database.get_active_channel_count()
        
        # Get bot and helper usernames
        bot_username = await Clients.get_bot_username()
        helper_username = await Clients.get_helper_username()
        
        text = (
            f"📊 **LinkerX Global Statistics**\n\n"
            f"**📺 Channels:**\n"
            f"• Total: {stats['total_channels']}\n"
            f"• Unique Owners: {stats['unique_owners']}\n\n"
            f"**🤖 Bot Installations:**\n"
            f"• Total Installs: {stats['total_bots']}\n"
            f"• Configured Bots: {len(Config.BOTS_TO_ADD)}\n\n"
            f"**⚙️ Queue Status:**\n"
            f"• Queue Size: {queue_manager.queue.qsize()}\n"
            f"• Waiting Users: {len(queue_manager.waiting_users)}\n\n"
            f"**🛡️ Spam Protection:**\n"
            f"• Active Memberships: {active_memberships}/{Config.MAX_USER_CHANNELS}\n"
            f"• Oldest Membership: {stats['oldest_membership']}\n\n"
            f"**👤 Accounts:**\n"
            f"• Bot: @{bot_username or 'N/A'}\n"
            f"• Helper: @{helper_username or 'N/A'}"
        )
        
        await message.reply_text(text)
        LOGGER.info("Stats command executed successfully")
    
    except Exception as e:
        LOGGER.error(f"/stats error: {e}")
        await message.reply_text(f"❌ **Error:** `{e}`")
