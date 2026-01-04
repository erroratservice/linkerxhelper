@staticmethod
async def save_restart_info(chat_id, message_id, status, error=None):
    """Save restart information for post-restart notification"""
    try:
        await Database.db["restart_info"].delete_many({})  # Clear old entries
        await Database.db["restart_info"].insert_one({
            "chat_id": chat_id,
            "message_id": message_id,
            "status": status,
            "error": error,
            "timestamp": datetime.utcnow()
        })
    except Exception as e:
        LOGGER.error(f"Failed to save restart info: {e}")

@staticmethod
async def get_restart_info():
    """Get restart information"""
    try:
        info = await Database.db["restart_info"].find_one()
        if info:
            await Database.db["restart_info"].delete_one({"_id": info["_id"]})
        return info
    except Exception as e:
        LOGGER.error(f"Failed to get restart info: {e}")
        return None
