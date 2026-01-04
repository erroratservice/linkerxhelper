import asyncio
from bot.utils.logger import LOGGER

class QueueManager:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.waiting_users = []
    
    async def add_to_queue(self, message, target_chat, owner_id, handler):
        """Add setup request to queue"""
        data = {
            "msg": message,
            "chat_id": target_chat,
            "owner_id": owner_id,
            "handler": handler
        }
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
        """Update queue positions for waiting users"""
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
                LOGGER.debug(f"Queue position update failed: {e}")
    
    async def worker(self):
        """Process queue requests one by one"""
        LOGGER.info("✅ Queue worker started")
        while True:
            data = await self.queue.get()
            
            if data in self.waiting_users:
                self.waiting_users.remove(data)
            
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
            await asyncio.sleep(2)

# Global queue manager instance
queue_manager = QueueManager()
