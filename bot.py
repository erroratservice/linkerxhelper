import os
import asyncio
import logging
from datetime import datetime
from aiohttp import web, ClientSession
from pymongo import AsyncMongoClient
from pyrogram import Client, filters, idle
from pyrogram.types import ChatPrivileges
from pyrogram.errors import (
    UserNotParticipant,
    FloodWait
)

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
USER_SESSION = os.environ.get("USER_SESSION", "")
MONGO_URL = os.environ.get("MONGO_URL", "")
OWNER_ID = int(os.environ.get("OWNER_ID", 0))

# Config
BOTS_TO_ADD = [b.strip() for b in os.environ.get("BOTS_TO_ADD", "").split(",") if b.strip()]

# Safety Delays - IMPORTANT FOR ACCOUNT PROTECTION
SYNC_CHANNEL_DELAY = int(os.environ.get("SYNC_CHANNEL_DELAY", 10))  # Seconds between channels in sync
SYNC_ACTION_DELAY = 2  # Seconds between adding bots in setup

PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# Cache for username (will be fetched dynamically)
_username_cache = None

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("LinkerX")

# --- VALIDATION ---
def validate_bot_usernames(bots):
    """Ensure bot usernames have @ prefix"""
    validated = []
    for bot in bots:
        bot = bot.strip()
        if not bot:
            continue
        if not bot.startswith("@"):
            bot = f"@{bot}"
        validated.append(bot)
    return validated

def validate_env():
    """Validate required environment variables"""
    required = {
        "API_ID": API_ID,
        "API_HASH": API_HASH,
        "BOT_TOKEN": BOT_TOKEN,
        "USER_SESSION": USER_SESSION,
        "MONGO_URL": MONGO_URL
    }
    
    missing = []
    
    for name, value in required.items():
        if not value or (isinstance(value, int) and value == 0):
            missing.append(name)
    
    if missing:
        raise ValueError(f"❌ Missing required environment variables: {', '.join(missing)}")
    
    if OWNER_ID == 0:
        logger.warning("⚠️ OWNER_ID not set - /sync and /stats commands will not work")
    
    logger.info("✅ Environment variables validated")

# Validate and process bot usernames
BOTS_TO_ADD = validate_bot_usernames(BOTS_TO_ADD)

if not BOTS_TO_ADD:
    logger.warning("⚠️ No bots configured in BOTS_TO_ADD")

# Validate environment
validate_env()

# Log safety delays
logger.info(f"🛡️ Safety delays: {SYNC_ACTION_DELAY}s between bots, {SYNC_CHANNEL_DELAY}s between channels")

# --- DATABASE ---
mongo_client = AsyncMongoClient(MONGO_URL)
db = mongo_client["linkerx_db"]
channels_col = db["channels"]

async def init_db():
    """Initialize database indexes"""
    try:
        await channels_col.create_index("channel_id", unique=True)
        await channels_col.create_index("owner_id")
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")
        raise

# --- CLIENTS ---
bot = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION, in_memory=True)

# --- HELPER USERNAME FUNCTION ---
async def get_helper_username():
    """Get the username of the helper account (cached)"""
    global _username_cache
    
    if _username_cache:
        return _username_cache
    
    try:
        me = await user_app.get_me()
        _username_cache = me.username or None
        if _username_cache:
            logger.info(f"✅ Helper username cached: @{_username_cache}")
        return _username_cache
    except Exception as e:
        logger.error(f"Failed to get username: {e}")
        return None

# --- QUEUE SYSTEM ---
class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.waiting_users = [] 

    async def add_to_queue(self, message, target_chat, owner_id):
        """Add a setup request to the queue"""
        request_data = {
            'msg': message, 
            'chat_id': target_chat, 
            'owner_id': owner_id
        }
        self.waiting_users.append(request_data)
        
        position = len(self.waiting_users)
        await message.edit(
            f"⏳ **Added to Queue**\n"
            f"📍 Position: #{position}\n"
            f"⏱️ Estimated wait: ~{(position - 1) * 30}s\n"
            f"Please wait..."
        )
        await self.queue.put(request_data)

    async def update_positions(self):
        """Update queue positions for all waiting users"""
        if not self.waiting_users: 
            return
            
        for i, req in enumerate(self.waiting_users):
            try:
                if i == 0:
                    await req['msg'].edit(
                        "🔄 **You're Next!**\n"
                        "⚙️ Starting setup now..."
                    )
                else:
                    await req['msg'].edit(
                        f"⏳ **Queue Position: #{i + 1}**\n"
                        f"📊 {i} user(s) ahead of you\n"
                        f"⏱️ Estimated wait: ~{i * 30}s"
                    )
            except Exception as e:
                logger.debug(f"Position update failed: {e}")

    async def worker(self):
        """Process queue requests one by one"""
        logger.info("✅ Queue Worker Started")
        while True:
            request_data = await self.queue.get()
            
            # Remove from waiting list
            if request_data in self.waiting_users:
                self.waiting_users.remove(request_data)
            
            # Update positions for remaining users
            asyncio.create_task(self.update_positions())

            message = request_data['msg']
            chat_id = request_data['chat_id']
            owner_id = request_data['owner_id']
            
            try:
                await message.edit("⚙️ **Processing Started...**")
                await setup_logic(message, chat_id, owner_id)
            except Exception as e:
                logger.error(f"Worker Error: {e}")
                try:
                    await message.edit(f"❌ **Error during processing:**\n{str(e)}")
                except:
                    pass
            
            self.queue.task_done()
            # DELAY BETWEEN QUEUE REQUESTS - IMPORTANT FOR RATE LIMITING
            await asyncio.sleep(2)

