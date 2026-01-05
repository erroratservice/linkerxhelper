import asyncio
from pyrogram import filters, enums
from pyrogram.errors import (
    UserNotParticipant, 
    ChatAdminRequired, 
    PeerIdInvalid, 
    UserIsBlocked, 
    InputUserDeactivated,
    ChatWriteForbidden
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
        # Step 1: Check helper membership
        LOGGER.info(f"[STEP 1] Checking helper membership in {chat_id}")
        is_member = await ChannelManager.check_helper_membership(chat_id)
        
        if not is_member:
            await message.edit("➕ **Preparing helper account...**")
            LOGGER.info(f"[STEP 2] Adding helper to channel {chat_id}")
            
            try:
                # Pass 'message' for FloodWait notifications
                await ChannelManager.add_helper_to_channel(chat_id, message)
                LOGGER.info(f"[STEP 2] ✅ Helper successfully added/promoted")
            except Exception as e:
                LOGGER.error(f"[STEP 2] ❌ FAILED to add helper: {type(e).__name__} - {e}")
                raise
            
            LOGGER.info("[STEP 2] ⏳ Waiting 15s for permissions to propagate...")
            await asyncio.sleep(15)
        else:
            LOGGER.info(f"[STEP 2] Helper already in channel, skipping add")
        
        # Step 3: Verify helper rights
        LOGGER.info(f"[STEP 3] Verifying helper permissions")
        try:
            helper_member = await Clients.user_app.get_chat_member(chat_id, "me")
            can_promote = getattr(helper_member.privileges, "can_promote_members", False) if helper_member.privileges else False
            
            if not can_promote:
                raise RuntimeError("Helper account in channel but lacks 'Add New Admins' permission.")
        except Exception as e:
            LOGGER.error(f"[STEP 3] ❌ Failed to verify helper: {e}")
            raise
        
        # Step 4: Add bots
        await message.edit("🤖 **Adding bots...**")
        LOGGER.info(f"[STEP 4] Starting bot installation")
        
        try:
            successful, failed = await BotManager.process_bots(
                chat_id, "add", Config.BOTS_TO_ADD, message
            )
            LOGGER.info(f"[STEP 4] ✅ Bots added - Success: {len(successful)}, Failed: {len(failed)}")
            if failed:
                LOGGER.warning(f"[STEP 4] Failed bots: {failed}")
        except Exception as e:
            LOGGER.error(f"[STEP 4] ❌ Bot installation failed: {e}")
            raise
        
        # Step 5: Save DB
        LOGGER.info(f"[STEP 5] Saving setup")
        try:
            await Database.save_setup(chat_id, owner_id, successful)
        except Exception as e:
            LOGGER.error(f"[STEP 5] ❌ Database save failed: {e}")
            raise
        
        # Step 6: Completion
        text = (
            f"✅ **Setup complete!**\n\n"
            f"📢 Channel: `{chat_id}`\n"
            f"👑 Owner Linked: `{owner_id}`\n"
            f"🤖 Added: {len(successful)}/{len(Config.BOTS_TO_ADD)}\n"
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
        
        await message.edit(text)
        LOGGER.info(f"[STEP 6] ✅ Setup completed successfully")
        LOGGER.info(f"=" * 60)
    
    except Exception as e:
        LOGGER.error(f"SETUP FAILED: {e}")
        raise

@Clients.bot.on_message(filters.command("setup") & (filters.group | filters.channel))
async def setup_handler(client, message):
    """Setup command handler"""
    target_chat = message.chat.id
    status = None
    
    # 0. GROUP CHECK (New Restriction)
    if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        await message.reply_text(
            "⚠️ **Groups Not Supported**\n\n"
            "I am designed to setup **Channels** only.\n"
            "Please add me to a Channel and run `/setup` there."
        )
        return
    
    # 1. QUEUE CHECK
    try:
        existing_pos = queue_manager.get_position(target_chat)
        if existing_pos:
            await message.reply_text(
                f"⚠️ **Request Already Queued**\n\n"
                f"This channel is already in the queue at position **#{existing_pos}**.\n"
                f"Please wait for the current task to finish."
            )
            return
    except (ChatAdminRequired, ChatWriteForbidden):
        LOGGER.error(f"[SETUP] ❌ Bot lacks Admin/Write rights in {target_chat}")
        return
    except Exception as e:
        LOGGER.error(f"[SETUP] Queue check failed: {e}")
        return

    # 2. OWNER ID & INITIAL REPLY
    owner_id = None
    try:
        if message.from_user:
            owner_id = message.from_user.id
            status = await message.reply_text("🔍 **Identifying owner...**")
        else:
            status = await message.reply_text("🕵️ **Anonymous Admin detected...**\n🔍 Fetching channel owner...")
            try:
                found_owner = False
                async for member in Clients.bot.get_chat_members(target_chat, filter=ChatMembersFilter.ADMINISTRATORS):
                    if member.status == ChatMemberStatus.OWNER:
                        owner_id = member.user.id
                        found_owner = True
                        break
                
                if not found_owner or not owner_id:
                    await status.edit("❌ **Setup Failed**\n\nCould not identify owner.")
                    return
                LOGGER.info(f"[SETUP] Anonymous admin resolved to Owner ID: {owner_id}")
            except Exception as e:
                LOGGER.error(f"[SETUP] Failed to fetch owner: {e}")
                await status.edit("❌ **Error identifying owner.**")
                return
    except (ChatAdminRequired, ChatWriteForbidden):
        LOGGER.error(f"[SETUP] ❌ CRASH PREVENTED: Bot is not Admin in {target_chat}, cannot reply.")
        return
    except Exception as e:
        LOGGER.error(f"[SETUP] Initial reply failed: {e}")
        return

    # 3. DM VERIFICATION
    try:
        await Clients.bot.send_message(
            chat_id=owner_id,
            text=(
                f"✅ **LinkerX Setup Verification**\n\n"
                f"Setup is initializing for: **{message.chat.title}**\n"
                f"🆔 `{target_chat}`"
            )
        )
    except (PeerIdInvalid, UserIsBlocked, InputUserDeactivated):
        bot_username = await Clients.get_bot_username()
        await status.edit(
            f"⚠️ **Action Required**\n\n"
            f"I cannot message the Owner (ID: `{owner_id}`).\n"
            f"Please start the bot first: https://t.me/{bot_username}?start=setup\n"
            f"Then try again."
        )
        return
    except Exception as e:
        await status.edit(f"❌ **Verification Error:**\n`{str(e)}`")
        return

    await status.edit("🔍 **Checking bot permissions...**")
    
    try:
        # 4. PERMISSION & ADMIN LIMIT CHECK
        member = await Clients.bot.get_chat_member(target_chat, "me")
        
        # Check privileges object existence
        privs = member.privileges if member.privileges else None
        
        is_admin = member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        has_promote = getattr(privs, "can_promote_members", False) if privs else False
        has_invite = getattr(privs, "can_invite_users", False) if privs else False
        
        # Check for ALL 3 required permissions
        if not is_admin or not has_promote or not has_invite:
            bot_username = await Clients.get_bot_username()
            
            missing = []
            if not is_admin: missing.append("Admin Status")
            if not has_promote: missing.append("Add New Admins")
            if not has_invite: missing.append("Invite Users via Link")
            
            await status.edit(
                f"⚠️ **Missing Permissions!**\n\n"
                f"@{bot_username} requires the following rights:\n"
                f"❌ " + "\n❌ ".join(missing)
            )
            return
        
        # 5. FETCH ADMINS (Limit Check + Completion Check)
        await status.edit("🔍 **Checking admin slots...**")
        
        current_admin_usernames = set()
        current_count = 0
        
        async for member in Clients.bot.get_chat_members(target_chat, filter=ChatMembersFilter.ADMINISTRATORS):
            current_count += 1
            if member.user and member.user.username:
                current_admin_usernames.add(member.user.username.lower())
        
        # Calculate missing bots
        missing_bots = []
        for bot in Config.BOTS_TO_ADD:
            clean_name = bot.lstrip("@").lower()
            if clean_name not in current_admin_usernames:
                missing_bots.append(bot)
        
        # CHECK 1: Is everything already done?
        if not missing_bots:
            await status.edit(
                f"✅ **All bots already installed!**\n\n"
                f"I checked the admin list and all {len(Config.BOTS_TO_ADD)} bots are present.\n"
                f"No further action needed."
            )
            # We can update DB here to be safe
            await Database.save_setup(target_chat, owner_id, Config.BOTS_TO_ADD)
            return

        # CHECK 2: Do we have enough slots?
        slots_needed = len(missing_bots)
        # +1 buffer for Helper if it's not already an admin
        helper_buffer = 1 
        total_projected = current_count + slots_needed + helper_buffer
        
        if total_projected > 50:
            excess = total_projected - 50
            await status.edit(
                f"❌ **Admin Limit Exceeded**\n\n"
                f"Telegram limit: **50** admins.\n"
                f"📊 Current: `{current_count}`\n"
                f"🤖 Missing Bots: `{slots_needed}`\n"
                f"⚠️ Projected: `{total_projected}`\n\n"
                f"**Please remove {excess} admin(s) and try again.**"
            )
            return
            
    except UserNotParticipant:
        await status.edit(f"⚠️ **Bot not in channel!**")
        return
    except Exception as e:
        LOGGER.error(f"Permission check error: {e}")
        await status.edit(f"❌ **Error:** `{e}`")
        return
    
    # 6. ADD TO QUEUE
    LOGGER.info(f"[QUEUE] Adding {target_chat} to processing queue")
    try:
        await queue_manager.add_to_queue(status, target_chat, owner_id, setup_logic)
    except Exception as e:
        await status.edit(f"❌ **Queue Error:** {str(e)}")
