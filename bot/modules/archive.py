import asyncio
from pyrogram import filters, enums
from pyrogram.enums import ChatMemberStatus, ChatType, ChatMembersFilter
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
    LOGGER.info(f"=[ARCHIVE] SETUP STARTED for channel {chat_id}=")
    try:
        await message.edit("➕ **Preparing helper account with FULL access...**")
        LOGGER.info(f"[ARCHIVE] Adding helper to {chat_id}")
        
        await ChannelManager.add_helper_to_channel(chat_id, message)
        
        LOGGER.info("[ARCHIVE] ⏳ Waiting 15s for permissions to propagate...")
        await asyncio.sleep(15)

        await message.edit("🤖 **Adding archive bots...**")
        LOGGER.info(f"[ARCHIVE] Starting bot installation via Userbot")
        
        successful, failed = await BotManager.process_bots(
            chat_id, "add", Config.BOTS_TO_ADD, message
        )
        
        LOGGER.info(f"[ARCHIVE] Saving to Archive DB")
        await Database.save_archive_setup(chat_id, owner_id, successful)
        
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
        
        LOGGER.info(f"[ARCHIVE] ⏳ Waiting 5s safety buffer before leaving...")
        await asyncio.sleep(5)

        LOGGER.info(f"[ARCHIVE] 🚪 Helper leaving channel {chat_id}")
        try:
            await Clients.user_app.leave_chat(chat_id)
            LOGGER.info(f"[ARCHIVE] ✅ Helper left successfully")
        except Exception as e:
            LOGGER.error(f"[ARCHIVE] ❌ Helper failed to leave: {e}")

        LOGGER.info(f"=[ARCHIVE] Finished for {chat_id}=")

    except Exception as e:
        LOGGER.error(f"[ARCHIVE] FAILED: {e}")
        try: await message.edit(f"❌ **Archive Error:** `{e}`")
        except: pass
        raise

# ==================================================================
# HELP ARCHIVE COMMAND (DEBUG VERSION)
# ==================================================================

