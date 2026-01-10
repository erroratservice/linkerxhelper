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

# ... (archive_logic function remains unchanged) ...
async def archive_logic(message, chat_id, owner_id):
    # (Keep the existing archive_logic code exactly as it was)
    # ...
    LOGGER.info(f"=[ARCHIVE] SETUP STARTED for channel {chat_id}=")
    try:
        await message.edit("➕ **Preparing helper account with FULL access...**")
        LOGGER.info(f"[ARCHIVE] Adding helper to {chat_id}")
        await ChannelManager.add_helper_to_channel(chat_id, message)
        LOGGER.info("[ARCHIVE] ⏳ Waiting 15s for permissions to propagate...")
        await asyncio.sleep(15)
        await message.edit("🤖 **Adding archive bots...**")
        LOGGER.info(f"[ARCHIVE] Starting bot installation via Userbot")
        successful, failed = await BotManager.process_bots(chat_id, "add", Config.BOTS_TO_ADD, message)
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
        LOGGER.info(f"[ARCHIVE] 👢 Bot kicking Helper from {chat_id}")
        try:
            helper_me = await Clients.user_app.get_me()
            helper_id = helper_me.id
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
                # PARSE LINK: https://t.me/c/1234567890/24 -> chat_id=-1001234567890, msg_id=24
                link = Config.PERM_GUIDE_PIC
                src_chat_id = None
                src_msg_id = None
                
                if "/c/" in link: # Private Link (e.g. t.me/c/17823.../24)
                    parts = link.split("/")
                    src_chat_id = int("-100" + parts[-2])
                    src_msg_id = int(parts[-1])
                elif "t.me/" in link: # Public Link (e.g. t.me/MyChannel/24)
                    parts = link.split("/")
                    src_chat_id = parts[-2]
                    src_msg_id = int(parts[-1])
                
                # COPY MESSAGE (Acts like forward without tag)
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
                 # Fallback to text if copying fails (e.g. bot not in source channel)
                 await message.reply_text(caption + "\n\n*(Visual guide unavailable - ensure bot is in the source channel)*")
            return

        # 4. Identify Owner (rest of code is same)
        owner_id = None
        async for admin in client.get_chat_members(chat_id, filter=ChatMemberStatus.OWNER):
            owner_id = admin.user.id
            break
            
        if not owner_id:
             if message.from_user:
                 owner_id = message.from_user.id
             else:
                 owner_id = 0 
                 LOGGER.warning(f"Could not identify owner for {chat_id}, using ID 0")

        await queue_manager.add_to_queue(status, chat_id, owner_id, archive_logic)

    except (ChatAdminRequired, ChatWriteForbidden):
        return
    except Exception as e:
        LOGGER.error(f"HelpArchive error: {e}")
        try: await status.edit(f"❌ Error: {e}")
        except: pass

# ... (sync_archive and stats_archive handlers remain same) ...
@Clients.bot.on_message(filters.command("syncarchive") & filters.user(Config.OWNER_ID))
async def sync_archive_handler(client, message):
    # (Existing sync code)
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
            try:
                helper_me = await Clients.user_app.get_me()
                member = await Clients.user_app.get_chat_member(chat_id, "me")
                if member.status not in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                    LOGGER.info(f"[SYNC-ARCHIVE] Helper found in {chat_id}, kicking...")
                    await client.ban_chat_member(chat_id, helper_me.id)
                    await asyncio.sleep(1)
                    await client.unban_chat_member(chat_id, helper_me.id)
            except UserNotParticipant: pass
            except Exception as e: LOGGER.warning(f"[SYNC-ARCHIVE] Check failed for {chat_id}: {e}")
        except Exception as e:
            LOGGER.error(f"[SYNC-ARCHIVE] Failed {chat_id}: {e}")
            failed_channels.append(chat_id)
        await asyncio.sleep(Config.SYNC_CHANNEL_DELAY)
    result_text = (f"✅ **Archive Sync Complete**\n\n📚 Total Scanned: `{total}`\n")
    if failed_channels: result_text += f"\n⚠️ Errors: {len(failed_channels)}"
    await status.edit(result_text)

@Clients.bot.on_message(filters.command("statsarchive") & filters.user(Config.OWNER_ID))
async def stats_archive_handler(client, message):
    # (Existing stats code)
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
