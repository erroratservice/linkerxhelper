from pyrogram import filters, enums
from bot.client import Clients
from config import Config

@Clients.bot.on_message(filters.command("start"))
async def start_handler(client, message):
    """
    Handle /start command.
    - Channels: Instruct to use /setup
    - Groups: Reject (Not supported)
    - DMs: Show welcome message
    """
    
    # 1. CHANNEL HANDLING
    if message.chat.type == enums.ChatType.CHANNEL:
        await message.reply_text(
            "⚙️ **Channel Setup**\n\n"
            "To install bots in this channel, please run the setup command:\n\n"
            "👉 **/setup**"
        )
        return

    # 2. GROUP HANDLING (Reject)
    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        await message.reply_text(
            "⚠️ **Groups Not Supported**\n\n"
            "I am designed to manage **Channels** only.\n"
            "Please add me to a Channel as an Admin and run `/setup` there."
        )
        return

    # 3. DM HANDLING (Welcome)
    user = message.from_user
    name = user.first_name if user else "User"
    
    text = (
        f"👋 **Hello, {name}!**\n\n"
        f"I am the **LinkerX Helper**.\n"
        f"I automatically install and promote bots in your channels.\n\n"
        f"**🚀 How to use:**\n"
        f"1️⃣ Add me to your **Channel** as an Admin.\n"
        f"   *(Permissions: Add Admins + Invite Users)*\n"
        f"2️⃣ Type `/setup` in the channel.\n"
        f"3️⃣ I will do the rest!\n\n"
        f"⚡ _Maintained by LiquidX_"
    )
    
    await message.reply_text(text)
