import os
import asyncio
import logging
from aiohttp import web, ClientSession
from pymongo import AsyncMongoClient
from pyrogram import Client, filters, idle
from pyrogram.types import ChatPrivileges
from pyrogram.errors import (
    UserNotParticipant,
    PeerIdInvalid,
    UsernameInvalid,
    FloodWait
)

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
USER_SESSION = os.environ.get("USER_SESSION")
MONGO_URL = os.environ.get("MONGO_URL")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# Convert CSV to list, remove empty strings
BOTS_TO_ADD = [b.strip() for b in os.environ.get("BOTS_TO_ADD", "GroupHelpBot").split(",") if b.strip()]

# Safety Delays
SYNC_CHANNEL_DELAY = int(os.environ.get("SYNC_CHANNEL_DELAY", 10)) # Seconds between channels in sync
SYNC_ACTION_DELAY = 3 # Seconds between adding bots in setup

PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LinkerX")

# --- DATABASE ---
mongo_client = AsyncMongoClient(MONGO_URL)
db = mongo_client["linkerx_db"]
channels_col = db["channels"]

# --- CLIENTS ---
bot = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION, in_memory=True)

# --- WEB SERVER ---
async def health_check(request):
    return web.Response(text="LinkerX is Alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

async def ping_server():
    while True:
        await asyncio.sleep(600)
        try:
            async with ClientSession() as session:
                async with session.get(URL) as resp:
                    pass
        except:
            pass

# --- CORE LOGIC: Add/Remove Bots with Status Callback ---
async def process_channel_bots(chat_id, action, bots_list, status_msg=None, current_step=0, total_steps=0):
    """
    Handles adding/removing bots.
    If status_msg is provided, it updates the message with progress.
    """
    if not bots_list:
        return [], []

    success = []
    failed = []

    privileges = ChatPrivileges(
        can_manage_chat=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_invite_users=True,
        can_pin_messages=True
    )

    for i, username in enumerate(bots_list):
        # Update User Interface (if msg provided)
        if status_msg:
            try:
                # E.g. "🔄 Setting up LinkerX...\nProcessing: BotName (2/5)"
                await status_msg.edit(
                    f"🔄 **Setting up LinkerX...**\n"
                    f"Action: {action.title()}ing Bots\n"
                    f"🤖 Current: `{username}` ({i+1}/{len(bots_list)})"
                )
            except Exception:
                pass # Ignore "message not modified" errors

        try:
            if action == 'add':
                try: await user_app.add_chat_members(chat_id, username)
                except: pass
                
                await asyncio.sleep(0.5) # Slight pause before promote
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success.append(username)
                
            elif action == 'remove':
                await user_app.promote_chat_member(chat_id, username, privileges=ChatPrivileges(can_manage_chat=False))
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success.append(username)

            # FloodWait Protection
            await asyncio.sleep(SYNC_ACTION_DELAY)

        except FloodWait as fw:
            await asyncio.sleep(fw.value)
            # Retry once after sleep
            try:
                 # (Simplified retry logic just for promote for brevity)
                 await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                 success.append(username)
            except:
                 failed.append(username)
        except Exception as e:
            logger.error(f"Failed {username} in {chat_id}: {e}")
            failed.append(username)

    return success, failed

# --- COMMANDS ---

@bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    try:
        if len(message.command) < 2:
            await message.reply_text("❌ Usage: `/setup <channel_id>`")
            return
        target_input = message.command[1]
        target_chat = int(target_input) if target_input.lstrip("-").isdigit() else target_input
    except:
        await message.reply_text("❌ Invalid ID.")
        return

    status_msg = await message.reply_text("⏳ **Verifying permissions...**")

    # 1. Verify Permissions
    try:
        member = await user_app.get_chat_member(target_chat, "me")
        can_promote = member.status == "creator" or (
            member.status == "administrator" and member.privileges and member.privileges.can_promote_members
        )
        if not can_promote:
            await status_msg.edit("⚠️ **Missing Permissions!**\nUser account must be Admin with 'Add New Admins' rights.")
            return
    except UserNotParticipant:
        await status_msg.edit("⚠️ **User Account Not Found!**\nPlease add the bot's user account to the channel first.")
        return
    except Exception as e:
        await status_msg.edit(f"❌ **Error:** {e}")
        return

    # 2. Process Bots with Live Updates
    successful, failed = await process_channel_bots(target_chat, 'add', BOTS_TO_ADD, status_msg)

    # 3. Update DB
    await channels_col.update_one(
        {"channel_id": target_chat},
        {
            "$set": {
                "channel_id": target_chat,
                "owner_id": message.from_user.id,
                "installed_bots": successful
            }
        },
        upsert=True
    )

    # 4. Final Report
    report_text = f"✅ **Setup Complete!**\n\n"
    report_text += f"📢 Channel: `{target_chat}`\n"
    report_text += f"🤖 Added: {len(successful)}/{len(BOTS_TO_ADD)}\n"
    
    if failed:
        report_text += f"⚠️ Failed: {', '.join(failed)}\n(Check if bots are valid or already admins)"

    await status_msg.edit(report_text)

@bot.on_message(filters.command("sync") & filters.user(OWNER_ID))
async def sync_all_channels(client, message):
    status_msg = await message.reply_text("🔄 **Initializing Global Sync...**")
    
    wanted_bots = set(BOTS_TO_ADD)
    processed = 0
    errors = 0
    total_channels = await channels_col.count_documents({})
    
    msg_update_interval = 5 # Update status msg every 5 channels
    current_idx = 0

    async for doc in channels_col.find():
        current_idx += 1
        chat_id = doc['channel_id']
        current_bots = set(doc.get('installed_bots', []))
        
        to_add = list(wanted_bots - current_bots)
        to_remove = list(current_bots - wanted_bots)
        
        # Periodic Status Update
        if current_idx % msg_update_interval == 0 or current_idx == 1:
            try:
                percent = int((current_idx / total_channels) * 100)
                await status_msg.edit(
                    f"🔄 **Global Sync Running**\n"
                    f"📊 Progress: {percent}%\n"
                    f"📺 Channel: {current_idx}/{total_channels}\n"
                    f"✅ Updated: {processed} | ❌ Errors: {errors}"
                )
            except: pass

        if not to_add and not to_remove:
            continue

        try:
            # We don't pass status_msg here because we don't want granular updates per bot during sync (too spammy)
            added_success, _ = await process_channel_bots(chat_id, 'add', to_add)
            removed_success, _ = await process_channel_bots(chat_id, 'remove', to_remove)
            
            new_state = list((current_bots - set(removed_success)) | set(added_success))
            
            await channels_col.update_one(
                {"channel_id": chat_id},
                {"$set": {"installed_bots": new_state}}
            )
            processed += 1
            
            # Sleep between channels
            await asyncio.sleep(SYNC_CHANNEL_DELAY)

        except Exception as e:
            errors += 1
            logger.error(f"Sync error {chat_id}: {e}")
            # Notify Owner (Optional)
            try:
                await bot.send_message(doc['owner_id'], f"⚠️ **LinkerX**: Sync failed for your channel `{chat_id}`. Please check permissions.")
            except: pass

    await status_msg.edit(
        f"✅ **Global Sync Finished**\n\n"
        f"📂 Total Channels: {total_channels}\n"
        f"📝 Updated: {processed}\n"
        f"⚠️ Errors: {errors}"
    )

async def main():
    await start_web_server()
    await bot.start()
    await user_app.start()
    asyncio.create_task(ping_server())
    logger.info("LinkerX Ultimate Started.")
    await idle()
    await bot.stop()
    await user_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
