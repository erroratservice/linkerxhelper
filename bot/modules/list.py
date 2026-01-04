from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from config import Config

@Clients.bot.on_message(filters.command("list") & filters.private)
async def list_handler(client, message):
    """Show user's registered channels"""
    try:
        docs = await Database.get_user_channels(message.from_user.id)
        
        if not docs:
            await message.reply_text(
                "📭 **No Channels Found**\n\n"
                "You haven't set up LinkerX in any channels yet.\n\n"
                "Use `/setup <channel_id>` to get started!"
            )
            return
        
        # Separate active and inactive channels
        active = [d for d in docs if d.get("user_is_member")]
        inactive = [d for d in docs if not d.get("user_is_member")]
        
        text = "📂 **Your LinkerX Channels**\n\n"
        
        if active:
            text += "**🟢 Active (Helper Present):**\n"
            for i, ch in enumerate(active, 1):
                bots = ch.get("installed_bots", [])
                jd = ch.get("user_joined_at")
                text += f"{i}. `{ch['channel_id']}`\n"
                text += f"   🤖 Bots: {len(bots)}/{len(Config.BOTS_TO_ADD)}\n"
                if jd:
                    text += f"   📅 Joined: {jd.strftime('%Y-%m-%d')}\n"
                text += "\n"
        
        if inactive:
            text += "**⚪ Inactive (Helper Removed):**\n"
            for i, ch in enumerate(inactive, 1):
                bots = ch.get("installed_bots", [])
                text += f"{i}. `{ch['channel_id']}`\n"
                text += f"   🤖 Bots: {len(bots)}/{len(Config.BOTS_TO_ADD)}\n\n"
        
        # Summary
        active_count = await Database.get_active_channel_count()
        text += f"📊 **Summary:**\n"
        text += f"Total: {len(docs)} | Active: {active_count}/{Config.MAX_USER_CHANNELS}"
        
        await message.reply_text(text)
    
    except Exception as e:
        from bot.utils.logger import LOGGER
        LOGGER.error(f"/list error: {e}")
        await message.reply_text(f"❌ **Error:** `{e}`")
