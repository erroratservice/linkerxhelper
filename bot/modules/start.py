from pyrogram import filters
from bot.client import Clients
from config import Config

@Clients.bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_handler(client, message):
    """Welcome message with instructions"""
    bot_username = await Clients.get_bot_username()
    bot_mention = f"@{bot_username}" if bot_username else "this bot"
    
    await message.reply_text(
        f"👋 **Welcome to LinkerX Setup Service**\n\n"
        f"I can automatically configure your channel with required bots.\n\n"
        f"**🚀 Setup Steps:**\n"
        f"1️⃣ Add {bot_mention} to your channel\n"
        f"2️⃣ Promote it to admin with **Add New Admins** permission\n"
        f"3️⃣ Go to your channel and run `/setup`\n\n"
        f"**📋 Available Commands:**\n"
        f"• `/list` - View your channels\n"
        f"• `/help` - Show this message\n\n"
        f"__Note: The setup command only works directly inside channels or groups.__"
    )
