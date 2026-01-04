from pyrogram import filters
from bot.client import Clients
from config import Config

@Clients.bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_handler(client, message):
    """Welcome message"""
    bot_username = await Clients.get_bot_username()
    bot_mention = f"@{bot_username}" if bot_username else "this bot"
    
    await message.reply_text(
        f"👋 **Welcome to LinkerX!**\n\n"
        f"**Setup steps:**\n"
        f"1️⃣ Add {bot_mention} to your channel\n"
        f"2️⃣ Promote it to admin with **Add New Admins** permission\n"
        f"3️⃣ Get channel ID from @username_to_id_bot\n"
        f"4️⃣ Run `/setup <channel_id>`\n\n"
        f"Example: `/setup -100123456789`\n\n"
        f"**Commands:**\n"
        f"• `/setup <channel_id>` - Setup channel\n"
        f"• `/list` - View your channels\n"
        f"• `/help` - Show this message"
    )
