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
        """
        Calculate estimated wait time dynamically.
        Base Overhead: 30s (15s perm wait + 10s queue delay + 5s network/join)
        Per Bot: 3s (2s fixed delay + 1s buffer for retries)
        """
        bots_count = len(Config.BOTS_TO_ADD)
        # Increased base overhead to account for the new 10s sleep between tasks
        time_per_user = 30 + (bots_count * 3)
        
        # Calculate total wait
        total_seconds = position * time_per_user
        
        # Format nice string
        if total_seconds < 60:
            return f"{total_seconds}s"
        else:
            return f"{total_seconds // 60}m {total_seconds % 60}s"

    def get_snapshot(self):
        """Get a list of waiting chat IDs to save before restart"""
        return [
            {"chat_id": user["chat_id"], "owner_id": user["owner_id"]} 
            for user in self.waiting_users
        ]

    async def sync_db(self):
        """Sync current queue state to database for crash recovery"""
        snapshot = self.get_snapshot()
        await Database.update_queue_state(snapshot)

    async def add_to_queue(self, message, target_chat, owner_id, handler):
        """Add setup request to queue"""
        data = {
            "msg": message,
            "chat_id": target_chat,
            "owner_id": owner_id,
            "handler": handler
        }
        self.waiting_users.append(data)
        
        # SAVE TO DB INSTANTLY (Crash Proof)
        await self.sync_db()
        
        # Position is 0-indexed in list
        queue_len = len(self.waiting_users)
        wait_pos = queue_len - 1  # Number of people ahead
        
        est_wait = self.calculate_wait(wait_pos)
        
        await message.edit(
            f"⏳ **Added to Queue**\n"
            f"📍 Position: #{queue_len}\n"
            f"⏱️ Est. Wait: ~{est_wait}\n"
            f"Please wait..."
        )
        await self.queue.put(data)
    
    async def update_positions(self):
        """Update queue positions for waiting users with rate limiting"""
        if not self.waiting_users:
            return
            
        # Create a copy to iterate safely
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
                
                # STAGGER UPDATES: Sleep between edits to prevent floodwait
                await asyncio.sleep(1.5)
                
            except FloodWait as e:
                LOGGER.warning(f"FloodWait during queue update: {e.value}s")
                await asyncio.sleep(e.value + 1)
            except Exception as e:
                LOGGER.debug(f"Queue position update failed for one user: {e}")
    
    async def worker(self):
        """Process queue requests one by one"""
        LOGGER.info("✅ Queue worker started")
        while True:
            data = await self.queue.get()
            
            # Remove from waiting list before processing
            if data in self.waiting_users:
                self.waiting_users.remove(data)
                # SYNC REMOVAL TO DB
                await self.sync_db()
            
            # Update positions for remaining users in background
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
                    await msg.edit(f"❌ Error during processing:\n`{e}`")
                except:
                    pass
            
            self.queue.task_done()
            
            # INCREASED DELAY: Sleep 10s between different channels
            LOGGER.info("⏳ Cooling down for 10s before next task...")
            await asyncio.sleep(10)

# Global queue manager instance
queue_manager = QueueManager()
