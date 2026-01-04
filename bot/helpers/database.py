from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from config import Config
from bot.utils.logger import LOGGER

class Database:
    client = None
    db = None
    channels = None
    
    @staticmethod
    async def initialize():
        """Initialize MongoDB connection"""
        Database.client = AsyncIOMotorClient(Config.MONGO_URL)
        Database.db = Database.client["linkerx_db"]
        Database.channels = Database.db["channels"]
        
        # Create indexes
        try:
            await Database.channels.create_index("channel_id", unique=True)
            await Database.channels.create_index("owner_id")
            await Database.channels.create_index("user_joined_at")
            await Database.channels.create_index("user_is_member")
            LOGGER.info("✅ Database indexes created")
        except Exception as e:
            LOGGER.error(f"❌ Database initialization error: {e}")
            raise
    
    @staticmethod
    async def get_active_channel_count():
        try:
            return await Database.channels.count_documents({"user_is_member": True})
        except Exception as e:
            LOGGER.error(f"Error counting active channels: {e}")
            return 0
    
    @staticmethod
    async def get_oldest_channel(exclude_id=None):
        query = {"user_is_member": True}
        if exclude_id:
            query["channel_id"] = {"$ne": exclude_id}
        
        cursor = Database.channels.find(query).sort("user_joined_at", 1).limit(1)
        result = await cursor.to_list(length=1)
        return result[0] if result else None
    
    @staticmethod
    async def get_all_channels():
        try:
            return await Database.channels.find({}).to_list(length=None)
        except Exception as e:
            LOGGER.error(f"Error getting all channels: {e}")
            return []
    
    @staticmethod
    async def get_user_channels(owner_id):
        try:
            cursor = Database.channels.find({"owner_id": owner_id})
            return await cursor.to_list(length=100)
        except Exception as e:
            LOGGER.error(f"Error getting user channels: {e}")
            return []
    
    @staticmethod
    async def update_channel_membership(chat_id, is_member, joined_at=None):
        update_data = {"user_is_member": is_member}
        if joined_at:
            update_data["user_joined_at"] = joined_at
        if not is_member:
            update_data["user_left_at"] = datetime.utcnow()
        
        await Database.channels.update_one(
            {"channel_id": chat_id},
            {"$set": update_data},
            upsert=True
        )
    
    @staticmethod
    async def save_setup(chat_id, owner_id, installed_bots):
        await Database.channels.update_one(
            {"channel_id": chat_id},
            {
                "$set": {
                    "channel_id": chat_id,
                    "owner_id": owner_id,
                    "installed_bots": installed_bots,
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
    
    @staticmethod
    async def update_channel_bots(chat_id, installed_bots):
        await Database.channels.update_one(
            {"channel_id": chat_id},
            {"$set": {
                "installed_bots": installed_bots,
                "last_updated": datetime.utcnow()
            }}
        )
    
    @staticmethod
    async def get_total_stats():
        """Get global statistics with crash prevention"""
        try:
            total_channels = await Database.channels.count_documents({})
            unique_owners = len(await Database.channels.distinct("owner_id"))
            
            # Use $ifNull to prevent crashes on incomplete setups
            pipeline = [
                {"$project": {"bot_count": {"$size": {"$ifNull": ["$installed_bots", []]}}}},
                {"$group": {"_id": None, "total_bots": {"$sum": "$bot_count"}}}
            ]
            result = await Database.channels.aggregate(pipeline).to_list(length=1)
            total_bots = result[0]["total_bots"] if result else 0
            
            # Oldest membership
            cursor = Database.channels.find({"user_is_member": True}).sort("user_joined_at", 1).limit(1)
            oldest_list = await cursor.to_list(length=1)
            oldest_info = "N/A"
            if oldest_list:
                join_date = oldest_list[0].get("user_joined_at")
                if join_date:
                    days = (datetime.utcnow() - join_date).days
                    oldest_info = f"{days} days ago"
            
            return {
                "total_channels": total_channels,
                "unique_owners": unique_owners,
                "total_bots": total_bots,
                "oldest_membership": oldest_info
            }
        except Exception as e:
            LOGGER.error(f"Error getting stats: {e}")
            return None
            
    # --- QUEUE & RESTART STATE ---
    
    @staticmethod
    async def update_queue_state(queue_data):
        """Save the current queue list to DB (Crash Proofing)"""
        try:
            await Database.db["system_state"].update_one(
                {"_id": "queue_state"},
                {"$set": {"users": queue_data, "updated_at": datetime.utcnow()}},
                upsert=True
            )
        except Exception as e:
            LOGGER.error(f"Failed to sync queue state: {e}")

    @staticmethod
    async def get_queue_state():
        """Get the queue list saved before a crash"""
        try:
            doc = await Database.db["system_state"].find_one({"_id": "queue_state"})
            return doc.get("users", []) if doc else []
        except Exception as e:
            LOGGER.error(f"Failed to get queue state: {e}")
            return []

    @staticmethod
    async def clear_queue_state():
        """Clear queue state after notifying users"""
        try:
            await Database.db["system_state"].delete_one({"_id": "queue_state"})
        except Exception as e:
            LOGGER.error(f"Failed to clear queue state: {e}")

    @staticmethod
    async def save_restart_info(chat_id, message_id, status, error=None, queue_data=None):
        """Save restart info including pending queue data"""
        try:
            restart_collection = Database.db["restart_info"]
            await restart_collection.delete_many({})
            await restart_collection.insert_one({
                "chat_id": chat_id,
                "message_id": message_id,
                "status": status,
                "error": error,
                "queue_data": queue_data or [],  # This handles the passed list
                "timestamp": datetime.utcnow()
            })
            LOGGER.info(f"✅ Restart info saved: status={status}, chat={chat_id}, msg={message_id}")
        except Exception as e:
            LOGGER.error(f"❌ Failed to save restart info: {e}")
    
    @staticmethod
    async def get_restart_info():
        try:
            restart_collection = Database.db["restart_info"]
            info = await restart_collection.find_one()
            if info:
                await restart_collection.delete_one({"_id": info["_id"]})
                LOGGER.info(f"✅ Retrieved restart info: status={info.get('status')}")
                return info
            return None
        except Exception as e:
            LOGGER.error(f"❌ Failed to get restart info: {e}")
            return None
    
    @staticmethod
    def close():
        if Database.client:
            Database.client.close()
            LOGGER.info("✅ Database connection closed")
