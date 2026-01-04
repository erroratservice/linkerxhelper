import os
import asyncio
import logging
from aiohttp import web, ClientSession
from pyrogram import Client, filters, idle
from pyrogram.types import ChatPrivileges
from pyrogram.errors import (
    UserNotParticipant,
    PeerIdInvalid,
    UsernameInvalid
)

# --- CONFIGURATION (Load from Env Vars) ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
USER_SESSION = os.environ.get("USER_SESSION")
# Comma separated list of bots to add (e.g., "GroupHelpBot,MissRose_bot")
BOTS_TO_ADD = os.environ.get("BOTS_TO_ADD", "GroupHelpBot").split(",")
PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")
# ---------------------

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Clients
bot = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION, in_memory=True)

# --- WEB SERVER & PINGER ---
async def health_check(request):
    return web.Response(text="LinkerX is Alive!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

async def ping_server():
    """Pings the server every 10 minutes to keep it awake."""
    while True:
        await asyncio.sleep(600)  # 10 Minutes
        try:
            async with ClientSession() as session:
                async with session.get(URL) as resp:
                    logger.info(f"Ping sent to {URL}. Status: {resp.status}")
        except Exception as e:
            logger.error(f"Ping failed: {e}")

# --- BOT LOGIC ---
@bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    try:
        if len(message.command) < 2:
            await message.reply_text("❌ **Usage:** `/setup <channel_id_or_username>`")
            return
        
        target_input = message.command[1]
        target_chat = int(target_input) if target_input.lstrip("-").isdigit() else target_input
            
    except Exception as e:
        await message.reply_text(f"❌ Error parsing channel ID: {e}")
        return

    status_msg = await message.reply_text("⏳ **Checking permissions...**")

    # 1. Check if User Account is in the channel
    try:
        member = await user_app.get_chat_member(target_chat, "me")
    except UserNotParticipant:
        await status_msg.edit(
            "⚠️ **User Account Not Found!**\n\n"
            "The user session account is not in that channel.\n"
            "Please add the user account to the channel and try again."
        )
        return
    except (PeerIdInvalid, UsernameInvalid):
        await status_msg.edit("❌ **Invalid Channel!** Check the ID/Username.")
        return
    except Exception as e:
        await status_msg.edit(f"❌ **Error:** {e}")
        return

    # 2. Check for Admin Permissions
    can_promote = False
    if member.status == "creator":
        can_promote = True
    elif member.status == "administrator" and member.privileges and member.privileges.can_promote_members:
        can_promote = True

    if not can_promote:
        await status_msg.edit(
            "⚠️ **Missing Permissions!**\n\n"
            "The user account cannot add new admins.\n"
            "1. Promote the user account to Admin.\n"
            "2. Enable **'Add New Admins'** permission.\n"
            "3. Send `/setup <channel_id>` again."
        )
        return

    # 3. Add and Promote Bots
    await status_msg.edit(f"✅ Permissions verified. Adding {len(BOTS_TO_ADD)} bots...")
    success_count = 0
    
    new_admin_privileges = ChatPrivileges(
        can_manage_chat=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=True
    )

    for bot_username in BOTS_TO_ADD:
        bot_username = bot_username.strip()
        try:
            try:
                await user_app.add_chat_members(target_chat, bot_username)
            except Exception:
                pass # Ignore if already in chat

            await user_app.promote_chat_member(
                chat_id=target_chat,
                user_id=bot_username,
                privileges=new_admin_privileges
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to add {bot_username}: {e}")

    await status_msg.edit(
        f"🎉 **LinkerX Setup Complete!**\n\n"
        f"Successfully added/promoted **{success_count}/{len(BOTS_TO_ADD)}** bots."
    )

async def main():
    logger.info("Starting Web Server...")
    await start_web_server()
    
    logger.info("Starting Pyrogram Clients...")
    await bot.start()
    await user_app.start()
    
    # Start the pinger in the background
    asyncio.create_task(ping_server())
    
    logger.info("LinkerX Service Started.")
    await idle()
    
    await bot.stop()
    await user_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