queue_manager = QueueManager()

# --- HELPER LOGIC ---
async def process_channel_bots(chat_id, action, bots_list, status_msg=None):
    """Process adding or removing bots from a channel"""
    if not bots_list:
        return [], []
        
    success, failed = [], []
    
    privileges = ChatPrivileges(
        can_manage_chat=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_invite_users=True,
        can_pin_messages=True,
        can_post_messages=True,
        can_edit_messages=True
    )

    for i, username in enumerate(bots_list):
        # Update status message
        if status_msg:
            try:
                await status_msg.edit(
                    f"⚙️ **Processing...**\n"
                    f"🤖 Bot: `{username}`\n"
                    f"📊 Progress: {i+1}/{len(bots_list)}"
                )
            except:
                pass

        try:
            if action == 'add':
                # Try to add member first (ignore if already member)
                try:
                    await user_app.add_chat_members(chat_id, username)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.debug(f"Add member {username}: {e}")
                
                # Promote to admin
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success.append(username)
                
            elif action == 'remove':
                # Demote from admin
                await user_app.promote_chat_member(
                    chat_id, 
                    username, 
                    privileges=ChatPrivileges(can_manage_chat=False)
                )
                # Remove from channel
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success.append(username)
            
            # CRITICAL DELAY - Prevents Telegram rate limits and account restrictions
            await asyncio.sleep(SYNC_ACTION_DELAY)
            
        except FloodWait as fw:
            logger.warning(f"⏳ FloodWait {fw.value}s for {username}")
            await asyncio.sleep(fw.value + 1)
            
            # Retry once after waiting
            try:
                if action == 'add':
                    await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                    success.append(username)
                else:
                    await user_app.promote_chat_member(
                        chat_id, 
                        username, 
                        privileges=ChatPrivileges(can_manage_chat=False)
                    )
                    success.append(username)
            except Exception as e:
                logger.error(f"Retry failed for {username}: {e}")
                failed.append(username)
                
        except Exception as e:
            logger.error(f"Failed {username} in {chat_id}: {type(e).__name__} - {e}")
            failed.append(username)
            
    return success, failed

async def setup_logic(message, target_chat, owner_id):
    """Main setup logic for adding bots to channel"""
    # Process bots
    successful, failed = await process_channel_bots(
        target_chat, 
        'add', 
        BOTS_TO_ADD, 
        message
    )
    
    # Update database with results
    await channels_col.update_one(
        {"channel_id": target_chat},
        {
            "$set": {
                "channel_id": target_chat,
                "owner_id": owner_id,
                "installed_bots": successful,
                "last_updated": datetime.utcnow()
            },
            "$setOnInsert": {
                "setup_date": datetime.utcnow()
            }
        },
        upsert=True
    )
    
    # Send completion message
    text = (
        f"✅ **Setup Complete!**\n\n"
        f"📢 Channel: `{target_chat}`\n"
        f"🤖 Added: {len(successful)}/{len(BOTS_TO_ADD)}\n"
    )
    
    if failed:
        text += f"\n⚠️ Failed: {', '.join(failed)}"
    
    await message.edit(text)

# --- COMMANDS ---

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    """Welcome message with setup instructions"""
    # Dynamically fetch username
    username = await get_helper_username()
    
    if username:
        helper_mention = f"@{username}"
    else:
        helper_mention = "the LinkerX Helper Account"
    
    await message.reply_text(
        f"👋 **Welcome to LinkerX Service Setup!**\n\n"
        f"I can configure your channel with the required bots automatically.\n\n"
        f"**🚀 Setup Instructions:**\n\n"
        f"1️⃣ **Add** this user to your channel:\n"
        f"   👉 {helper_mention}\n\n"
        f"2️⃣ **Promote** it to Admin with:\n"
        f"   ✅ __'Add New Admins'__ permission enabled\n\n"
        f"3️⃣ **Get your Channel ID** from @username_to_id_bot\n\n"
        f"4️⃣ **Run the command:**\n"
        f"   `/setup <channel_id>`\n"
        f"   Example: `/setup -100123456789`\n\n"
        f"**📋 Other Commands:**\n"
        f"• `/list` - View your channels\n"
        f"• `/help` - Show this message"
    )

