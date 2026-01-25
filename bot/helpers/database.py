from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from config import Config
from bot.utils.logger import LOGGER

class Database:
    client = None
    db = None
    channels = None
    archive_channels = None
    
    @staticmethod
    async def initialize():
        """Initialize MongoDB connection"""
        Database.client = AsyncIOMotorClient(Config.MONGO_URL)
        Database.db = Database.client["linkerx_db"]
        
        # 1. Main Collection
        Database.channels = Database.db["channels"]
        # 2. Archive Collection
        Database.archive_channels = Database.db["archive_channels"]
        
        # Indexes for Main
        try:
            await Database.channels.create_index("channel_id", unique=True)
            await Database.channels.create_index("owner_id")
            await Database.channels.create_index("user_joined_at")
            await Database.channels.create_index("user_is_member")
            LOGGER.info("✅ Main Database indexes created")
        except Exception as e:
            LOGGER.error(f"❌ Database initialization error: {e}")
            raise

        # Indexes for Archive
        try:
            await Database.archive_channels.create_index("channel_id", unique=True)
            await Database.archive_channels.create_index("owner_id")
            LOGGER.info("✅ Archive Database indexes created")
        except Exception as e:
            LOGGER.error(f"❌ Archive Database index error: {e}")

    # =================================================================
    #  MAIN DATABASE METHODS
    # =================================================================
    
    @staticmethod
    async def is_channel_in_main_db(chat_id):
        """Check if channel exists in main Setup DB (Prevention Check)"""
        try:
            doc = await Database.channels.find_one({"channel_id": chat_id})
            return doc is not None
        except Exception:
            return False

    @staticmethod
    async def get_active_channel_count():
        try:
            return await Database.channels.count_documents({"user_is_member": True})
        except Exception as e:
            LOGGER.error(f"Error counting active channels: {e}")
            return 0
    
    @staticmethod
    async def get_oldest_channel(exclude_ids=None):
        """
        Get oldest active channel, excluding a LIST of IDs.
        Changed from exclude_id (single) to exclude_ids (list).
        """
        query = {"user_is_member": True}
        
        if exclude_ids:
            # Ensure it is a list and not empty
            if isinstance(exclude_ids, list) and len(exclude_ids) > 0:
                query["channel_id"] = {"$nin": exclude_ids}
            elif isinstance(exclude_ids, int):
                # Fallback for legacy calls if any
                query["channel_id"] = {"$ne": exclude_ids}
        
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
        try:
            total_channels = await Database.channels.count_documents({})
            unique_owners = len(await Database.channels.distinct("owner_id"))
            
            pipeline = [
                {"$project": {"bot_count": {"$size": {"$ifNull": ["$installed_bots", []]}}}},
                {"$group": {"_id": None, "total_bots": {"$sum": "$bot_count"}}}
            ]
            result = await Database.channels.aggregate(pipeline).to_list(length=1)
            total_bots = result[0]["total_bots"] if result else 0
            
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

    # =================================================================
    #  ARCHIVE DATABASE METHODS
    # =================================================================

    @staticmethod
    async def save_archive_setup(chat_id, owner_id, installed_bots):
        """Save setup results to the SEPARATE archive collection"""
        await Database.archive_channels.update_one(
            {"channel_id": chat_id},
            {
                "$set": {
                    "channel_id": chat_id,
                    "owner_id": owner_id,
                    "installed_bots": installed_bots,
                    "last_updated": datetime.utcnow(),
                    "helper_finished": True, 
                },
                "$setOnInsert": {
                    "setup_date": datetime.utcnow(),
                },
            },
            upsert=True,
        )

    @staticmethod
    async def get_archive_stats():
        try:
            total_channels = await Database.archive_channels.count_documents({})
            unique_owners = len(await Database.archive_channels.distinct("owner_id"))
            
            pipeline = [
                {"$project": {"bot_count": {"$size": {"$ifNull": ["$installed_bots", []]}}}},
                {"$group": {"_id": None, "total_bots": {"$sum": "$bot_count"}}}
            ]
            result = await Database.archive_channels.aggregate(pipeline).to_list(length=1)
            total_bots = result[0]["total_bots"] if result else 0
            
            return {
                "total_channels": total_channels,
                "unique_owners": unique_owners,
                "total_bots": total_bots,
            }
        except Exception as e:
            LOGGER.error(f"Error getting archive stats: {e}")
            return None

    @staticmethod
    async def get_all_archive_channels():
        try:
            return await Database.archive_channels.find({}).to_list(length=None)
        except Exception as e:
            LOGGER.error(f"Error getting all archive channels: {e}")
            return []

    # =================================================================
    #  SYSTEM STATE
    # =================================================================
    @staticmethod
    async def update_queue_state(queue_data):
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
        try:
            doc = await Database.db["system_state"].find_one({"_id": "queue_state"})
            return doc.get("users", []) if doc else []
        except Exception as e:
            LOGGER.error(f"Failed to get queue state: {e}")
            return []

    @staticmethod
    async def clear_queue_state():
        try:
            await Database.db["system_state"].delete_one({"_id": "queue_state"})
        except Exception as e:
            LOGGER.error(f"Failed to clear queue state: {e}")

    @staticmethod
    async def save_restart_info(chat_id, message_id, status, error=None, queue_data=None):
        try:
            restart_collection = Database.db["restart_info"]
            await restart_collection.delete_many({})
            await restart_collection.insert_one({
                "chat_id": chat_id,
                "message_id": message_id,
                "status": status,
                "error": error,
                "queue_data": queue_data or [],
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
