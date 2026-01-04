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
    FloodWait,
    ChatAdminRequired,
    UserAlreadyParticipant
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
SYNC_ACTION_DELAY = 2  # Seconds between adding bots

# Channel limit - Keep user in max 100 channels to avoid spam flags
MAX_USER_CHANNELS = int(os.environ.get("MAX_USER_CHANNELS", 100))  # Safe limit, well below 500

PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# Cache for usernames
_bot_username_cache = None
_helper_username_cache = None

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

# Log safety settings
logger.info(f"🛡️ Safety delays: {SYNC_ACTION_DELAY}s between bots, {SYNC_CHANNEL_DELAY}s between channels")
logger.info(f"📊 Max user channels: {MAX_USER_CHANNELS} (spam protection)")

# --- DATABASE ---
mongo_client = AsyncMongoClient(MONGO_URL)
db = mongo_client["linkerx_db"]
channels_col = db["channels"]

async def init_db():
    """Initialize database indexes"""
    try:
        await channels_col.create_index("channel_id", unique=True)
        await channels_col.create_index("owner_id")
        await channels_col.create_index("user_joined_at")  # Track join order for cleanup
        await channels_col.create_index("user_is_member")  # Fast lookup of active channels
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"❌ Database initialization error: {e}")
        raise

# --- CLIENTS ---
bot = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION, in_memory=True)

# --- USERNAME FUNCTIONS ---
async def get_bot_username():
    """Get the username of the bot (cached)"""
    global _bot_username_cache
    
    if _bot_username_cache:
        return _bot_username_cache
    
    try:
        me = await bot.get_me()
        _bot_username_cache = me.username or None
        if _bot_username_cache:
            logger.info(f"✅ Bot username cached: @{_bot_username_cache}")
        return _bot_username_cache
    except Exception as e:
        logger.error(f"Failed to get bot username: {e}")
        return None

async def get_helper_username():
    """Get the username of the helper account (cached)"""
    global _helper_username_cache
    
    if _helper_username_cache:
        return _helper_username_cache
    
    try:
        me = await user_app.get_me()
        _helper_username_cache = me.username or None
        if _helper_username_cache:
            logger.info(f"✅ Helper username cached: @{_helper_username_cache}")
        return _helper_username_cache
    except Exception as e:
        logger.error(f"Failed to get helper username: {e}")
        return None

async def get_helper_user_id():
    """Get the user ID of the helper account"""
    try:
        me = await user_app.get_me()
        return me.id
    except Exception as e:
        logger.error(f"Failed to get helper user ID: {e}")
        return None

# --- CHANNEL MEMBERSHIP MANAGEMENT ---
async def get_active_channel_count():
    """Get count of channels where user is currently a member"""
    try:
        count = await channels_col.count_documents({"user_is_member": True})
        return count
    except Exception as e:
        logger.error(f"Error counting active channels: {e}")
        return 0

async def manage_channel_capacity_before_join(new_channel_id):
    """
    Before joining a new channel, check capacity and remove from oldest if needed.
    This prevents spam flags by maintaining a stable membership count.
    """
    active_count = await get_active_channel_count()
    
    if active_count >= MAX_USER_CHANNELS:
        logger.warning(f"⚠️ At channel limit ({active_count}/{MAX_USER_CHANNELS}). Removing from oldest channel...")
        
        # Find the OLDEST channel where user is member (by join timestamp)
        cursor = channels_col.find(
            {"user_is_member": True, "channel_id": {"$ne": new_channel_id}}
        ).sort("user_joined_at", 1).limit(1)
        
        oldest_channels = await cursor.to_list(length=1)
        
        if oldest_channels:
            oldest = oldest_channels[0]
            try:
                # Leave the oldest channel
                await user_app.leave_chat(oldest['channel_id'])
                
                # Update database
                await channels_col.update_one(
                    {"channel_id": oldest['channel_id']},
                    {
                        "$set": {
                            "user_is_member": False,
                            "user_left_at": datetime.utcnow()
                        }
                    }
                )
                
                logger.info(f"✅ Left oldest channel {oldest['channel_id']} (joined {oldest.get('user_joined_at')})")
                await asyncio.sleep(2)  # Brief delay after leaving
                
            except Exception as e:
                logger.error(f"Failed to leave oldest channel {oldest['channel_id']}: {e}")

