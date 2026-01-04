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
    UsernameInvalid
)

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
USER_SESSION = os.environ.get("USER_SESSION")
MONGO_URL = os.environ.get("MONGO_URL")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# Config List
BOTS_TO_ADD = [b.strip() for b in os.environ.get("BOTS_TO_ADD", "GroupHelpBot").split(",") if b.strip()]

PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LinkerX")

# --- DATABASE SETUP (New PyMongo Async) ---
# Note: AsyncMongoClient is the new native async class
mongo_client = AsyncMongoClient(MONGO_URL)
db = mongo_client["linkerx_db"]
channels_col = db["channels"]

# --- CLIENTS ---
bot = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION, in_memory=True)

# --- WEB SERVER (Keep Alive) ---
async def health_check(request):
    return web.Response(text="LinkerX Database Service is Alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server running on {PORT}")

async def ping_server():
    while True:
        await asyncio.sleep(600)
        try:
            async with ClientSession() as session:
                async with session.get(URL) as resp:
                    pass
        except Exception:
            pass

# --- HELPER: ADD/REMOVE BOTS ---
async def manage_bots(chat_id, action, bot_usernames):
    if not bot_usernames:
        return []

    success_list = []
    
    privileges = ChatPrivileges(
        can_manage_chat=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_promote_members=False, # Usually bots don't need to add other admins
        can_invite_users=True,
        can_pin_messages=True
    )

    for username in bot_usernames:
        try:
            if action == 'add':
                try: 
                    await user_app.add_chat_members(chat_id, username)
                except Exception: 
                    pass
                
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success_list.append(username)
                
            elif action == 'remove':
                await user_app.promote_chat_member(
                    chat_id, username, 
                    privileges=ChatPrivileges(can_manage_chat=False)
                )
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success_list.append(username)

        except Exception as e:
            logger.error(f"Error {action}ing {username} in {chat_id}: {e}")
    
    return success_list

# --- BOT COMMANDS ---

@bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    try:
        if len(message.command) < 2:
            await message.reply_text("❌ Usage: `/setup <channel_id>`")
            return
        
        target_input = message.command[1]
        target_chat = int(target_input) if target_input.lstrip("-").isdigit() else target_input
        
    except Exception:
        await message.reply_text("❌ Invalid ID format.")
        return

    status_msg = await message.reply_text("⏳ **Verifying permissions...**")

    # 1. Verify Admin Permissions
    try:
        member = await user_app.get_chat_member(target_chat, "me")
        can_promote = member.status == "creator" or (
            member.status == "administrator" and member.privileges and member.privileges.can_promote_members
        )
        
        if not can_promote:
            await status_msg.edit("⚠️ **Missing Permissions!**\nMake sure the User Account is Admin with 'Add New Admins' rights.")
            return

    except UserNotParticipant:
        await status_msg.edit("⚠️ **User Account Not Found!**\nPlease add the bot's user account to the channel first.")
        return
    except Exception as e:
        await status_msg.edit(f"❌ **Error:** {e}")
        return

    # 2. Add Bots
    await status_msg.edit(f"✅ Adding {len(BOTS_TO_ADD)} bots...")
    successful_bots = await manage_bots(target_chat, 'add', BOTS_TO_ADD)

    # 3. Save to DB (PyMongo Async)
    # Note: await is used for the database operation
    await channels_col.update_one(
        {"channel_id": target_chat},
        {
            "$set": {
                "channel_id": target_chat,
                "owner_id": message.from_user.id,
                "installed_bots": successful_bots
            }
        },
        upsert=True
    )

    await status_msg.edit(
        f"🎉 **Setup Complete!**\n"
        f"Managed Channel: `{target_chat}`\n"
        f"Bots Active: {len(successful_bots)}/{len(BOTS_TO_ADD)}"
    )

@bot.on_message(filters.command("sync") & filters.user(OWNER_ID))
async def sync_all_channels(client, message):
    status_msg = await message.reply_text("🔄 **Starting Global Sync...**")
    
    wanted_bots = set(BOTS_TO_ADD)
    processed = 0
    errors = 0

    # PyMongo Async Cursor Iteration
    # We use 'async for' to iterate over the cursor
    async for doc in channels_col.find():
        chat_id = doc['channel_id']
        owner_id = doc['owner_id']
        current_bots = set(doc.get('installed_bots', []))
        
        to_add = list(wanted_bots - current_bots)
        to_remove = list(current_bots - wanted_bots)
        
        if not to_add and not to_remove:
            continue

        try:
            added = await manage_bots(chat_id, 'add', to_add)
            removed = await manage_bots(chat_id, 'remove', to_remove)
            
            new_state = list((current_bots - set(removed)) | set(added))
            
            await channels_col.update_one(
                {"channel_id": chat_id},
                {"$set": {"installed_bots": new_state}}
            )
            processed += 1

        except Exception as e:
            errors += 1
            logger.error(f"Sync failed for {chat_id}: {e}")
            
            try:
                await bot.send_message(
                    owner_id,
                    f"⚠️ **LinkerX Service Alert**\n"
                    f"Failed to sync bots in channel `{chat_id}`.\n"
                    f"Please check admin permissions."
                )
            except Exception:
                pass 

    await status_msg.edit(f"✅ **Sync Finished**\nChannels Updated: {processed}\nErrors: {errors}")

async def main():
    await start_web_server()
    await bot.start()
    await user_app.start()
    asyncio.create_task(ping_server())
    logger.info("LinkerX (PyMongo Native Async) Started.")
    await idle()
    await bot.stop()
    await user_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
