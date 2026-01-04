import os
import asyncio
import logging
from aiohttp import web, ClientSession
from pymongo import AsyncMongoClient
from pyrogram import Client, filters, idle
from pyrogram.types import ChatPrivileges
from pyrogram.errors import (
    UserNotParticipant,
    FloodWait
)

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
USER_SESSION = os.environ.get("USER_SESSION")
MONGO_URL = os.environ.get("MONGO_URL")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# Config
BOTS_TO_ADD = [b.strip() for b in os.environ.get("BOTS_TO_ADD", "GroupHelpBot").split(",") if b.strip()]
PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# Global Variable to store the User Session Username
HELPER_USERNAME = "LinkerX_Helper" 

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

# --- QUEUE SYSTEM ---
class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.waiting_users = [] 

    async def add_to_queue(self, message, target_chat):
        request_data = {'msg': message, 'chat_id': target_chat}
        self.waiting_users.append(request_data)
        
        position = len(self.waiting_users)
        await message.edit(f"⏳ **Added to Queue**\nPosition: #{position}\nPlease wait...")
        await self.queue.put(request_data)

    async def update_positions(self):
        if not self.waiting_users: return
        for i, req in enumerate(self.waiting_users):
            try:
                if i == 0: await req['msg'].edit("🔄 **Queue Update:**\nYou are Next! Starting soon...")
                else: await req['msg'].edit(f"⏳ **Queue Update:**\nCurrent Position: #{i + 1}")
            except: pass

    async def worker(self):
        logger.info("Queue Worker Started")
        while True:
            request_data = await self.queue.get()
            if request_data in self.waiting_users: self.waiting_users.remove(request_data)
            asyncio.create_task(self.update_positions())

            message = request_data['msg']
            chat_id = request_data['chat_id']
            
            try:
                await message.edit("⚙️ **Processing Started...**")
                await setup_logic(message, chat_id)
            except Exception as e:
                logger.error(f"Worker Error: {e}")
                try: await message.edit(f"❌ Error during processing: {e}")
                except: pass
            
            self.queue.task_done()
            await asyncio.sleep(2) 

queue_manager = QueueManager()

# --- HELPER LOGIC ---
async def process_channel_bots(chat_id, action, bots_list, status_msg=None):
    if not bots_list: return [], []
    success, failed = [], []
    privileges = ChatPrivileges(
        can_manage_chat=True, can_delete_messages=True, can_restrict_members=True,
        can_promote_members=False, can_invite_users=True, can_pin_messages=True
    )

    for i, username in enumerate(bots_list):
        if status_msg:
            try: await status_msg.edit(f"⚙️ **Processing...**\nBot: `{username}` ({i+1}/{len(bots_list)})")
            except: pass

        try:
            if action == 'add':
                try: await user_app.add_chat_members(chat_id, username)
                except: pass
                await asyncio.sleep(0.5)
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success.append(username)
            elif action == 'remove':
                await user_app.promote_chat_member(chat_id, username, privileges=ChatPrivileges(can_manage_chat=False))
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success.append(username)
            await asyncio.sleep(2)
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try: 
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success.append(username)
            except: failed.append(username)
        except: failed.append(username)
    return success, failed

async def setup_logic(message, target_chat):
    successful, failed = await process_channel_bots(target_chat, 'add', BOTS_TO_ADD, message)
    await channels_col.update_one(
        {"channel_id": target_chat},
        {"$set": {"channel_id": target_chat, "owner_id": message.chat.id, "installed_bots": successful}},
        upsert=True
    )
    text = f"✅ **Setup Complete!**\nAdded: {len(successful)}/{len(BOTS_TO_ADD)}"
    if failed: text += f"\nFailed: {', '.join(failed)}"
    await message.edit(text)

# --- COMMANDS ---

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    # Dynamic username used here
    helper_mention = f"@{HELPER_USERNAME}" if HELPER_USERNAME else "the LinkerX Account"
    
    await message.reply_text(
        f"👋 **Welcome to LinkerX Service Setup!**\n\n"
        f"I can configure your channel with the required bots automatically.\n\n"
        f"**🚀 Setup Instructions:**\n"
        f"1. **Add** this user to your channel:\n"
        f"   👉 `{helper_mention}` (Click to copy)\n\n"
        f"2. **Promote** it to Admin with:\n"
        f"   ✅ __'Add New Admins'__ permission enabled.\n\n"
        f"3. **Get your Channel ID** from @username_to_id_bot\n\n"
        f"4. **Run the command:**\n"
        f"   `/setup <channel_id>`\n"
        f"   Example: `/setup -100123456789`"
    )

@bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    if len(message.command) < 2:
        await message.reply_text("❌ **Invalid Format**\nUsage: `/setup <channel_id>`\nTip: Use @username_to_id_bot for IDs.")
        return

    raw_id = message.command[1]
    try:
        target_chat = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
    except:
        await message.reply_text("❌ Invalid ID format.")
        return

    status_msg = await message.reply_text("🔍 **Checking permissions...**")

    try:
        member = await user_app.get_chat_member(target_chat, "me")
        if not (member.status == "creator" or (member.privileges and member.privileges.can_promote_members)):
            await status_msg.edit("⚠️ **Missing Permissions!**\nPlease enable **'Add New Admins'** for the user account.")
            return
    except UserNotParticipant:
        await status_msg.edit(f"⚠️ **User Not Found!**\nPlease add `@{HELPER_USERNAME}` to the channel first.")
        return
    except Exception as e:
        await status_msg.edit(f"❌ Error: {e}")
        return

    await queue_manager.add_to_queue(status_msg, target_chat)

@bot.on_message(filters.command("sync") & filters.user(OWNER_ID))
async def sync_all_channels(client, message):
    status_msg = await message.reply_text("🔄 **Global Sync Started...**")
    processed = 0
    
    async for doc in channels_col.find():
        chat_id = doc['channel_id']
        current = set(doc.get('installed_bots', []))
        wanted = set(BOTS_TO_ADD)
        
        if (wanted - current) or (current - wanted):
            try:
                s_add, _ = await process_channel_bots(chat_id, 'add', list(wanted - current))
                s_rem, _ = await process_channel_bots(chat_id, 'remove', list(current - wanted))
                new_state = list((current - set(s_rem)) | set(s_add))
                await channels_col.update_one({"channel_id": chat_id}, {"$set": {"installed_bots": new_state}})
                processed += 1
                await asyncio.sleep(5) 
            except Exception: pass
            
            if processed % 5 == 0: await status_msg.edit(f"🔄 **Syncing...**\nUpdated: {processed}")

    await status_msg.edit(f"✅ **Sync Finished!**\nTotal Updated: {processed}")

# --- SERVER & RUN ---
async def health_check(request): return web.Response(text="Alive")
async def start_web():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

async def ping():
    while True:
        await asyncio.sleep(600)
        try:
             async with ClientSession() as session:
                async with session.get(URL) as resp: pass
        except: pass

async def main():
    global HELPER_USERNAME
    await start_web()
    
    # Start Clients
    await bot.start()
    await user_app.start()
    
    # --- FETCH USERNAME HERE ---
    try:
        me = await user_app.get_me()
        HELPER_USERNAME = me.username
        logger.info(f"User Session detected as: @{HELPER_USERNAME}")
    except Exception as e:
        logger.error(f"Could not fetch User Session username: {e}")
    # ---------------------------

    asyncio.create_task(queue_manager.worker())
    asyncio.create_task(ping())
    
    logger.info("LinkerX Service Ready.")
    await idle()
    await bot.stop()
    await user_app.stop()

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
