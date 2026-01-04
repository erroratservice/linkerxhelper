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
            f"⏱️ Estimated wait: ~{(pos - 1) * 30}s"
        )
        await self.queue.put(data)
    
    async def update_positions(self):
        """Update queue positions"""
        if not self.waiting_users:
            return
        for i, req in enumerate(self.waiting_users):
            try:
                if i == 0:
                    await req["msg"].edit("🔄 **You're Next!**\n⚙️ Starting setup...")
                else:
                    await req["msg"].edit(
                        f"⏳ **Queue Position: #{i+1}**\n"
                        f"⏱️ Estimated wait: ~{i*30}s"
                    )
            except Exception:
                pass
    
    async def worker(self):
        """Process queue"""
        LOGGER.info("Queue worker started")
        while True:
            data = await self.queue.get()
            if data in self.waiting_users:
                self.waiting_users.remove(data)
            asyncio.create_task(self.update_positions())
            
            try:
                await data["msg"].edit("⚙️ **Processing...**")
                await data["handler"](data["msg"], data["chat_id"], data["owner_id"])
            except Exception as e:
                LOGGER.error(f"Worker error: {e}")
                try:
                    await data["msg"].edit(f"❌ Error: `{e}`")
                except:
                    pass
            
            self.queue.task_done()
            await asyncio.sleep(2)

# Global instance
queue_manager = QueueManager()