async def add_user_to_channel(chat_id):
    """
    Bot adds user account to channel and tracks membership.
    Manages capacity before joining to prevent spam flags.
    """
    helper_id = await get_helper_user_id()
    if not helper_id:
        raise Exception("Could not get helper user ID")
    
    # Check if already a member
    try:
        member = await user_app.get_chat_member(chat_id, "me")
        if member.status in ["member", "administrator", "creator"]:
            logger.info(f"✅ Helper already in channel {chat_id}")
            
            # Update membership status in case it was marked as not member
            await channels_col.update_one(
                {"channel_id": chat_id},
                {"$set": {"user_is_member": True}},
                upsert=False
            )
            return True
    except UserNotParticipant:
        pass  # Need to join
    
    # Manage capacity BEFORE joining (spam protection)
    await manage_channel_capacity_before_join(chat_id)
    
    try:
        # Bot adds user account
        await bot.add_chat_members(chat_id, helper_id)
        
        # Update database with join timestamp
        await channels_col.update_one(
            {"channel_id": chat_id},
            {
                "$set": {
                    "user_is_member": True,
                    "user_joined_at": datetime.utcnow()
                }
            },
            upsert=False
        )
        
        logger.info(f"✅ Added helper to channel {chat_id}")
        await asyncio.sleep(2)  # Brief delay after joining
        return True
        
    except UserAlreadyParticipant:
        logger.info(f"Helper already in channel {chat_id}")
        await channels_col.update_one(
            {"channel_id": chat_id},
            {"$set": {"user_is_member": True}},
            upsert=False
        )
        return True
        
    except Exception as e:
        logger.error(f"Failed to add helper to channel: {e}")
        raise

async def check_user_membership(chat_id):
    """Check if user account is currently in the channel"""
    try:
        member = await user_app.get_chat_member(chat_id, "me")
        return member.status in ["member", "administrator", "creator"]
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.error(f"Error checking membership: {e}")
        return False

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
            await asyncio.sleep(2)

queue_manager = QueueManager()

# --- HELPER LOGIC ---
async def process_channel_bots(chat_id, action, bots_list, status_msg=None):
    """Process adding or removing bots from a channel (user account must be in channel)"""
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
                # Try to add member first
                try:
                    await user_app.add_chat_members(chat_id, username)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.debug(f"Add member {username}: {e}")
                
                # Promote to admin
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success.append(username)
                
            elif action == 'remove':
                await user_app.promote_chat_member(
                    chat_id, 
                    username, 
                    privileges=ChatPrivileges(can_manage_chat=False)
                )
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success.append(username)
            
            await asyncio.sleep(SYNC_ACTION_DELAY)
            
        except FloodWait as fw:
            logger.warning(f"⏳ FloodWait {fw.value}s for {username}")
            await asyncio.sleep(fw.value + 1)
            
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
    """
    Main setup logic - bot is already admin.
    User account joins and STAYS in channel (spam protection).
    """
    
    try:
        # Step 1: Check if user is already in channel
        is_member = await check_user_membership(target_chat)
        
        if not is_member:
            # Step 2: Add user account to channel (manages capacity automatically)
            await message.edit("➕ **Managing channel access...**")
            await add_user_to_channel(target_chat)
            await asyncio.sleep(2)  # Ensure membership is registered
        else:
            logger.info(f"User already in channel {target_chat}")
        
        # Step 3: User account adds bots
        await message.edit("🤖 **Adding bots...**")
        successful, failed = await process_channel_bots(
            target_chat, 
            'add', 
            BOTS_TO_ADD, 
            message
        )
        
        # Step 4: Update database (USER STAYS IN CHANNEL)
        await channels_col.update_one(
            {"channel_id": target_chat},
            {
                "$set": {
                    "channel_id": target_chat,
                    "owner_id": owner_id,
                    "installed_bots": successful,
                    "last_updated": datetime.utcnow(),
                    "user_is_member": True  # IMPORTANT: User stays
                },
                "$setOnInsert": {
                    "setup_date": datetime.utcnow(),
                    "user_joined_at": datetime.utcnow()
                }
            },
            upsert=True
        )
        
        # Get current active channel count
        active_count = await get_active_channel_count()
        
        # Final message
        text = (
            f"✅ **Setup Complete!**\n\n"
            f"📢 Channel: `{target_chat}`\n"
            f"🤖 Added: {len(successful)}/{len(BOTS_TO_ADD)}\n"
            f"📊 Active channels: {active_count}/{MAX_USER_CHANNELS}\n"
        )
        
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
        
        await message.edit(text)
        
    except Exception as e:
        logger.error(f"Setup error: {e}")
        raise

