import asyncio
from pyrogram.errors import FloodWait
from bot.utils.logger import LOGGER
from bot.helpers.database import Database
from bot.client import Clients
from config import Config

class VirtualMessage:
    """Wrapper to make a message_id behave like a Pyrogram Message object"""
    def __init__(self, chat_id, message_id):
        self.chat_id = chat_id
        self.id = message_id
        self.chat = type('obj', (object,), {'id': chat_id})
        
    async def edit(self, text):
        try:
            await Clients.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.id,
                text=text
            )
        except Exception as e:
            LOGGER.debug(f"VirtualMessage edit failed: {e}")

class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.waiting_users = []
        self.current_task = None  # Track what is currently running
    
    def calculate_wait(self, position):
        """Calculate wait: 30s overhead + 3s per bot"""
        bots_count = len(Config.BOTS_TO_ADD)
        time_per_user = 30 + (bots_count * 3)
        total_seconds = position * time_per_user
        
        if total_seconds < 60:
            return f"{total_seconds}s"
        else:
            return f"{total_seconds // 60}m {total_seconds % 60}s"

    def get_position(self, chat_id):
        """Check if a chat is already in the queue and return its position (1-based)"""
        for index, user in enumerate(self.waiting_users):
            if user["chat_id"] == chat_id:
                return index + 1
        return None

    async def sync_db(self):
        """Sync ACTIVE task + WAITING list to DB for crash recovery"""
        snapshot = []
        
        # 1. Add current task (if any) to front of list
        if self.current_task:
            snapshot.append({
                "chat_id": self.current_task["chat_id"],
                "owner_id": self.current_task["owner_id"],
                "message_id": self.current_task["msg"].id,
                "is_active": True
            })
            
        # 2. Add waiting users
        for user in self.waiting_users:
            snapshot.append({
                "chat_id": user["chat_id"],
                "owner_id": user["owner_id"],
                "message_id": user["msg"].id,
                "is_active": False
            })
            
        await Database.update_queue_state(snapshot)

    async def restore_queue(self):
        """Restore queue from database after restart"""
        saved_queue = await Database.get_queue_state()
        if not saved_queue:
            return

        LOGGER.info(f"♻️ Restoring {len(saved_queue)} tasks from previous session...")
        
        # Import setup_logic here to avoid circular imports
        from bot.modules.setup import setup_logic
        
        for item in saved_queue:
            # Use .get() to safely handle old data that might lack keys
            chat_id = item.get("chat_id")
            message_id = item.get("message_id")
            owner_id = item.get("owner_id")
            
            # Skip corrupted/old entries
            if not chat_id or not message_id:
                LOGGER.warning(f"Skipping invalid queue entry: {item}")
                continue

            # Create a VirtualMessage so setup_logic can call .edit()
            v_msg = VirtualMessage(chat_id, message_id)
            
            data = {
                "msg": v_msg,
                "chat_id": chat_id,
                "owner_id": owner_id,
                "handler": setup_logic
            }
            
            self.waiting_users.append(data)
            await self.queue.put(data)
            
            # Notify user
            try:
                await v_msg.edit("🔄 **Bot Restarted!**\nResuming your task automatically...")
            except:
                pass

    async def add_to_queue(self, message, target_chat, owner_id, handler):
        """Add to queue with immediate DB sync"""
        data = {
            "msg": message,
            "chat_id": target_chat,
            "owner_id": owner_id,
            "handler": handler
        }
        self.waiting_users.append(data)
        
        # Save state immediately
        await self.sync_db()
        
        queue_len = len(self.waiting_users)
        # Check if worker is idle (queue empty but no current task) or busy
        wait_pos = queue_len 
        if self.current_task:
             wait_pos += 1
             
        est_wait = self.calculate_wait(wait_pos - 1)
        
        await message.edit(
            f"⏳ **Added to Queue**\n"
            f"📍 Position: #{queue_len}\n"
            f"⏱️ Est. Wait: ~{est_wait}\n"
            f"Please wait..."
        )
        await self.queue.put(data)
    
    async def update_positions(self):
        """Update messages for waiting users"""
        if not self.waiting_users:
            return
            
        current_users = list(self.waiting_users)
        
        for i, req in enumerate(current_users):
            try:
                # Use getattr to safely handle VirtualMessage vs Pyrogram Message
                # msg_id = getattr(req["msg"], "id", None)
                
                if i == 0:
                    await req["msg"].edit("🔄 **You're Next!**\n⚙️ Starting setup now...")
                else:
                    est_wait = self.calculate_wait(i)
                    await req["msg"].edit(
                        f"⏳ **Queue Position: #{i+1}**\n"
                        f"📊 {i} user(s) ahead of you\n"
                        f"⏱️ Est. Wait: ~{est_wait}"
                    )
                await asyncio.sleep(1.5)
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except Exception:
                pass
    
    async def worker(self):
        """Process queue requests one by one"""
        LOGGER.info("✅ Queue worker started")
        while True:
            # 1. Get next task
            data = await self.queue.get()
            
            # 2. Mark as Active & Sync DB
            self.current_task = data
            if data in self.waiting_users:
                self.waiting_users.remove(data)
            await self.sync_db()
            
            # 3. Update others waiting
            asyncio.create_task(self.update_positions())
            
            msg = data["msg"]
            chat_id = data["chat_id"]
            owner_id = data["owner_id"]
            handler = data["handler"]
            
            try:
                await msg.edit("⚙️ **Processing...**")
                await handler(msg, chat_id, owner_id)
            except Exception as e:
                LOGGER.error(f"Worker error in {chat_id}: {e}")
                try:
                    await msg.edit(f"❌ Error: `{e}`")
                except:
                    pass
            
            # 4. Task Done - Clear Active & Sync DB
            self.current_task = None
            await self.sync_db()
            
            self.queue.task_done()
            LOGGER.info("⏳ Cooling down for 10s before next task...")
            await asyncio.sleep(10)

queue_manager = QueueManager()
