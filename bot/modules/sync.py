import asyncio
from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from bot.helpers.channel_manager import ChannelManager
from bot.helpers.bot_manager import BotManager
from config import Config
from bot.utils.logger import LOGGER

@Clients.bot.on_message(filters.command("sync") & filters.user(Config.OWNER_ID))
async def sync_all_channels(client, message):
    """Sync all channels with current bot configuration (Owner only)"""
    if Config.OWNER_ID == 0:
        await message.reply_text("❌ This command is disabled (OWNER_ID not set)")
        return
    
    status = await message.reply_text("🔄 **Global Sync Started...**")
    
    processed = 0
    errors = 0
    rejoined = 0
    
    try:
        channels = await Database.get_all_channels()
        total = len(channels)
        
        if total == 0:
            await status.edit("📭 No channels to sync.")
            return
        
        LOGGER.info(f"Starting sync for {total} channels")
        
        for idx, ch in enumerate(channels, 1):
            
            # --- [TRAFFIC LIGHT] PAUSE LOGIC ---
            while len(ChannelManager.ACTIVE_SETUPS) > 0:
                LOGGER.info(f"[SYNC-MAIN] ⏸️ Paused due to active setup in {ChannelManager.ACTIVE_SETUPS}...")
                try: await status.edit(f"⏸️ **Paused...**\nPriority Setup Running.\nWill resume shortly.")
                except: pass
                await asyncio.sleep(5)
            # -----------------------------------

            chat_id = ch["channel_id"]
            current = set(ch.get("installed_bots", []))
            wanted = set(Config.BOTS_TO_ADD)
            
            to_add = list(wanted - current)
            to_remove = list(current - wanted)
            
            # Skip if no changes needed
            if not to_add and not to_remove:
                continue
            
            try:
                # Check if helper is in channel
                is_member = await ChannelManager.check_helper_membership(chat_id)
                
                if not is_member:
                    # Need to rejoin
                    LOGGER.info(f"Rejoining channel {chat_id} for sync")
                    await ChannelManager.add_helper_to_channel(chat_id)
                    rejoined += 1
                    
                    # FIX: Increased wait to 10s after rejoining
                    LOGGER.info("⏳ Waiting 10s after rejoin...")
                    await asyncio.sleep(10)
                
                # Sync bots
                added_success, _ = await BotManager.process_bots(chat_id, "add", to_add)
                removed_success, _ = await BotManager.process_bots(chat_id, "remove", to_remove)
                
                # Update database
                new_state = list((current - set(removed_success)) | set(added_success))
                await Database.update_channel_bots(chat_id, new_state)
                
                processed += 1
                LOGGER.info(f"✅ Synced channel {chat_id}")
                
                await asyncio.sleep(Config.SYNC_CHANNEL_DELAY)
            
            except Exception as e:
                errors += 1
                LOGGER.error(f"Sync error for {chat_id}: {e}")
                
                # Notify channel owner
                try:
                    await Clients.bot.send_message(
                        ch["owner_id"],
                        f"⚠️ **LinkerX Sync Failed**\n\n"
                        f"🆔 Channel: `{chat_id}`\n"
                        f"❌ Error: {str(e)[:100]}\n\n"
                        f"Please run `/setup` inside the channel to fix."
                    )
                except Exception as notify_err:
                    LOGGER.error(f"Failed to notify owner: {notify_err}")
            
            # FIX: Update on 1st channel, then every 5th channel
            if idx == 1 or idx % 5 == 0:
                try:
                    await status.edit(
                        f"🔄 **Syncing...**\n\n"
                        f"Progress: {idx}/{total}\n"
                        f"✅ Updated: {processed}\n"
                        f"🔄 Rejoined: {rejoined}\n"
                        f"❌ Errors: {errors}"
                    )
                except Exception as e:
                    LOGGER.warning(f"Status update failed: {e}")
        
        # Final message
        await status.edit(
            f"✅ **Sync Finished!**\n\n"
            f"📊 Total Channels: {total}\n"
            f"📝 Updated: {processed}\n"
            f"🔄 Rejoined: {rejoined}\n"
            f"⚠️ Errors: {errors}"
        )
        LOGGER.info(f"Sync completed: {processed} updated, {errors} errors")
    
    except Exception as e:
        LOGGER.error(f"Global sync error: {e}")
        await status.edit(f"❌ **Sync Failed:** {str(e)}")