# --- COMMANDS ---

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    """Welcome message with setup instructions"""
    bot_username = await get_bot_username()
    bot_mention = f"@{bot_username}" if bot_username else "this bot"
    
    await message.reply_text(
        f"👋 **Welcome to LinkerX Service Setup!**\n\n"
        f"I can configure your channel with the required bots automatically.\n\n"
        f"**🚀 Setup Instructions:**\n\n"
        f"1️⃣ **Add** {bot_mention} to your channel\n\n"
        f"2️⃣ **Promote** it to Admin with:\n"
        f"   ✅ __'Add New Admins'__ permission enabled\n\n"
        f"3️⃣ **Get your Channel ID** from @username_to_id_bot\n\n"
        f"4️⃣ **Run the command:**\n"
        f"   `/setup <channel_id>`\n"
        f"   Example: `/setup -100123456789`\n\n"
        f"**🛡️ Spam Protection:**\n"
        f"• Helper account stays in up to {MAX_USER_CHANNELS} channels\n"
        f"• Oldest channels auto-removed when limit reached\n"
        f"• Natural usage pattern prevents spam flags\n\n"
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
    
    try:
        target_chat = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
    except:
        await message.reply_text("❌ Invalid ID format. Please check and try again.")
        return

    status_msg = await message.reply_text("🔍 **Checking permissions...**")

    # Verify BOT permissions (not user account)
    try:
        bot_member = await bot.get_chat_member(target_chat, "me")
        can_promote = (
            bot_member.status == "creator" or 
            (bot_member.status == "administrator" and 
             bot_member.privileges and 
             bot_member.privileges.can_promote_members)
        )
        
        if not can_promote:
            bot_username = await get_bot_username()
            bot_mention = f"@{bot_username}" if bot_username else "the bot"
            
            await status_msg.edit(
                f"⚠️ **Missing Permissions!**\n\n"
                f"{bot_mention} needs:\n"
                f"✅ Admin status\n"
                f"✅ 'Add New Admins' permission enabled\n\n"
                f"Please update permissions and try again."
            )
            return
            
    except UserNotParticipant:
        bot_username = await get_bot_username()
        bot_mention = f"@{bot_username}" if bot_username else "the bot"
        
        await status_msg.edit(
            f"⚠️ **Bot Not Found!**\n\n"
            f"Please add {bot_mention} to your channel first,\n"
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
        
        active_channels = [ch for ch in channels if ch.get('user_is_member')]
        inactive_channels = [ch for ch in channels if not ch.get('user_is_member')]
        
        if active_channels:
            text += "**🟢 Active (Helper Present):**\n"
            for idx, ch in enumerate(active_channels, 1):
                bots = ch.get('installed_bots', [])
                text += f"{idx}. `{ch['channel_id']}`\n"
                text += f"   🤖 Bots: {len(bots)}/{len(BOTS_TO_ADD)}\n"
                join_date = ch.get('user_joined_at')
                if join_date:
                    text += f"   📅 Joined: {join_date.strftime('%Y-%m-%d')}\n"
                text += "\n"
        
        if inactive_channels:
            text += "\n**⚪ Inactive (Helper Removed):**\n"
            for idx, ch in enumerate(inactive_channels, 1):
                bots = ch.get('installed_bots', [])
                text += f"{idx}. `{ch['channel_id']}`\n"
                text += f"   🤖 Bots: {len(bots)}/{len(BOTS_TO_ADD)}\n"
                text += "\n"
        
        # Show capacity info
        active_count = await get_active_channel_count()
        text += f"📊 **Summary:**\n"
        text += f"Total: {len(channels)} | Active: {active_count}/{MAX_USER_CHANNELS}"
        
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
    rejoined = 0
    
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
            
            if not to_add and not to_remove:
                continue
            
            try:
                # Check if user is already in channel
                is_member = await check_user_membership(chat_id)
                
                if not is_member:
                    # Need to rejoin
                    logger.info(f"Rejoining channel {chat_id} for sync")
                    await add_user_to_channel(chat_id)
                    rejoined += 1
                    await asyncio.sleep(2)
                
                # Sync bots
                added_success, _ = await process_channel_bots(chat_id, 'add', to_add)
                removed_success, _ = await process_channel_bots(chat_id, 'remove', to_remove)
                
                # Update database (keep membership status as is)
                new_state = list((current - set(removed_success)) | set(added_success))
                await channels_col.update_one(
                    {"channel_id": chat_id},
                    {"$set": {
                        "installed_bots": new_state,
                        "last_updated": datetime.utcnow()
                    }}
                )
                
                processed += 1
                await asyncio.sleep(SYNC_CHANNEL_DELAY)
                
            except Exception as e:
                errors += 1
                logger.error(f"Sync error for {chat_id}: {e}")
                
                # Notify owner
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
            
            if idx % 5 == 0:
                try:
                    await status_msg.edit(
                        f"🔄 **Syncing...**\n\n"
                        f"Progress: {idx}/{total}\n"
                        f"✅ Updated: {processed}\n"
                        f"🔄 Rejoined: {rejoined}\n"
                        f"❌ Errors: {errors}"
                    )
                except:
                    pass
        
        await status_msg.edit(
            f"✅ **Sync Finished!**\n\n"
            f"📊 Total Channels: {total}\n"
            f"📝 Updated: {processed}\n"
            f"🔄 Rejoined: {rejoined}\n"
            f"⚠️ Errors: {errors}"
        )
        
    except Exception as e:
        logger.error(f"Global sync error: {e}")
        await status_msg.edit(f"❌ **Sync Failed:** {str(e)}")

@bot.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats_handler(client, message):
    """Show global statistics (Owner only)"""
    try:
        total_channels = await channels_col.count_documents({})
        unique_owners = len(await channels_col.distinct("owner_id"))
        active_memberships = await get_active_channel_count()
        
        pipeline = [
            {"$project": {"bot_count": {"$size": "$installed_bots"}}},
            {"$group": {"_id": None, "total_bots": {"$sum": "$bot_count"}}}
        ]
        result = await channels_col.aggregate(pipeline).to_list(length=1)
        total_bots = result[0]['total_bots'] if result else 0
        
        bot_username = await get_bot_username()
        helper_username = await get_helper_username()
        
        # Calculate oldest membership
        cursor = channels_col.find({"user_is_member": True}).sort("user_joined_at", 1).limit(1)
        oldest = await cursor.to_list(length=1)
        oldest_info = "N/A"
        if oldest:
            join_date = oldest[0].get('user_joined_at')
            if join_date:
                days_ago = (datetime.utcnow() - join_date).days
                oldest_info = f"{days_ago} days ago"
        
        text = (
            f"📊 **LinkerX Statistics**\n\n"
            f"📺 Total Channels: {total_channels}\n"
            f"👥 Unique Users: {unique_owners}\n"
            f"🤖 Total Bot Installs: {total_bots}\n"
            f"⚙️ Configured Bots: {len(BOTS_TO_ADD)}\n"
            f"📋 Queue Size: {queue_manager.queue.qsize()}\n"
            f"⏳ Waiting: {len(queue_manager.waiting_users)}\n\n"
            f"**🛡️ Spam Protection:**\n"
            f"Active Memberships: {active_memberships}/{MAX_USER_CHANNELS}\n"
            f"Oldest Membership: {oldest_info}\n\n"
            f"**Accounts:**\n"
            f"🤖 Bot: @{bot_username or 'N/A'}\n"
            f"👤 Helper: @{helper_username or 'N/A'}"
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
    await asyncio.sleep(60)
    
    while True:
        await asyncio.sleep(600)
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
        await start_web()
        await init_db()
        
        await bot.start()
        logger.info("✅ Bot client started")
        
        try:
            await user_app.start()
            logger.info("✅ User session started")
        except Exception as e:
            logger.critical(f"❌ Failed to start user session: {e}")
            await bot.stop()
            return
        
        # Pre-fetch usernames
        bot_username = await get_bot_username()
        helper_username = await get_helper_username()
        
        if bot_username:
            logger.info(f"✅ Bot Account: @{bot_username}")
        if helper_username:
            logger.info(f"✅ Helper Account: @{helper_username}")
        
        asyncio.create_task(queue_manager.worker())
        asyncio.create_task(ping())
        
        logger.info("🚀 LinkerX Service Ready")
        logger.info(f"📝 Configured with {len(BOTS_TO_ADD)} bots")
        logger.info(f"🛡️ Spam protection: Max {MAX_USER_CHANNELS} channels")
        
        await idle()
        
    except KeyboardInterrupt:
        logger.info("⚠️ Received interrupt signal")
    except Exception as e:
        logger.critical(f"❌ Fatal error: {e}")
    finally:
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
            await mongo_client.close()  # FIX: Added await
            logger.info("✅ Database connection closed")
        except:
            pass
        
        logger.info("✅ Shutdown complete")

# FIX: Changed how we run the event loop
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("Program terminated by user")
    finally:
        loop.close()
