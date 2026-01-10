import asyncio
from pyrogram import filters, enums
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import (
    ChatAdminRequired,
    UserNotParticipant,
    ChatWriteForbidden,
    FloodWait
)
from bot.client import Clients
from bot.helpers.queue import queue_manager
from bot.helpers.channel_manager import ChannelManager
from bot.helpers.bot_manager import BotManager
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

# ==================================================================
# LOGIC WORKER
# ==================================================================

async def archive_logic(message, chat_id, owner_id):
    """
    Main logic for /helparchive.
    1. Adds helper
    2. Adds bots
    3. Saves to ARCHIVE DB
    4. BOT KICKS HELPER (Important: Helper does not leave, Bot kicks it)
    """
    LOGGER.info(f"=[ARCHIVE] SETUP STARTED for channel {chat_id}=")
    
    try:
        # 1. Add Helper with FULL permissions 
        await message.edit("➕ **Preparing helper account with FULL access...**")
        LOGGER.info(f"[ARCHIVE] Adding helper to {chat_id}")
        
        # Pass message for FloodWait handling
        await ChannelManager.add_helper_to_channel(chat_id, message)
        
        LOGGER.info("[ARCHIVE] ⏳ Waiting 15s for permissions to propagate...")
        await asyncio.sleep(15)

        # 2. Add bots using the Helper account
        await message.edit("🤖 **Adding archive bots...**")
        LOGGER.info(f"[ARCHIVE] Starting bot installation via Userbot")
        
        successful, failed = await BotManager.process_bots(
            chat_id, "add", Config.BOTS_TO_ADD, message
        )
        
        # 3. Save to SEPARATE Archive Database
        LOGGER.info(f"[ARCHIVE] Saving to Archive DB")
        await Database.save_archive_setup(chat_id, owner_id, successful)
        
        # 4. Final Message
        text = (
            f"✅ **Archive Setup Complete!**\n\n"
            f"📢 Channel: `{chat_id}`\n"
            f"🤖 Bots Added: {len(successful)}/{len(Config.BOTS_TO_ADD)}\n\n"
            f"🙏 **Thank you for your help!**\n"
            f"The helper account will now be removed from the channel."
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
            
        await message.edit(text)
        
        # 5. KICK HELPER (Bot Bans then Unbans Helper)
        LOGGER.info(f"[ARCHIVE] 👢 Bot kicking Helper from {chat_id}")
        try:
            helper_me = await Clients.user_app.get_me()
            helper_id = helper_me.id
            
            # Kick (Ban then Unban)
            await Clients.bot.ban_chat_member(chat_id, helper_id)
            await asyncio.sleep(1)
            await Clients.bot.unban_chat_member(chat_id, helper_id)
            
            LOGGER.info(f"[ARCHIVE] ✅ Helper successfully kicked")
        except Exception as e:
            LOGGER.error(f"[ARCHIVE] ❌ Failed to kick helper: {e}")
            # We don't raise here, as the main job is done

        LOGGER.info(f"=[ARCHIVE] Finished for {chat_id}=")

    except Exception as e:
        LOGGER.error(f"[ARCHIVE] FAILED: {e}")
        try: await message.edit(f"❌ **Archive Error:** `{e}`")
        except: pass
        raise

# ==================================================================
# HELP ARCHIVE COMMAND
# ==================================================================

@Clients.bot.on_message(filters.command("helparchive") & (filters.group | filters.channel))
async def help_archive_handler(client, message):
    """Handler for strict archive setup"""
    
    # Restrict to Channels only
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
         return await message.reply_text("⚠️ This command is for **Channels** only.")

    chat_id = message.chat.id
    
    # 1. Queue Check
    if queue_manager.get_position(chat_id):
        return await message.reply_text("⚠️ This channel is already in queue.")

    status = await message.reply_text("🔍 **Checking strict permissions...**")
    
    try:
        # 2. Fetch Bot Member & Privileges
        member = await client.get_chat_member(chat_id, "me")
        privs = member.privileges

        # STRICT Permission List (All major admin rights)
        # As per the request for "every single permission" 
        # 
        required_privs = {
            "can_manage_chat": "Manage Channel",
            "can_change_info": "Change Channel Info",
            "can_post_messages": "Post Messages",
            "can_edit_messages": "Edit Messages",
            "can_delete_messages": "Delete Messages",
            "can_invite_users": "Invite Users via Link",
            "can_restrict_members": "Ban Users",
            "can_promote_members": "Add New Admins",
            "can_manage_video_chats": "Manage Video Chats",
            "can_manage_topics": "Manage Topics" # Some older channels might not have this, but requested "all"
        }

        missing = []
        if not privs:
             missing = list(required_privs.values())
        else:
            for attr, label in required_privs.items():
                # Check if attribute exists (for safety vs old api versions) and is True
                if not getattr(privs, attr, False):
                    missing.append(label)

        # 3. Strict Check Failure -> Send Photo Guide
        if missing or member.status != ChatMemberStatus.ADMINISTRATOR:
            await status.delete()
            
            caption = (
                "🛑 **INSUFFICIENT PERMISSIONS FOR ARCHIVE**\n\n"
                "For `/helparchive`, the bot requires **EVERY** permission enabled.\n\n"
                "❌ **Missing:**\n" + "\n".join([f"- {m}" for m in missing]) +
                "\n\n👇 **Please enable ALL permissions as shown below:**"
            )
            
            # Send visual guide
            try:
                await message.reply_photo(
                    photo=Config.PERM_GUIDE_PIC,
                    caption=caption
                )
            except Exception as e:
                 # Fallback if image fails
                 LOGGER.error(f"Failed to send perm guide image: {e}")
                 await message.reply_text(caption + "\n*(Image failed to load)*")
            return

        # 4. Identify Owner
        owner_id = None
        # Try to find owner from admin list
        async for admin in client.get_chat_members(chat_id, filter=ChatMemberStatus.OWNER):
            owner_id = admin.user.id
            break
            
        # If bot can't see owner (common in big channels), fallback to Anonymous
        if not owner_id:
             if message.from_user:
                 owner_id = message.from_user.id
             else:
                 # Fallback dummy ID if strictly anonymous and we can't find owner
                 # We need an ID for DB purposes
                 owner_id = 0 
                 LOGGER.warning(f"Could not identify owner for {chat_id}, using ID 0")

        # 5. Add to Queue
        await queue_manager.add_to_queue(status, chat_id, owner_id, archive_logic)

    except (ChatAdminRequired, ChatWriteForbidden):
        return # Can't reply anyway
    except Exception as e:
        LOGGER.error(f"HelpArchive error: {e}")
        try: await status.edit(f"❌ Error: {e}")
        except: pass

# ==================================================================
# SYNC ARCHIVE COMMAND
# ==================================================================

@Clients.bot.on_message(filters.command("syncarchive") & filters.user(Config.OWNER_ID))
async def sync_archive_handler(client, message):
    """Syncs only channels in the ARCHIVE DB"""
    status = await message.reply_text("♻️ **Starting Archive Sync...**")
    
    # Get channels ONLY from archive DB
    channels = await Database.get_all_archive_channels()
    total = len(channels)
    processed = 0
    failed_channels = []

    LOGGER.info(f"[SYNC-ARCHIVE] Started for {total} channels")

    for channel_data in channels:
        chat_id = channel_data.get("channel_id")
        processed += 1
        
        if processed % 5 == 0:
            try:
                await status.edit(f"♻️ **Archive Syncing...**\nDo not restart bot.\nProgress: {processed}/{total}")
            except: pass

        try:
            # Sync Logic for Archive: 
            # We mainly check if Helper is gone. If Helper is still there, we kick it.
            try:
                helper_me = await Clients.user_app.get_me()
                member = await Clients.user_app.get_chat_member(chat_id, "me")
                
                # If we are here, Helper is still in channel -> KICK IT
                if member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                    LOGGER.info(f"[SYNC-ARCHIVE] Helper found in {chat_id}, kicking...")
                    await client.ban_chat_member(chat_id, helper_me.id)
                    await asyncio.sleep(1)
                    await client.unban_chat_member(chat_id, helper_me.id)
            except UserNotParticipant:
                # Good, helper is not there
                pass
            except Exception as e:
                LOGGER.warning(f"[SYNC-ARCHIVE] Check failed for {chat_id}: {e}")

        except Exception as e:
            LOGGER.error(f"[SYNC-ARCHIVE] Failed {chat_id}: {e}")
            failed_channels.append(chat_id)
        
        await asyncio.sleep(Config.SYNC_CHANNEL_DELAY)

    result_text = (
        f"✅ **Archive Sync Complete**\n\n"
        f"📚 Total Scanned: `{total}`\n"
    )
    if failed_channels:
        result_text += f"\n⚠️ Errors: {len(failed_channels)}"

    await status.edit(result_text)

# ==================================================================
# STATS ARCHIVE COMMAND
# ==================================================================

@Clients.bot.on_message(filters.command("statsarchive") & filters.user(Config.OWNER_ID))
async def stats_archive_handler(client, message):
    """Shows stats only for Archive DB"""
    status = await message.reply_text("📊 Fetching archive stats...")
    
    stats = await Database.get_archive_stats()
    
    if not stats:
        return await status.edit("❌ Failed to fetch archive stats.")
        
    text = (
        f"📂 **Archive Database Stats**\n\n"
        f"📢 Total Archived Channels: `{stats['total_channels']}`\n"
        f"👑 Unique Owners: `{stats['unique_owners']}`\n"
        f"🤖 Total Bots Deployed: `{stats['total_bots']}`\n"
    )
    
    await status.edit(text)
