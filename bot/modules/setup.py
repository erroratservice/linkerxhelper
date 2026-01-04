import asyncio
from pyrogram import filters, enums
from pyrogram.errors import (
    UserNotParticipant, 
    ChatAdminRequired, 
    PeerIdInvalid, 
    UserIsBlocked, 
    InputUserDeactivated
)
from pyrogram.enums import ChatMemberStatus, ChatMembersFilter
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
            
            # INCREASED DELAY: Give Telegram 5 seconds to sync admin rights
            LOGGER.info("[STEP 2] ⏳ Waiting 5s for permissions to propagate...")
            await asyncio.sleep(5)
        else:
            LOGGER.info(f"[STEP 2] Helper already in channel, skipping add")
        
        # Step 3: Verify helper has admin rights with promote permission
        LOGGER.info(f"[STEP 3] Verifying helper permissions in {chat_id}")
        try:
            helper_member = await Clients.user_app.get_chat_member(chat_id, "me")
            
            can_promote = getattr(helper_member.privileges, "can_promote_members", False) if helper_member.privileges else False
            
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
        text = (
            f"✅ **Setup complete!**\n\n"
            f"📢 Channel: `{chat_id}`\n"
            f"👑 Owner Linked: `{owner_id}`\n"
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
    target_chat = message.chat.id
    status = None
    
    # -------------------------------------------------------
    # 1. OWNER ID DETECTION
    # -------------------------------------------------------
    owner_id = None
    if message.from_user:
        # Normal user or non-anonymous admin
        owner_id = message.from_user.id
        status = await message.reply_text("🔍 **Identifying owner...**")
    else:
        # Anonymous Admin case
        status = await message.reply_text("🕵️ **Anonymous Admin detected...**\n🔍 Fetching channel owner to link account...")
        try:
            # Iterate through admins to find the Owner
            found_owner = False
            async for member in Clients.bot.get_chat_members(target_chat, filter=ChatMembersFilter.ADMINISTRATORS):
                if member.status == ChatMemberStatus.OWNER:
                    owner_id = member.user.id
                    found_owner = True
                    break
            
            if not found_owner or not owner_id:
                await status.edit("❌ **Setup Failed**\n\nCould not identify the channel owner. Please disable Anonymous Admin and try again.")
                return
            
            LOGGER.info(f"[SETUP] Anonymous admin resolved to Owner ID: {owner_id}")
        except Exception as e:
            LOGGER.error(f"[SETUP] Failed to fetch owner: {e}")
            await status.edit("❌ **Error identifying owner.**\nPlease disable Anonymous Admin and try again.")
            return

    LOGGER.info(f"[SETUP CMD] Received in chat {target_chat} linked to owner {owner_id}")

    # -------------------------------------------------------
    # 2. VERIFY OWNER REACHABILITY (DM CHECK)
    # -------------------------------------------------------
    try:
        # Try to send a verification message to the owner
        await Clients.bot.send_message(
            chat_id=owner_id,
            text=(
                f"✅ **LinkerX Setup Verification**\n\n"
                f"Setup is initializing for channel: **{message.chat.title}**\n"
                f"🆔 `{target_chat}`\n\n"
                f"__You are receiving this because you are linked as the owner of this setup.__"
            )
        )
    except (PeerIdInvalid, UserIsBlocked, InputUserDeactivated):
        # User has not started the bot or blocked it
        bot_username = await Clients.get_bot_username()
        await status.edit(
            f"⚠️ **Action Required**\n\n"
            f"I cannot message the Owner (ID: `{owner_id}`).\n"
            f"To ensure you receive error alerts and status updates, you must start the bot first.\n\n"
            f"👇 **Please do this:**\n"
            f"1. [Click Here to Start Bot](https://t.me/{bot_username}?start=setup)\n"
            f"2. Come back here and run `/setup` again."
        )
        return
    except Exception as e:
        LOGGER.error(f"[DM CHECK] Failed: {e}")
        await status.edit(f"❌ **Verification Error:**\nUnable to verify connection with owner: `{str(e)}`")
        return

    await status.edit("🔍 **Checking bot permissions...**")
    
    # -------------------------------------------------------
    # 3. PERMISSION CHECKS
    # -------------------------------------------------------
    try:
        LOGGER.info(f"[PERMISSION CHECK] Checking bot permissions in {target_chat}")
        member = await Clients.bot.get_chat_member(target_chat, "me")
        
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
