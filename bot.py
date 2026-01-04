import os
import asyncio
import logging
from datetime import datetime
from aiohttp import web, ClientSession
from motor.motor_asyncio import AsyncIOMotorClient  # Motor async driver [web:56][web:63]
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

# Bots to add as admins
BOTS_TO_ADD = [b.strip() for b in os.environ.get("BOTS_TO_ADD", "").split(",") if b.strip()]

# Safety delays
SYNC_CHANNEL_DELAY = int(os.environ.get("SYNC_CHANNEL_DELAY", 10))  # seconds between channels in sync
SYNC_ACTION_DELAY = 2                                              # seconds between per-bot actions

# Helper user max channels (below TG limit 500)
MAX_USER_CHANNELS = int(os.environ.get("MAX_USER_CHANNELS", 100))

PORT = int(os.environ.get("PORT", 8080))
URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

# Cached usernames
_bot_username_cache = None
_helper_username_cache = None

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("LinkerX")


# --- VALIDATION & NORMALIZATION ---

def validate_bot_usernames(bots):
    normalized = []
    for bot in bots:
        bot = bot.strip()
        if not bot:
            continue
        if not bot.startswith("@"):
            bot = f"@{bot}"
        normalized.append(bot)
    return normalized


def validate_env():
    required = {
        "API_ID": API_ID,
        "API_HASH": API_HASH,
        "BOT_TOKEN": BOT_TOKEN,
        "USER_SESSION": USER_SESSION,
        "MONGO_URL": MONGO_URL,
    }
    missing = [k for k, v in required.items() if not v or (isinstance(v, int) and v == 0)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    if OWNER_ID == 0:
        logger.warning("OWNER_ID not set - /sync and /stats restricted features will be disabled")
    logger.info("✅ Environment variables validated")


BOTS_TO_ADD = validate_bot_usernames(BOTS_TO_ADD)
if not BOTS_TO_ADD:
    logger.warning("⚠️ No bots configured in BOTS_TO_ADD")

validate_env()
logger.info(f"🛡️ Safety delays: {SYNC_ACTION_DELAY}s between bots, {SYNC_CHANNEL_DELAY}s between channels")
logger.info(f"📊 Max helper user channels: {MAX_USER_CHANNELS}")

# --- DATABASE (Motor) [web:46][web:51] ---

mongo_client = AsyncIOMotorClient(MONGO_URL)
db = mongo_client["linkerx_db"]
channels_col = db["channels"]


async def init_db():
    try:
        await channels_col.create_index("channel_id", unique=True)
        await channels_col.create_index("owner_id")
        await channels_col.create_index("user_joined_at")
        await channels_col.create_index("user_is_member")
        logger.info("✅ Database indexes created")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise


# --- PYROGRAM CLIENTS ---

bot = Client("bot_client", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("user_client", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION, in_memory=True)


# --- USERNAME HELPERS ---

async def get_bot_username():
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        me = await bot.get_me()
        _bot_username_cache = me.username or None
        if _bot_username_cache:
            logger.info(f"Bot username: @{_bot_username_cache}")
        return _bot_username_cache
    except Exception as e:
        logger.error(f"Failed to get bot username: {e}")
        return None


async def get_helper_username():
    global _helper_username_cache
    if _helper_username_cache:
        return _helper_username_cache
    try:
        me = await user_app.get_me()
        _helper_username_cache = me.username or None
        if _helper_username_cache:
            logger.info(f"Helper username: @{_helper_username_cache}")
        return _helper_username_cache
    except Exception as e:
        logger.error(f"Failed to get helper username: {e}")
        return None


async def get_helper_user_id():
    try:
        me = await user_app.get_me()
        return me.id
    except Exception as e:
        logger.error(f"Failed to get helper user ID: {e}")
        return None


# --- CHANNEL MEMBERSHIP MANAGEMENT ---

async def get_active_channel_count():
    try:
        return await channels_col.count_documents({"user_is_member": True})
    except Exception as e:
        logger.error(f"Error counting active channels: {e}")
        return 0


async def manage_channel_capacity_before_join(new_channel_id):
    active_count = await get_active_channel_count()
    if active_count < MAX_USER_CHANNELS:
        return

    logger.warning(f"Helper at limit ({active_count}/{MAX_USER_CHANNELS}), freeing oldest membership")

    cursor = channels_col.find(
        {"user_is_member": True, "channel_id": {"$ne": new_channel_id}}
    ).sort("user_joined_at", 1).limit(1)

    oldest_list = await cursor.to_list(length=1)
    if not oldest_list:
        return

    oldest = oldest_list[0]
    try:
        await user_app.leave_chat(oldest["channel_id"])
        await channels_col.update_one(
            {"channel_id": oldest["channel_id"]},
            {"$set": {"user_is_member": False, "user_left_at": datetime.utcnow()}}
        )
        logger.info(f"Left oldest channel {oldest['channel_id']}")
        await asyncio.sleep(2)
    except Exception as e:
        logger.error(f"Failed to leave oldest channel {oldest['channel_id']}: {e}")


async def add_user_to_channel(chat_id):
    helper_id = await get_helper_user_id()
    if not helper_id:
        raise RuntimeError("Helper user ID unavailable")

    # Check if already member
    try:
        member = await user_app.get_chat_member(chat_id, "me")
        if member.status in ("member", "administrator", "creator"):
            await channels_col.update_one(
                {"channel_id": chat_id},
                {"$set": {"user_is_member": True}},
                upsert=False,
            )
            logger.info(f"Helper already in channel {chat_id}")
            return True
    except UserNotParticipant:
        pass

    await manage_channel_capacity_before_join(chat_id)

    try:
        await bot.add_chat_members(chat_id, helper_id)
    except UserAlreadyParticipant:
        logger.info(f"Helper already in channel {chat_id} (UserAlreadyParticipant)")
    except ChatAdminRequired:
        logger.error(f"Bot lacks rights to add helper to channel {chat_id}")
        raise
    except Exception as e:
        logger.error(f"Error adding helper to channel {chat_id}: {e}")
        raise

    await channels_col.update_one(
        {"channel_id": chat_id},
        {
            "$set": {
                "user_is_member": True,
                "user_joined_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info(f"Helper added to channel {chat_id}")
    await asyncio.sleep(2)
    return True


async def check_user_membership(chat_id):
    try:
        member = await user_app.get_chat_member(chat_id, "me")
        return member.status in ("member", "administrator", "creator")
    except UserNotParticipant:
        return False
    except Exception as e:
        logger.error(f"Error checking helper membership in {chat_id}: {e}")
        return False


# --- QUEUE SYSTEM ---

class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.waiting_users = []

    async def add_to_queue(self, message, target_chat, owner_id):
        data = {"msg": message, "chat_id": target_chat, "owner_id": owner_id}
        self.waiting_users.append(data)
        pos = len(self.waiting_users)
        await message.edit(
            f"⏳ **Added to Queue**\n"
            f"📍 Position: #{pos}\n"
            f"⏱️ Estimated wait: ~{(pos - 1) * 30}s\n"
            f"Please wait..."
        )
        await self.queue.put(data)

    async def update_positions(self):
        if not self.waiting_users:
            return
        for i, req in enumerate(self.waiting_users):
            try:
                if i == 0:
                    await req["msg"].edit("🔄 **You're Next!**\n⚙️ Starting setup now...")
                else:
                    await req["msg"].edit(
                        f"⏳ **Queue Position: #{i+1}**\n"
                        f"📊 {i} user(s) ahead of you\n"
                        f"⏱️ Estimated wait: ~{i*30}s"
                    )
            except Exception as e:
                logger.debug(f"Queue position update failed: {e}")

    async def worker(self):
        logger.info("Queue worker started")
        while True:
            data = await self.queue.get()
            if data in self.waiting_users:
                self.waiting_users.remove(data)
            asyncio.create_task(self.update_positions())

            msg = data["msg"]
            chat_id = data["chat_id"]
            owner_id = data["owner_id"]

            try:
                await msg.edit("⚙️ **Processing started...**")
                await setup_logic(msg, chat_id, owner_id)
            except Exception as e:
                logger.error(f"Worker error in {chat_id}: {e}")
                try:
                    await msg.edit(f"❌ Error during processing:\n`{e}`")
                except:
                    pass

            self.queue.task_done()
            await asyncio.sleep(2)


queue_manager = QueueManager()


# --- BOT/BOTS PROCESSING ---

async def process_channel_bots(chat_id, action, bots_list, status_msg=None):
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
        can_edit_messages=True,
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
            if action == "add":
                # Add bot as member first (ignore if already there)
                try:
                    await user_app.add_chat_members(chat_id, username)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.debug(f"add_chat_members({username}) error: {e}")
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success.append(username)
            elif action == "remove":
                await user_app.promote_chat_member(
                    chat_id, username, privileges=ChatPrivileges(can_manage_chat=False)
                )
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success.append(username)

            await asyncio.sleep(SYNC_ACTION_DELAY)

        except FloodWait as fw:
            logger.warning(f"FloodWait {fw.value}s for {username}")
            await asyncio.sleep(fw.value + 1)
            try:
                if action == "add":
                    await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                    success.append(username)
                else:
                    await user_app.promote_chat_member(
                        chat_id, username, privileges=ChatPrivileges(can_manage_chat=False)
                    )
                    success.append(username)
            except Exception as e:
                logger.error(f"Retry failed for {username}: {e}")
                failed.append(username)

        except ChatAdminRequired as e:
            logger.error(f"ChatAdminRequired for {username} in {chat_id}: {e}")
            failed.append(username)

        except Exception as e:
            logger.error(f"Failed {username} in {chat_id}: {type(e).__name__} - {e}")
            failed.append(username)

    return success, failed


async def setup_logic(message, target_chat, owner_id):
    try:
        # Ensure helper is in channel (or join respecting capacity)
        is_member = await check_user_membership(target_chat)
        if not is_member:
            await message.edit("➕ **Preparing helper account...**")
            await add_user_to_channel(target_chat)
            await asyncio.sleep(2)

        await message.edit("🤖 **Adding bots...**")
        successful, failed = await process_channel_bots(target_chat, "add", BOTS_TO_ADD, message)

        await channels_col.update_one(
            {"channel_id": target_chat},
            {
                "$set": {
                    "channel_id": target_chat,
                    "owner_id": owner_id,
                    "installed_bots": successful,
                    "last_updated": datetime.utcnow(),
                    "user_is_member": True,
                },
                "$setOnInsert": {
                    "setup_date": datetime.utcnow(),
                    "user_joined_at": datetime.utcnow(),
                },
            },
            upsert=True,
        )

        active_count = await get_active_channel_count()
        text = (
            f"✅ **Setup complete!**\n\n"
            f"📢 Channel: `{target_chat}`\n"
            f"🤖 Added: {len(successful)}/{len(BOTS_TO_ADD)}\n"
            f"📊 Helper active in: {active_count}/{MAX_USER_CHANNELS} channels\n"
        )
        if failed:
            text += f"\n⚠️ Failed: {', '.join(failed)}"
        await message.edit(text)

    except Exception as e:
        logger.error(f"Setup error in {target_chat}: {e}")
        raise


# --- COMMANDS ---

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    bot_username = await get_bot_username()
    bot_mention = f"@{bot_username}" if bot_username else "this bot"
    await message.reply_text(
        f"👋 **Welcome to LinkerX!**\n\n"
        f"**Setup steps:**\n"
        f"1️⃣ Add {bot_mention} to your channel\n"
        f"2️⃣ Promote it to admin with **Add New Admins** permission\n"
        f"3️⃣ Get channel ID from @username_to_id_bot\n"
        f"4️⃣ Run `/setup <channel_id>`\n\n"
        f"Example: `/setup -100123456789`\n\n"
        f"**Spam protection:**\n"
        f"• Helper user stays in up to {MAX_USER_CHANNELS} channels\n"
        f"• Oldest channels are automatically freed when needed\n\n"
        f"**Commands:**\n"
        f"• `/setup <channel_id>`\n"
        f"• `/list`\n"
        f"• `/help`"
    )


@bot.on_message(filters.command("help") & filters.private)
async def help_handler(client, message):
    await start_handler(client, message)


@bot.on_message(filters.command("setup") & filters.private)
async def setup_handler(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "❌ Invalid format.\n\n"
            "Usage: `/setup <channel_id>`\n"
            "Tip: use @username_to_id_bot to get the ID"
        )
        return

    raw_id = message.command[1]
    try:
        target_chat = int(raw_id) if raw_id.lstrip("-").isdigit() else raw_id
    except:
        await message.reply_text("❌ Invalid channel ID format")
        return

    status = await message.reply_text("🔍 **Checking bot permissions...**")

    # Relaxed but correct permission check for bot [web:71][web:17]
    try:
        member = await bot.get_chat_member(target_chat, "me")
        logger.info(f"Bot member in {target_chat}: status={member.status}, privileges={member.privileges}")

        is_admin = member.status in ("administrator", "creator")
        has_promote_flag = bool(getattr(member.privileges, "can_promote_members", False))

        can_promote = is_admin  # relaxed: any admin is allowed; real rights checked on API call

        if is_admin and not has_promote_flag:
            logger.warning(
                f"Bot is admin in {target_chat} but can_promote_members is False/None; "
                "continuing, real rights will be validated on add/promote."
            )

        if not can_promote:
            bot_username = await get_bot_username()
            bot_mention = f"@{bot_username}" if bot_username else "the bot"
            await status.edit(
                f"⚠️ **Missing permissions!**\n\n"
                f"{bot_mention} must be an admin in the channel with 'Add New Admins' enabled."
            )
            return

    except UserNotParticipant:
        bot_username = await get_bot_username()
        bot_mention = f"@{bot_username}" if bot_username else "the bot"
        await status.edit(
            f"⚠️ **Bot not in channel!**\n\n"
            f"Please add {bot_mention} to the channel and promote it to admin."
        )
        return
    except Exception as e:
        logger.error(f"Bot permission check error: {e}")
        await status.edit(f"❌ Error checking permissions: `{e}`")
        return

    await queue_manager.add_to_queue(status, target_chat, message.from_user.id)


@bot.on_message(filters.command("list") & filters.private)
async def list_handler(client, message):
    try:
        cursor = channels_col.find({"owner_id": message.from_user.id})
        docs = await cursor.to_list(length=100)

        if not docs:
            await message.reply_text(
                "📭 No channels registered.\nUse `/setup <channel_id>` to add one."
            )
            return

        active = [d for d in docs if d.get("user_is_member")]
        inactive = [d for d in docs if not d.get("user_is_member")]

        text = "📂 **Your LinkerX channels**\n\n"
        if active:
            text += "**🟢 Active (helper present):**\n"
            for i, ch in enumerate(active, 1):
                bots = ch.get("installed_bots", [])
                jd = ch.get("user_joined_at")
                text += f"{i}. `{ch['channel_id']}`\n"
                text += f"   🤖 Bots: {len(bots)}/{len(BOTS_TO_ADD)}\n"
                if jd:
                    text += f"   📅 Joined: {jd.strftime('%Y-%m-%d')}\n"
                text += "\n"
        if inactive:
            text += "**⚪ Inactive (helper not present):**\n"
            for i, ch in enumerate(inactive, 1):
                bots = ch.get("installed_bots", [])
                text += f"{i}. `{ch['channel_id']}`\n"
                text += f"   🤖 Bots: {len(bots)}/{len(BOTS_TO_ADD)}\n\n"

        active_count = await get_active_channel_count()
        text += f"📊 Summary: {len(docs)} total, {active_count}/{MAX_USER_CHANNELS} active"
        await message.reply_text(text)
    except Exception as e:
        logger.error(f"/list error: {e}")
        await message.reply_text(f"❌ Error: `{e}`")


@bot.on_message(filters.command("sync") & filters.user(OWNER_ID))
async def sync_all_channels(client, message):
    status = await message.reply_text("🔄 **Global sync started...**")
    processed = 0
    errors = 0
    rejoined = 0

    try:
        channels = await channels_col.find({}).to_list(length=None)
        total = len(channels)

        for idx, ch in enumerate(channels, 1):
            chat_id = ch["channel_id"]
            current = set(ch.get("installed_bots", []))
            wanted = set(BOTS_TO_ADD)
            to_add = list(wanted - current)
            to_remove = list(current - wanted)

            if not to_add and not to_remove:
                continue

            try:
                if not await check_user_membership(chat_id):
                    await add_user_to_channel(chat_id)
                    rejoined += 1
                    await asyncio.sleep(2)

                added, _ = await process_channel_bots(chat_id, "add", to_add)
                removed, _ = await process_channel_bots(chat_id, "remove", to_remove)

                new_state = list((current - set(removed)) | set(added))
                await channels_col.update_one(
                    {"channel_id": chat_id},
                    {"$set": {"installed_bots": new_state, "last_updated": datetime.utcnow()}},
                )
                processed += 1
                await asyncio.sleep(SYNC_CHANNEL_DELAY)

            except Exception as e:
                errors += 1
                logger.error(f"Sync error in {chat_id}: {e}")
                try:
                    await bot.send_message(
                        ch["owner_id"],
                        f"⚠️ LinkerX sync failed for `{chat_id}`:\n`{e}`\n"
                        f"Please run `/setup {chat_id}` again."
                    )
                except:
                    pass

            if idx % 5 == 0:
                try:
                    await status.edit(
                        f"🔄 Syncing...\n\n"
                        f"Progress: {idx}/{total}\n"
                        f"✅ Updated: {processed}\n"
                        f"🔄 Rejoined: {rejoined}\n"
                        f"❌ Errors: {errors}"
                    )
                except:
                    pass

        await status.edit(
            f"✅ **Sync finished!**\n\n"
            f"📊 Total channels: {total}\n"
            f"📝 Updated: {processed}\n"
            f"🔄 Rejoined: {rejoined}\n"
            f"⚠️ Errors: {errors}"
        )
    except Exception as e:
        logger.error(f"Global sync error: {e}")
        await status.edit(f"❌ Sync failed: `{e}`")


@bot.on_message(filters.command("stats") & filters.user(OWNER_ID))
async def stats_handler(client, message):
    try:
        total = await channels_col.count_documents({})
        owners = len(await channels_col.distinct("owner_id"))
        active = await get_active_channel_count()

        pipeline = [
            {"$project": {"bot_count": {"$size": "$installed_bots"}}},
            {"$group": {"_id": None, "total_bots": {"$sum": "$bot_count"}}},
        ]
        res = await channels_col.aggregate(pipeline).to_list(length=1)
        total_bots = res[0]["total_bots"] if res else 0

        cursor = channels_col.find({"user_is_member": True}).sort("user_joined_at", 1).limit(1)
        oldest_list = await cursor.to_list(length=1)
        oldest_info = "N/A"
        if oldest_list:
            jd = oldest_list[0].get("user_joined_at")
            if jd:
                days = (datetime.utcnow() - jd).days
                oldest_info = f"{days} days ago"

        bot_username = await get_bot_username()
        helper_username = await get_helper_username()

        text = (
            f"📊 **LinkerX stats**\n\n"
            f"📺 Channels: {total}\n"
            f"👥 Owners: {owners}\n"
            f"🤖 Bot installs: {total_bots}\n"
            f"⚙️ Configured bots: {len(BOTS_TO_ADD)}\n"
            f"📋 Queue size: {queue_manager.queue.qsize()}\n"
            f"⏳ Waiting: {len(queue_manager.waiting_users)}\n\n"
            f"🛡️ Spam protection:\n"
            f"Active memberships: {active}/{MAX_USER_CHANNELS}\n"
            f"Oldest membership: {oldest_info}\n\n"
            f"Accounts:\n"
            f"🤖 Bot: @{bot_username or 'N/A'}\n"
            f"👤 Helper: @{helper_username or 'N/A'}"
        )
        await message.reply_text(text)
    except Exception as e:
        logger.error(f"/stats error: {e}")
        await message.reply_text(f"❌ Error: `{e}`")


# --- WEB SERVER ---

async def health_check(request):
    return web.Response(text="✅ LinkerX is alive")


async def start_web():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"🌐 Web server started on port {PORT}")


async def ping():
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(600)
        try:
            async with ClientSession() as session:
                async with session.get(URL, timeout=10) as resp:
                    logger.info(f"🏓 Self-ping: {resp.status}")
        except Exception as e:
            logger.error(f"Ping failed: {e}")


# --- MAIN ---

async def main():
    try:
        await start_web()
        await init_db()

        await bot.start()
        logger.info("Bot client started")

        try:
            await user_app.start()
            logger.info("User session started")
        except Exception as e:
            logger.critical(f"Failed to start user session: {e}")
            await bot.stop()
            return

        await get_bot_username()
        await get_helper_username()

        asyncio.create_task(queue_manager.worker())
        asyncio.create_task(ping())

        logger.info("LinkerX service ready")
        await idle()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
    finally:
        logger.info("Shutting down...")
        try:
            await bot.stop()
        except:
            pass
        try:
            await user_app.stop()
        except:
            pass
        try:
            mongo_client.close()
        except:
            pass
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
