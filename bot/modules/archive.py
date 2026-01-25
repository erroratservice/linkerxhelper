import asyncio
from pyrogram import filters, enums
from pyrogram.enums import ChatMemberStatus, ChatType, ChatMembersFilter
from pyrogram.errors import (
    ChatAdminRequired,
    UserNotParticipant,
    ChatWriteForbidden,
    FloodWait,
    ChannelInvalid,
    PeerIdInvalid,
    ChannelPrivate,
    UserAlreadyParticipant
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
# 1. LOGIC WORKER (The Main Setup Process)
# ==================================================================

async def archive_logic(message, chat_id, owner_id):
    """
    Executes the setup logic after queue and permission checks.
    """
    LOGGER.info(f"=[ARCHIVE] SETUP STARTED for channel {chat_id}=")
    
    # --- ACTIVATE TRAFFIC LIGHT (PAUSE SYNC) ---
    ChannelManager.ACTIVE_SETUPS.add(chat_id)
    
    try:
        # 1. Add Helper
        await message.edit("➕ **Preparing helper account with FULL access...**")
        LOGGER.info(f"[ARCHIVE] Adding helper to {chat_id}")
        
        await ChannelManager.add_helper_to_channel(chat_id, message)
        
        # SAFETY: Wait for permissions to sync across DCs
        LOGGER.info("[ARCHIVE] ⏳ Waiting 15s for permissions to propagate...")
        await asyncio.sleep(15)

        # 2. Add Bots
        await message.edit("🤖 **Adding archive bots...**")
        LOGGER.info(f"[ARCHIVE] Starting bot installation via Userbot")
        
        successful, failed = await BotManager.process_bots(
            chat_id, "add", Config.BOTS_TO_ADD, message
        )
        
        # 3. Save to DB
        LOGGER.info(f"[ARCHIVE] Saving to Archive DB")
        await Database.save_archive_setup(chat_id, owner_id, successful)
        
        # 4. Result Message
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
        
        # 5. Cleanup (Helper Leaves)
        # SAFETY: Buffer before leaving to ensure commands processed
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
        
    finally:
        # --- DEACTIVATE TRAFFIC LIGHT (RESUME SYNC) ---
        ChannelManager.ACTIVE_SETUPS.discard(chat_id)

# ==================================================================
# 2. HELP ARCHIVE COMMAND (Trigger)
# ==================================================================

@Clients.bot.on_message(filters.command("helparchive") & (filters.group | filters.channel))
async def help_archive_handler(client, message):
    
    LOGGER.info(f"[DEBUG] /helparchive triggered in {message.chat.id}")

    # Check Channel Type
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
         try: await message.reply_text("⚠️ This command is for **Channels** only.")
         except: pass
         return

    chat_id = message.chat.id

    # Check Queue
    if queue_manager.get_position(chat_id):
        try: await message.reply_text("⚠️ This channel is already in queue.")
        except: pass
        return

    # Send Status
    status = None
    try:
        status = await message.reply_text("🔍 **Checking permissions...**")
    except (ChatAdminRequired, ChatWriteForbidden):
        LOGGER.warning(f"[DEBUG] ❌ Bot lacks basic Write rights.")
        return
    except Exception as e:
        LOGGER.error(f"[DEBUG] Initial reply failed: {e}")
        return
    
    # Check DB Conflict
    try:
        is_main_setup = await Database.is_channel_in_main_db(chat_id)
        if is_main_setup:
            await status.edit("🛑 **Action Blocked**\nChannel already configured in Main DB.")
            return
    except Exception as e:
        LOGGER.error(f"[DEBUG] DB Check failed: {e}")

    # Permission Check
    try:
        LOGGER.info(f"[DEBUG] Fetching fresh chat member data for bot...")
        member = await client.get_chat_member(chat_id, "me")
        privs = member.privileges
        
        # REQUIRED PRIVILEGES (Removed 'can_manage_topics')
        required_privs = {
            "can_manage_chat": "Manage Channel",
            "can_change_info": "Change Channel Info",
            "can_post_messages": "Post Messages",
            "can_edit_messages": "Edit Messages",
            "can_delete_messages": "Delete Messages",
            "can_invite_users": "Invite Users via Link",
            "can_promote_members": "Add New Admins",
            "can_manage_video_chats": "Manage Video Chats"
        }

        missing = []

        # 1. Admin Status
        if member.status != ChatMemberStatus.ADMINISTRATOR:
            LOGGER.warning(f"[DEBUG] ❌ Bot is NOT administrator. Status: {member.status}")
            missing.append("Bot must be an Administrator")

        # 2. Individual Privileges
        if not privs:
            LOGGER.warning("[DEBUG] ❌ Privileges object is None!")
            missing.extend(list(required_privs.values()))
        else:
            LOGGER.info("[DEBUG] --- Checking Individual Permissions ---")
            for attr, label in required_privs.items():
                has_perm = getattr(privs, attr, False)
                LOGGER.info(f"[DEBUG] Checking '{attr}' ({label})... Found: {has_perm}")
                
                if not has_perm:
                    missing.append(label)

        # Show Guide if permissions missing
        if missing:
            LOGGER.info(f"[DEBUG] ❌ Missing List: {missing}")
            try: await status.delete()
            except: pass
            
            caption = (
                "🛑 **INSUFFICIENT PERMISSIONS**\n\n"
                "The bot requires **EVERY** permission enabled.\n\n"
                "❌ **Missing:**\n" + "\n".join([f"- {m}" for m in missing]) +
                "\n\n👇 **Please enable ALL permissions as shown:**"
            )
            
            try:
                link = str(Config.PERM_GUIDE_PIC)
                LOGGER.info(f"[DEBUG] Sending guide using link: {link}")
                
                src_chat_id, src_msg_id = None, None
                
                if "/c/" in link: 
                    parts = link.split("/")
                    src_chat_id = int("-100" + parts[-2])
                    src_msg_id = int(parts[-1])
                elif "t.me/" in link: 
                    parts = link.split("/")
                    src_chat_id = parts[-2]
                    src_msg_id = int(parts[-1])
                
                if src_chat_id and src_msg_id:
                    await client.copy_message(chat_id, src_chat_id, src_msg_id, caption=caption)
                else:
                    await message.reply_text(caption)
            except Exception as e:
                 LOGGER.error(f"[DEBUG] Failed to send guide: {e}")
                 await message.reply_text(caption + "\n\n*(Visual guide unavailable)*")
            return

        # Find Owner
        LOGGER.info("[DEBUG] Permissions OK. Finding owner...")
        owner_id = 0
        try:
            async for admin in client.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                if admin.status == enums.ChatMemberStatus.OWNER:
                    owner_id = admin.user.id
                    LOGGER.info(f"[DEBUG] Owner found: {owner_id}")
                    break
        except Exception as e:
            LOGGER.warning(f"[DEBUG] Owner check failed: {e}")

        # Add to Queue
        LOGGER.info("[DEBUG] Adding to processing queue...")
        await queue_manager.add_to_queue(status, chat_id, owner_id, archive_logic)

    except Exception as e:
        LOGGER.error("CRITICAL CRASH in helparchive", exc_info=True)
        try: await status.edit(f"❌ Error: {e}")
        except: pass

# ==================================================================
# 3. SYNC ARCHIVE COMMAND (With Pause Logic & 10s Safety)
# ==================================================================

@Clients.bot.on_message(filters.command("syncarchive") & filters.user(Config.OWNER_ID))
async def sync_archive_handler(client, message):
    """
    Advanced Maintenance with 'Traffic Light' Pause System.
    """
    status = await message.reply_text("♻️ **Starting Smart Archive Sync...**")
    
    channels = await Database.get_all_archive_channels()
    total = len(channels)
    processed = 0
    deleted = 0
    repaired = 0
    skipped = 0
    
    LOGGER.info(f"[SYNC-ARCHIVE] Started Smart Sync for {total} channels")

    required_bots = {bot.lstrip('@').lower() for bot in Config.BOTS_TO_ADD}

    for channel_data in channels:
        
        # --- [TRAFFIC LIGHT] PAUSE LOGIC ---
        while len(ChannelManager.ACTIVE_SETUPS) > 0:
            LOGGER.info(f"[SYNC] ⏸️ Paused due to active setup in {ChannelManager.ACTIVE_SETUPS}...")
            try: await status.edit(f"⏸️ **Paused...**\nPriority Setup Running.\nWill resume shortly.")
            except: pass
            await asyncio.sleep(5)
        # -----------------------------------

        chat_id = channel_data.get("channel_id")
        processed += 1
        
        # FIX: Update on 1st channel, then every 5th channel
        if processed == 1 or processed % 5 == 0:
            try: 
                await status.edit(
                    f"♻️ **Smart Syncing...**\n"
                    f"Progress: `{processed}/{total}`\n"
                    f"🗑 Deleted: `{deleted}`\n"
                    f"🔧 Repaired: `{repaired}`\n"
                    f"✅ OK: `{skipped}`"
                )
            except Exception as e:
                LOGGER.warning(f"Status update failed: {e}")

        try:
            # ====================================================
            # STEP A: EXISTENCE CHECK
            # ====================================================
            try:
                # We try to get chat info. If this fails, channel is likely dead/inaccessible
                await Clients.bot.get_chat(chat_id)
            except (ChannelInvalid, PeerIdInvalid, ChannelPrivate):
                LOGGER.warning(f"[SYNC] 🗑 Channel {chat_id} is dead. Removing from DB.")
                await Database.archive_channels.delete_one({"channel_id": chat_id})
                deleted += 1
                continue 
            except Exception as e:
                LOGGER.error(f"[SYNC] ⚠️ Error accessing {chat_id}: {e}")
                continue

            # ====================================================
            # STEP B: CHECK BOT STATUS
            # ====================================================
            current_bots = set()
            try:
                # Get all admins to see which bots are present
                async for member in Clients.bot.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
                    if member.user.is_bot:
                        current_bots.add(member.user.username.lower())
            except Exception as e:
                LOGGER.warning(f"[SYNC] Could not fetch admins for {chat_id}: {e}")
            
            # Calculate missing bots
            missing_bots = required_bots - current_bots
            
            if not missing_bots:
                # ALL BOTS PRESENT -> Healthy
                await Database.archive_channels.update_one(
                    {"channel_id": chat_id},
                    {"$set": {"last_updated": datetime.utcnow()}}
                )
                skipped += 1
                
                # Check if Helper is lingering in a healthy channel
                try:
                    await Clients.user_app.leave_chat(chat_id)
                    LOGGER.info(f"[SYNC] Helper removed from healthy channel {chat_id}")
                except: pass
                
                continue

            # ====================================================
            # STEP C: REPAIR (Bots are missing)
            # ====================================================
            LOGGER.info(f"[SYNC] 🔧 Repairing {chat_id}. Missing: {len(missing_bots)}")
            
            # 1. Check/Add Helper
            helper_me = await Clients.user_app.get_me()
            helper_in_chat = False
            
            try:
                await Clients.user_app.get_chat_member(chat_id, "me")
                helper_in_chat = True
            except UserNotParticipant:
                helper_in_chat = False
            except Exception:
                helper_in_chat = False

            if not helper_in_chat:
                LOGGER.info(f"[SYNC] ➕ Adding Helper to {chat_id}...")
                try:
                    # FIX: Use ChannelManager to JOIN via LINK
                    await ChannelManager.add_helper_to_channel(chat_id, status_message=None)
                    
                    # SAFETY: Wait 10s AFTER JOINING
                    LOGGER.info("[SYNC] ⏳ Waiting 10s after join...")
                    await asyncio.sleep(10)
                except Exception as e:
                    LOGGER.error(f"[SYNC] ❌ Failed to add Helper to {chat_id}: {e}")
                    continue # Cannot proceed without helper

            # 2. Add Missing Bots (Using Helper)
            bots_to_install = [f"@{b}" for b in missing_bots]
            
            try:
                await BotManager.process_bots(chat_id, "add", bots_to_install, status_msg=None)
                repaired += 1
            except Exception as e:
                LOGGER.error(f"[SYNC] Failed to install bots in {chat_id}: {e}")

            # 3. Helper Cleanup
            # SAFETY: Wait 10s BEFORE LEAVING
            LOGGER.info(f"[SYNC] ⏳ Waiting 10s before leaving {chat_id}...")
            await asyncio.sleep(10) 
            
            try:
                await Clients.user_app.leave_chat(chat_id)
                LOGGER.info(f"[SYNC] 🚪 Helper left {chat_id}")
            except Exception as e:
                LOGGER.warning(f"[SYNC] Helper failed to leave {chat_id}: {e}")

        except Exception as e:
            LOGGER.error(f"[SYNC] Critical error processing {chat_id}: {e}")

        # Rate Limit Protection
        await asyncio.sleep(Config.SYNC_CHANNEL_DELAY)

    # Final Report
    await status.edit(
        f"✅ **Smart Sync Complete**\n\n"
        f"📚 Scanned: `{total}`\n"
        f"🗑 Removed Dead: `{deleted}`\n"
        f"🔧 Repaired: `{repaired}`\n"
        f"✅ Already Healthy: `{skipped}`"
    )

# ==================================================================
# 4. STATS ARCHIVE COMMAND
# ==================================================================

@Clients.bot.on_message(filters.command("statsarchive") & filters.user(Config.OWNER_ID))
async def stats_archive_handler(client, message):
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