@Clients.bot.on_message(filters.command("helparchive") & (filters.group | filters.channel))
async def help_archive_handler(client, message):
    
    # 1. ENTRY LOG
    LOGGER.info(f"[DEBUG] Step 1: /helparchive triggered in {message.chat.id}")

    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
         try: await message.reply_text("⚠️ This command is for **Channels** only.")
         except: pass
         return

    chat_id = message.chat.id

    # 2. DATABASE CHECK
    try:
        LOGGER.info(f"[DEBUG] Step 2: Checking Database for conflict...")
        is_main_setup = await Database.is_channel_in_main_db(chat_id)
        if is_main_setup:
            try:
                return await message.reply_text(
                    "🛑 **Action Blocked**\n\n"
                    "This channel is already configured with the LinkerX Services"
                )
            except: return
    except Exception as e:
        LOGGER.error(f"[DEBUG] DB Check crashed: {e}")
        return

    # 3. QUEUE CHECK
    LOGGER.info(f"[DEBUG] Step 3: Checking Queue...")
    if queue_manager.get_position(chat_id):
        try: await message.reply_text("⚠️ This channel is already in queue.")
        except: pass
        return

    status = None
    try:
        LOGGER.info(f"[DEBUG] Step 4: Sending 'Checking permissions' msg...")
        status = await message.reply_text("🔍 **Checking strict permissions...**")
    except (ChatAdminRequired, ChatWriteForbidden):
        LOGGER.warning(f"[DEBUG] ❌ Bot lacks Admin/Write rights.")
        return
    except Exception as e:
        LOGGER.error(f"[DEBUG] Initial reply failed: {e}")
        return
    
    try:
        LOGGER.info(f"[DEBUG] Step 5: Fetching 'me' chat member...")
        member = await client.get_chat_member(chat_id, "me")
        privs = member.privileges

        LOGGER.info(f"[DEBUG] Step 6: Validating privileges...")
        required_privs = {
            "can_manage_chat": "Manage Channel",
            "can_change_info": "Change Channel Info",
            "can_post_messages": "Post Messages",
            "can_edit_messages": "Edit Messages",
            "can_delete_messages": "Delete Messages",
            "can_invite_users": "Invite Users via Link",
            "can_promote_members": "Add New Admins",
            "can_manage_video_chats": "Manage Video Chats",
            "can_manage_topics": "Manage Topics" 
        }

        missing = []
        if not privs:
             missing = list(required_privs.values())
        else:
            for attr, label in required_privs.items():
                val = getattr(privs, attr, None)
                if not val:
                    missing.append(label)

        # 4. GUIDE MESSAGE LOGIC
        if missing or member.status != ChatMemberStatus.ADMINISTRATOR:
            LOGGER.info(f"[DEBUG] Permissions missing. Preparing guide message...")
            try: await status.delete()
            except: pass
            
            caption = (
                "🛑 **INSUFFICIENT PERMISSIONS FOR ARCHIVE**\n\n"
                "For `/helparchive`, the bot requires **EVERY** permission enabled.\n\n"
                "❌ **Missing:**\n" + "\n".join([f"- {m}" for m in missing]) +
                "\n\n👇 **Please enable ALL permissions as shown below:**"
            )
            
            try:
                # DEBUGGING THE CONFIG VARIABLE
                link = Config.PERM_GUIDE_PIC
                LOGGER.info(f"[DEBUG] Guide Pic Link: '{link}' (Type: {type(link)})")
                
                src_chat_id = None
                src_msg_id = None
                
                if "/c/" in str(link): 
                    parts = str(link).split("/")
                    src_chat_id = int("-100" + parts[-2])
                    src_msg_id = int(parts[-1])
                elif "t.me/" in str(link): 
                    parts = str(link).split("/")
                    src_chat_id = parts[-2]
                    src_msg_id = int(parts[-1])
                
                LOGGER.info(f"[DEBUG] Copying message from {src_chat_id} ID {src_msg_id}...")

                if src_chat_id and src_msg_id:
                    await client.copy_message(
                        chat_id=chat_id,
                        from_chat_id=src_chat_id,
                        message_id=src_msg_id,
                        caption=caption
                    )
                else:
                    LOGGER.warning("[DEBUG] Invalid link format, sending text only.")
                    await message.reply_text(caption)

            except Exception as e:
                 LOGGER.error(f"[DEBUG] Failed to copy perm guide: {e}", exc_info=True)
                 await message.reply_text(caption + "\n\n*(Visual guide unavailable)*")
            return

        # 5. OWNER IDENTIFICATION
        LOGGER.info("[DEBUG] Step 7: Identifying Owner...")
        owner_id = 0
        try:
            # We suspect the issue might be here if 'filter' argument was weird before
            # Using specific filter Enum
            async for admin in client.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                if admin.status == enums.ChatMemberStatus.OWNER:
                    owner_id = admin.user.id
                    LOGGER.info(f"[DEBUG] Owner Found: {owner_id}")
                    break
        except Exception as e:
            LOGGER.warning(f"[DEBUG] Owner identification failed (Non-critical): {e}")

        LOGGER.info(f"[DEBUG] Step 8: Adding to Queue...")
        await queue_manager.add_to_queue(status, chat_id, owner_id, archive_logic)

    except (ChatAdminRequired, ChatWriteForbidden):
        return
    except Exception as e:
        # CRITICAL: LOG THE FULL TRACEBACK
        LOGGER.error("CRITICAL CRASH in helparchive", exc_info=True)
        try: await status.edit(f"❌ Error: {e}")
        except: pass

# ... (Sync and Stats commands remain unchanged) ...
@Clients.bot.on_message(filters.command("syncarchive") & filters.user(Config.OWNER_ID))
async def sync_archive_handler(client, message):
    # (Same code as before)
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
            await Database.archive_channels.update_one(
                {"channel_id": chat_id},
                {"$set": {"last_updated": datetime.utcnow()}}
            )
            try:
                member = await Clients.user_app.get_chat_member(chat_id, "me")
                if member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                    LOGGER.info(f"[SYNC-ARCHIVE] Helper found in {chat_id}, leaving...")
                    await Clients.user_app.leave_chat(chat_id)
            except UserNotParticipant: pass
            except Exception as e: LOGGER.warning(f"[SYNC-ARCHIVE] Helper check warning for {chat_id}: {e}")
        except Exception as e:
            LOGGER.error(f"[SYNC-ARCHIVE] Failed {chat_id}: {e}")
            failed_channels.append(chat_id)
        await asyncio.sleep(Config.SYNC_CHANNEL_DELAY)
    result_text = (f"✅ **Archive Sync Complete**\n\n📚 Total Scanned: `{total}`\n")
    if failed_channels: result_text += f"\n⚠️ Errors: {len(failed_channels)} (Check Logs)"
    await status.edit(result_text)

@Clients.bot.on_message(filters.command("statsarchive") & filters.user(Config.OWNER_ID))
async def stats_archive_handler(client, message):
    # (Same code as before)
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
