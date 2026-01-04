import asyncio
from pyrogram import filters
from pyrogram.errors import UserNotParticipant, ChatAdminRequired
from pyrogram.enums import ChatMemberStatus
from bot.client import Clients
from bot.helpers.queue import queue_manager
from bot.helpers.channel_manager import ChannelManager
from bot.helpers.bot_manager import BotManager
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

async def setup_logic(message, chat_id, owner_id):
    """Main setup logic - executed by queue worker"""
    LOGGER.info(f"=" * 60)
    LOGGER.info(f"SETUP STARTED for channel {chat_id}")
    LOGGER.info(f"=" * 60)
    
    try:
        # Step 1: Check if helper is already in channel
        LOGGER.info(f"[STEP 1] Checking helper membership in {chat_id}")
        is_member = await ChannelManager.check_helper_membership(chat_id)
        LOGGER.info(f"[STEP 1] Helper membership status: {is_member}")
        
        if not is_member:
            await message.edit("➕ **Preparing helper account...**")
            LOGGER.info(f"[STEP 2] Adding helper to channel {chat_id}")
            
            try:
                await ChannelManager.add_helper_to_channel(chat_id)
                LOGGER.info(f"[STEP 2] ✅ Helper successfully added/promoted")
            except Exception as e:
                LOGGER.error(f"[STEP 2] ❌ FAILED to add helper: {type(e).__name__} - {e}")
                raise
            
            await asyncio.sleep(2)
        else:
            LOGGER.info(f"[STEP 2] Helper already in channel, skipping add")
        
        # Step 3: Verify helper has admin rights with promote permission
        LOGGER.info(f"[STEP 3] Verifying helper permissions in {chat_id}")
        try:
            helper_member = await Clients.user_app.get_chat_member(chat_id, "me")
            LOGGER.info(f"[STEP 3] Helper status: {helper_member.status}")
            LOGGER.info(f"[STEP 3] Helper privileges: {helper_member.privileges}")
            
            can_promote = getattr(helper_member.privileges, "can_promote_members", False) if helper_member.privileges else False
            LOGGER.info(f"[STEP 3] Helper can_promote_members: {can_promote}")
            
            if not can_promote:
                LOGGER.error(f"[STEP 3] ❌ Helper lacks promote permission!")
                raise RuntimeError(
                    "Helper account is in channel but lacks 'Add New Admins' permission.\n"
                    "Please manually promote @HelpingYouSetup with 'Add New Admins' permission."
                )
        except Exception as e:
            LOGGER.error(f"[STEP 3] ❌ Failed to verify helper permissions: {e}")
            raise
        
        # Step 4: Add bots
        await message.edit("🤖 **Adding bots...**")
        LOGGER.info(f"[STEP 4] Starting bot installation for {chat_id}")
        LOGGER.info(f"[STEP 4] Bots to install: {len(Config.BOTS_TO_ADD)}")
        
        try:
            successful, failed = await BotManager.process_bots(
                chat_id, "add", Config.BOTS_TO_ADD, message
            )
            LOGGER.info(f"[STEP 4] ✅ Bots added - Success: {len(successful)}, Failed: {len(failed)}")
            if failed:
                LOGGER.warning(f"[STEP 4] Failed bots: {failed}")
        except Exception as e:
            LOGGER.error(f"[STEP 4] ❌ Bot installation failed: {type(e).__name__} - {e}")
            raise
        
        # Step 5: Save to database
        LOGGER.info(f"[STEP 5] Saving setup to database")
        try:
            await Database.save_setup(chat_id, owner_id, successful)
            LOGGER.info(f"[STEP 5] ✅ Database updated")
        except Exception as e:
            LOGGER.error(f"[STEP 5] ❌ Database save failed: {e}")
            raise
        
        # Step 6: Send completion message
        active_count = await Database.get_active_channel_count()
        text = (
            f"✅ **Setup complete!**\n\n"
            f"📢 Channel: `{chat_id}`\n"
            f"🤖 Added: {len(successful)}/{len(Config.BOTS_TO_ADD)}\n"
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
        
        await message.edit(text)
        LOGGER.info(f"[STEP 6] ✅ Setup completed successfully for {chat_id}")
        LOGGER.info(f"=" * 60)
    
    except Exception as e:
        LOGGER.error(f"=" * 60)
        LOGGER.error(f"SETUP FAILED for channel {chat_id}")
        LOGGER.error(f"Error type: {type(e).__name__}")
        LOGGER.error(f"Error message: {str(e)}")
        LOGGER.error(f"=" * 60)
        raise

@Clients.bot.on_message(filters.command("setup") & (filters.group | filters.channel))
async def setup_handler(client, message):
    """Setup command handler - Works in Groups/Channels only"""
    
    # Identify the user (Owner)
    if not message.from_user:
        await message.reply_text("❌ Please run this command as a user (disable anonymous admin) so I can link this channel to your account.")
        return
        
    owner_id = message.from_user.id
    target_chat = message.chat.id
    
    LOGGER.info(f"[SETUP CMD] Received in chat {target_chat} from user {owner_id}")
    
    status = await message.reply_text("🔍 **Checking bot permissions...**")
    
    # Check bot permissions with detailed logging
    try:
        LOGGER.info(f"[PERMISSION CHECK] Checking bot permissions in {target_chat}")
        member = await Clients.bot.get_chat_member(target_chat, "me")
        LOGGER.info(f"[PERMISSION CHECK] Bot status: {member.status}")
        LOGGER.info(f"[PERMISSION CHECK] Bot privileges: {member.privileges}")
        
        # Check if Admin or Owner
        is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        # Check for Promote permission
        has_promote_flag = bool(getattr(member.privileges, "can_promote_members", False)) if member.privileges else False
        
        LOGGER.info(f"[PERMISSION CHECK] is_admin: {is_admin}, can_promote_members: {has_promote_flag}")
        
        if not is_admin:
            LOGGER.error(f"[PERMISSION CHECK] ❌ Bot is not admin in {target_chat}")
            bot_username = await Clients.get_bot_username()
            bot_mention = f"@{bot_username}" if bot_username else "the bot"
            await status.edit(
                f"⚠️ **Missing permissions!**\n\n"
                f"{bot_mention} must be an admin in this channel with **Add New Admins** permission enabled.\n\n"
                f"Please update permissions and try again."
            )
            return
        
        if not has_promote_flag:
            LOGGER.error(f"[PERMISSION CHECK] ❌ Bot lacks can_promote_members")
            bot_username = await Clients.get_bot_username()
            bot_mention = f"@{bot_username}" if bot_username else "the bot"
            await status.edit(
                f"⚠️ **Missing 'Add New Admins' permission!**\n\n"
                f"{bot_mention} is admin but lacks **Add New Admins** permission.\n\n"
                f"Please enable this permission and try again."
            )
            return
        
        LOGGER.info(f"[PERMISSION CHECK] ✅ Bot permissions verified")
    
    except UserNotParticipant:
        # This shouldn't theoretically happen if the bot is replying to a command in the channel, 
        # but safe to handle.
        LOGGER.error(f"[PERMISSION CHECK] ❌ Bot not in channel {target_chat}")
        await status.edit(
            f"⚠️ **Bot not in channel!**\n\n"
            f"Please ensure the bot is added to the channel properly."
        )
        return
    
    except Exception as e:
        LOGGER.error(f"[PERMISSION CHECK] ❌ Error checking permissions: {type(e).__name__} - {e}")
        await status.edit(f"❌ **Error checking permissions:**\n`{e}`")
        return
    
    # Add to queue for processing
    LOGGER.info(f"[QUEUE] Adding {target_chat} to processing queue")
    try:
        await queue_manager.add_to_queue(status, target_chat, owner_id, setup_logic)
        LOGGER.info(f"[QUEUE] ✅ Added to queue successfully")
    except Exception as e:
        LOGGER.error(f"[QUEUE] ❌ Failed to add to queue: {e}")
        await status.edit(f"❌ **Error:** {str(e)}")
