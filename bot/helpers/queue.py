import asyncio
from pyrogram.errors import FloodWait
from bot.utils.logger import LOGGER
from bot.helpers.database import Database
from config import Config

class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.waiting_users = []
    
    def calculate_wait(self, position):
        """Calculate wait: 30s overhead + 3s per bot"""
        bots_count = len(Config.BOTS_TO_ADD)
        time_per_user = 30 + (bots_count * 3)
        total_seconds = position * time_per_user
        
        if total_seconds < 60:
            return f"{total_seconds}s"
        else:
            return f"{total_seconds // 60}m {total_seconds % 60}s"

    def get_snapshot(self):
        """Get snapshot for DB sync"""
        return [
            {"chat_id": user["chat_id"], "owner_id": user["owner_id"]} 
            for user in self.waiting_users
        ]

    def get_position(self, chat_id):
        """Check if a chat is already in the queue and return its position (1-based)"""
        for index, user in enumerate(self.waiting_users):
            if user["chat_id"] == chat_id:
                return index + 1
        return None

    async def sync_db(self):
        """Sync to DB"""
        snapshot = self.get_snapshot()
        await Database.update_queue_state(snapshot)

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
        wait_pos = queue_len - 1
        est_wait = self.calculate_wait(wait_pos)
        
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
        """Process queue"""
        LOGGER.info("✅ Queue worker started")
        while True:
            data = await self.queue.get()
            
            if data in self.waiting_users:
                self.waiting_users.remove(data)
                await self.sync_db()
            
            asyncio.create_task(self.update_positions())
            
            msg = data["msg"]
            chat_id = data["chat_id"]
            owner_id = data["owner_id"]
            handler = data["handler"]
            
            try:
                await msg.edit("⚙️ **Processing started...**")
                await handler(msg, chat_id, owner_id)
            except Exception as e:
                LOGGER.error(f"Worker error in {chat_id}: {e}")
                try:
                    await msg.edit(f"❌ Error: `{e}`")
                except:
                    pass
            
            self.queue.task_done()
            LOGGER.info("⏳ Cooling down for 10s before next task...")
            await asyncio.sleep(10)

queue_manager = QueueManager()
