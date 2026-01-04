from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from config import Config

@Clients.bot.on_message(filters.command("list") & filters.private)
async def list_handler(client, message):
    """List user's channels"""
    try:
        cursor = Database.channels.find({"owner_id": message.from_user.id})
        docs = await cursor.to_list(length=100)
        
        if not docs:
            await message.reply_text("📭 No channels registered.\nUse `/setup <channel_id>`")
            return
        
        active = [d for d in docs if d.get("user_is_member")]
        inactive = [d for d in docs if not d.get("user_is_member")]
        
        text = "📂 **Your LinkerX channels**\n\n"
        if active:
            text += "**🟢 Active:**\n"
            for i, ch in enumerate(active, 1):
                bots = ch.get("installed_bots", [])
                text += f"{i}. `{ch['channel_id']}` - {len(bots)}/{len(Config.BOTS_TO_ADD)} bots\n"
        
        if inactive:
            text += "\n**⚪ Inactive:**\n"
            for i, ch in enumerate(inactive, 1):
                bots = ch.get("installed_bots", [])
                text += f"{i}. `{ch['channel_id']}` - {len(bots)}/{len(Config.BOTS_TO_ADD)} bots\n"
        
        active_count = await Database.get_active_channel_count()
        text += f"\n📊 Total: {len(docs)} | Active: {active_count}/{Config.MAX_USER_CHANNELS}"
        await message.reply_text(text)
    
    except Exception as e:
        await message.reply_text(f"❌ Error: `{e}`")
