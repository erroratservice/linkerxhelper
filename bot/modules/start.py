from pyrogram import filters, enums
from bot.client import Clients
from config import Config

@Clients.bot.on_message(filters.command("start"))
async def start_handler(client, message):
    """
    Handle /start command.
    - In Channels: Instruct to use /setup
    - In DMs/Groups: Show welcome message
    """
    
    # 1. SPECIAL HANDLING FOR CHANNELS
    # Channels don't always have a 'from_user', so we handle them first
    if message.chat.type == enums.ChatType.CHANNEL:
        await message.reply_text(
            "⚙️ **Channel Setup**\n\n"
            "To install bots in this channel, please run the setup command:\n\n"
            "👉 **/setup**"
        )
        return

    # 2. Handle DMs and Groups (Standard Welcome)
    # It is safe to access message.from_user here
    user = message.from_user
    name = user.first_name if user else "User"
    
    text = (
        f"👋 **Hello, {name}!**\n\n"
        f"I am the **LinkerX Helper**.\n"
        f"I automatically install and promote bots in your channels.\n\n"
        f"**🚀 How to use:**\n"
        f"1️⃣ Add me to your Channel as an **Admin**.\n"
        f"   *(Permissions: Add Admins + Invite Users)*\n"
        f"2️⃣ Type `/setup` in the channel.\n"
        f"3️⃣ I will do the rest!\n\n"
        f"⚡ _Powered by LinkerX_"
    )
    
    await message.reply_text(text)
    