@bot.on_message(filters.command("help") & filters.private)
async def help_handler(client, message):
    """Show help message"""
    await start_handler(client, message)

@bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    """Setup LinkerX in a channel"""
    if len(message.command) < 2:
        await message.reply_text(
            "❌ **Invalid Format**\n\n"
            "Usage: `/setup <channel_id>`\n\n"
            "💡 Tip: Use @username_to_id_bot to get your channel ID"
        )
        return

    raw_id = message.command[1]
    
    # Parse channel ID
    try:
        target_chat = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
    except:
        await message.reply_text("❌ Invalid ID format. Please check and try again.")
        return

    status_msg = await message.reply_text("🔍 **Checking permissions...**")

    # Verify permissions
    try:
        member = await user_app.get_chat_member(target_chat, "me")
        can_promote = (
            member.status == "creator" or 
            (member.status == "administrator" and 
             member.privileges and 
             member.privileges.can_promote_members)
        )
        
        if not can_promote:
            await status_msg.edit(
                "⚠️ **Missing Permissions!**\n\n"
                "The LinkerX helper account needs:\n"
                "✅ Admin status\n"
                "✅ 'Add New Admins' permission enabled\n\n"
                "Please update permissions and try again."
            )
            return
            
    except UserNotParticipant:
        # Dynamically fetch username for error message
        username = await get_helper_username()
        helper_mention = f"@{username}" if username else "the LinkerX Helper Account"
        
        await status_msg.edit(
            f"⚠️ **User Not Found!**\n\n"
            f"Please add {helper_mention} to your channel first,\n"
            f"then promote it to admin with 'Add New Admins' permission."
        )
        return
        
    except Exception as e:
        logger.error(f"Permission check error: {e}")
        await status_msg.edit(f"❌ **Error:** {str(e)}")
        return

    # Add to queue
    await queue_manager.add_to_queue(status_msg, target_chat, message.from_user.id)

