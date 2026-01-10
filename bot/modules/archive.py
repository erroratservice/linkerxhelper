import asyncio
from pyrogram import filters, enums
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import (
    ChatAdminRequired,
    UserNotParticipant,
    ChatWriteForbidden,
    FloodWait
)
from datetime import datetime
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
            f"Please leave the channel for the convenience and safety of your account."
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
            
        await message.edit(text)
        
        # 5. KICK HELPER (Bot Bans then Unbans Helper)
        LOGGER.info(f"[ARCHIVE] 👢 Bot kicking Helper from {chat_id}")
        try:
            helper_me = await Clients.user_app.get_me()
            helper_id = helper_me.id
            
            # Kick (Ban then Unban) to ensure they can be added back later if needed
            await Clients.bot.ban_chat_member(chat_id, helper_id)
            await asyncio.sleep(1)
            await Clients.bot.unban_chat_member(chat_id, helper_id)
            
            LOGGER.info(f"[ARCHIVE] ✅ Helper successfully kicked")
        except Exception as e:
            LOGGER.error(f"[ARCHIVE] ❌ Failed to kick helper: {e}")

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
    
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
         return await message.reply_text("⚠️ This command is for **Channels** only.")

    chat_id = message.chat.id

    # 0. CONFLICT CHECK
    is_main_setup = await Database.is_channel_in_main_db(chat_id)
    if is_main_setup:
        return await message.reply_text(
            "🛑 **Action Blocked**\n\n"
            "This channel is already configured with the LinkerX Services\n"
            "You cannot use Archive mode here as it conflicts with the existing setup."
            "Please create a new channel and add the bot there and run the /helparchive commnand there"
        )
    
    if queue_manager.get_position(chat_id):
        return await message.reply_text("⚠️ This channel is already in queue.")

    status = await message.reply_text("🔍 **Checking strict permissions...**")
    
    try:
        member = await client.get_chat_member(chat_id, "me")
        privs = member.privileges

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
            "can_manage_topics": "Manage Topics" 
        }

        missing = []
        if not privs:
             missing = list(required_privs.values())
        else:
            for attr, label in required_privs.items():
                if not getattr(privs, attr, False):
                    missing.append(label)

        # 3. Strict Check Failure -> Copy Guide Message
        if missing or member.status != ChatMemberStatus.ADMINISTRATOR:
            await status.delete()
            
            caption = (
                "🛑 **INSUFFICIENT PERMISSIONS FOR ARCHIVE**\n\n"
                "For `/helparchive`, the bot requires **EVERY** permission enabled.\n\n"
                "❌ **Missing:**\n" + "\n".join([f"- {m}" for m in missing]) +
                "\n\n👇 **Please enable ALL permissions as shown below:**"
            )
            
            try:
                link = Config.PERM_GUIDE_PIC
                src_chat_id = None
                src_msg_id = None
                
                if "/c/" in link: 
                    parts = link.split("/")
                    src_chat_id = int("-100" + parts[-2])
                    src_msg_id = int(parts[-1])
                elif "t.me/" in link: 
                    parts = link.split("/")
                    src_chat_id = parts[-2]
                    src_msg_id = int(parts[-1])
                
                if src_chat_id and src_msg_id:
                    await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=src_chat_id,
                        message_id=src_msg_id,
                        caption=caption
                    )
                else:
                    raise ValueError("Invalid link format")

            except Exception as e:
                 LOGGER.error(f"Failed to copy perm guide message: {e}")
                 await message.reply_text(caption + "\n\n*(Visual guide unavailable)*")
            return

        # 4. Identify Owner (Attempt only, use dummy if failed)
        owner_id = 0
        try:
            async for admin in client.get_chat_members(chat_id, filter=ChatMemberStatus.OWNER):
                owner_id = admin.user.id
                break
        except:
            LOGGER.warning(f"Could not identify owner for {chat_id}, using ID 0")

        await queue_manager.add_to_queue(status, chat_id, owner_id, archive_logic)

    except (ChatAdminRequired, ChatWriteForbidden):
        return
    except Exception as e:
        LOGGER.error(f"HelpArchive error: {e}")
        try: await status.edit(f"❌ Error: {e}")
        except: pass

# ==================================================================
# SYNC ARCHIVE COMMAND (UPDATED)
# ==================================================================

@Clients.bot.on_message(filters.command("syncarchive") & filters.user(Config.OWNER_ID))
async def sync_archive_handler(client, message):
    """
    Syncs channels in the ARCHIVE DB.
    - SILENT: Does not notify owner on failure.
    - NO OWNER CHECK: Ignores owner existence.
    - KICKS HELPER: Ensures helper is gone.
    """
    status = await message.reply_text("♻️ **Starting Archive Sync...**")
    
    channels = await Database.get_all_archive_channels()
    total = len(channels)
    processed = 0
    failed_channels = []

    LOGGER.info(f"[SYNC-ARCHIVE] Started for {total} channels")

    for channel_data in channels:
        chat_id = channel_data.get("channel_id")
        processed += 1
        
        if processed % 5 == 0:
            try: await status.edit(f"♻️ **Archive Syncing...**\nDo not restart bot.\nProgress: {processed}/{total}")
            except: pass

        try:
            # 1. Update "last_updated" in DB (Silent heartbeat)
            await Database.archive_channels.update_one(
                {"channel_id": chat_id},
                {"$set": {"last_updated": datetime.utcnow()}}
            )

            # 2. Check & Kick Helper (Silent Maintenance)
            try:
                helper_me = await Clients.user_app.get_me()
                member = await Clients.user_app.get_chat_member(chat_id, "me")
                
                # If Helper is present -> Kick (Ban+Unban)
                if member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                    LOGGER.info(f"[SYNC-ARCHIVE] Helper found in {chat_id}, kicking...")
                    await client.ban_chat_member(chat_id, helper_me.id)
                    await asyncio.sleep(1)
                    await client.unban_chat_member(chat_id, helper_me.id)
            except UserNotParticipant:
                pass # Helper is already gone, good.
            except Exception as e:
                # Log but DO NOT message owner
                LOGGER.warning(f"[SYNC-ARCHIVE] Helper check warning for {chat_id}: {e}")

        except Exception as e:
            # Log but DO NOT message owner
            LOGGER.error(f"[SYNC-ARCHIVE] Failed {chat_id}: {e}")
            failed_channels.append(chat_id)
        
        await asyncio.sleep(Config.SYNC_CHANNEL_DELAY)

    result_text = (
        f"✅ **Archive Sync Complete**\n\n"
        f"📚 Total Scanned: `{total}`\n"
    )
    if failed_channels:
        result_text += f"\n⚠️ Errors: {len(failed_channels)} (Check Logs)"

    await status.edit(result_text)

# ==================================================================
# STATS ARCHIVE COMMAND
# ==================================================================

@Clients.bot.on_message(filters.command("statsarchive") & filters.user(Config.OWNER_ID))
async def stats_archive_handler(client, message):
    """Shows stats only for Archive DB"""
    status = await message.reply_text("📊 Fetching archive stats...")
    stats = await Database.get_archive_stats()
    if not stats: return await status.edit("❌ Failed to fetch archive stats.")
    text = (
        f"📂 **Archive Database Stats**\n\n"
        f"📢 Total Archived Channels: `{stats['total_channels']}`\n"
        f"👑 Unique Owners: `{stats['unique_owners']}`\n"
        f"🤖 Total Bots Deployed: `{stats['total_bots']}`\n"
    )
    await status.edit(text)