@bot.on_message(filters.command("list") & filters.private)
async def list_handler(client, message):
    """Show user's channels"""
    try:
        cursor = channels_col.find({"owner_id": message.from_user.id})
        channels = await cursor.to_list(length=100)
        
        if not channels:
            await message.reply_text(
                "📭 **No Channels Found**\n\n"
                "You haven't set up LinkerX in any channels yet.\n\n"
                "Use `/setup <channel_id>` to get started!"
            )
            return
        
        text = "📂 **Your LinkerX Channels:**\n\n"
        
        for idx, ch in enumerate(channels, 1):
            bots = ch.get('installed_bots', [])
            setup_date = ch.get('setup_date', None)
            
            text += f"{idx}. **Channel ID:** `{ch['channel_id']}`\n"
            text += f"   🤖 Bots: {len(bots)}/{len(BOTS_TO_ADD)}\n"
            
            if setup_date:
                text += f"   📅 Setup: {setup_date.strftime('%Y-%m-%d')}\n"
            
            text += "\n"
        
        text += f"📊 Total: {len(channels)} channel(s)"
        
        await message.reply_text(text)
        
    except Exception as e:
        logger.error(f"List error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

@bot.on_message(filters.command("sync") & filters.user(OWNER_ID))
async def sync_all_channels(client, message):
    """Sync all channels with current bot configuration (Owner only)"""
    status_msg = await message.reply_text("🔄 **Global Sync Started...**")
    
    processed = 0
    errors = 0
    
    try:
        channels = await channels_col.find({}).to_list(length=None)
        total = len(channels)
        
        logger.info(f"Starting sync for {total} channels")
        
        for idx, doc in enumerate(channels, 1):
            chat_id = doc['channel_id']
            current = set(doc.get('installed_bots', []))
            wanted = set(BOTS_TO_ADD)
            
            to_add = list(wanted - current)
            to_remove = list(current - wanted)
            
            # Skip if no changes needed
            if not to_add and not to_remove:
                continue
            
            try:
                # Process additions and removals
                added_success, _ = await process_channel_bots(chat_id, 'add', to_add)
                removed_success, _ = await process_channel_bots(chat_id, 'remove', to_remove)
                
                # Update database
                new_state = list((current - set(removed_success)) | set(added_success))
                await channels_col.update_one(
                    {"channel_id": chat_id},
                    {"$set": {
                        "installed_bots": new_state,
                        "last_updated": datetime.utcnow()
                    }}
                )
                
                processed += 1
                
                # CRITICAL DELAY BETWEEN CHANNELS - Prevents account restrictions
                await asyncio.sleep(SYNC_CHANNEL_DELAY)
                
            except Exception as e:
                errors += 1
                logger.error(f"Sync error for {chat_id}: {e}")
                
                # Notify channel owner
                try:
                    await bot.send_message(
                        doc['owner_id'],
                        f"⚠️ **LinkerX Sync Failed**\n\n"
                        f"🆔 Channel: `{chat_id}`\n"
                        f"❌ Error: {str(e)[:100]}\n\n"
                        f"Please run `/setup {chat_id}` again to fix."
                    )
                except:
                    pass
            
            # Update status every 5 channels
            if idx % 5 == 0:
                try:
                    await status_msg.edit(
                        f"🔄 **Syncing...**\n\n"
                        f"Progress: {idx}/{total}\n"
                        f"✅ Updated: {processed}\n"
                        f"❌ Errors: {errors}"
                    )
                except:
                    pass
        
        # Final report
        await status_msg.edit(
            f"✅ **Sync Finished!**\n\n"
            f"📊 Total Channels: {total}\n"
            f"📝 Updated: {processed}\n"
            f"⚠️ Errors: {errors}"
        )
        
    except Exception as e:
        logger.error(f"Global sync error: {e}")
        await status_msg.edit(f"❌ **Sync Failed:** {str(e)}")

@bot.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats_handler(client, message):
    """Show global statistics (Owner only)"""
    try:
        # Get basic counts
        total_channels = await channels_col.count_documents({})
        unique_owners = len(await channels_col.distinct("owner_id"))
        
        # Calculate total bot installations
        pipeline = [
            {"$project": {"bot_count": {"$size": "$installed_bots"}}},
            {"$group": {"_id": None, "total_bots": {"$sum": "$bot_count"}}}
        ]
        result = await channels_col.aggregate(pipeline).to_list(length=1)
        total_bots = result[0]['total_bots'] if result else 0
        
        # Get helper username
        username = await get_helper_username()
        helper_info = f"@{username}" if username else "Not available"
        
        text = (
            f"📊 **LinkerX Statistics**\n\n"
            f"📺 Total Channels: {total_channels}\n"
            f"👥 Unique Users: {unique_owners}\n"
            f"🤖 Total Bot Installs: {total_bots}\n"
            f"⚙️ Configured Bots: {len(BOTS_TO_ADD)}\n"
            f"📋 Queue Size: {queue_manager.queue.qsize()}\n"
            f"⏳ Waiting: {len(queue_manager.waiting_users)}\n"
            f"👤 Helper Account: {helper_info}"
        )
        
        await message.reply_text(text)
        
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await message.reply_text(f"❌ **Error:** {str(e)}")

# --- WEB SERVER ---
async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="✅ LinkerX is Alive")

async def start_web():
    """Start web server for health checks"""
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 Web server started on port {PORT}")

async def ping():
    """Self-ping to prevent Render sleep"""
    # Initial delay
    await asyncio.sleep(60)
    
    while True:
        await asyncio.sleep(600)  # Every 10 minutes
        try:
            async with ClientSession() as session:
                async with session.get(URL, timeout=10) as resp:
                    logger.info(f"🏓 Self-ping: {resp.status}")
        except Exception as e:
            logger.error(f"❌ Ping failed: {e}")

# --- MAIN ---
async def main():
    """Main entry point"""
    try:
        # Start web server
        await start_web()
        
        # Initialize database
        await init_db()
        
        # Start bot client
        await bot.start()
        logger.info("✅ Bot client started")
        
        # Start user session with error handling
        try:
            await user_app.start()
            logger.info("✅ User session started")
        except Exception as e:
            logger.critical(f"❌ Failed to start user session: {e}")
            logger.critical("Check USER_SESSION environment variable")
            await bot.stop()
            return
        
        # Pre-fetch and cache username
        username = await get_helper_username()
        if username:
            logger.info(f"✅ Helper Account: @{username}")
        else:
            logger.warning("⚠️ Could not fetch helper username")
        
        # Start background workers
        asyncio.create_task(queue_manager.worker())
        asyncio.create_task(ping())
        
        logger.info("🚀 LinkerX Service Ready")
        logger.info(f"📝 Configured with {len(BOTS_TO_ADD)} bots")
        
        # Keep alive
        await idle()
        
    except KeyboardInterrupt:
        logger.info("⚠️ Received interrupt signal")
    except Exception as e:
        logger.critical(f"❌ Fatal error: {e}")
    finally:
        # Graceful shutdown
        logger.info("🛑 Shutting down...")
        
        try:
            await bot.stop()
            logger.info("✅ Bot stopped")
        except:
            pass
        
        try:
            await user_app.stop()
            logger.info("✅ User session stopped")
        except:
            pass
        
        try:
            mongo_client.close()
            logger.info("✅ Database connection closed")
        except:
            pass
        
        logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program terminated by user")
