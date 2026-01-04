import asyncio
from config import Config
from bot.client import Clients
from bot.helpers.database import Database
from bot.helpers.web import start_web_server, ping_server
from bot.helpers.queue import queue_manager
from bot.utils.logger import LOGGER
from pyrogram import idle

# Import all modules
from bot.modules import *

async def main():
    try:
        # Validate config
        Config.validate()
        LOGGER.info(f"🛡️ Safety: {Config.SYNC_ACTION_DELAY}s between bots, {Config.SYNC_CHANNEL_DELAY}s between channels")
        LOGGER.info(f"📊 Max helper channels: {Config.MAX_USER_CHANNELS}")
        
        # Initialize clients
        Clients.initialize()
        
        # Initialize database
        await Database.initialize()
        
        # Start web server
        await start_web_server()
        
        # Start bot client
        await Clients.bot.start()
        LOGGER.info("✅ Bot client started")
        
        # Start user client
        try:
            await Clients.user_app.start()
            LOGGER.info("✅ User session started")
        except Exception as e:
            LOGGER.critical(f"Failed to start user session: {e}")
            await Clients.bot.stop()
            return
        
        # Cache usernames
        await Clients.get_bot_username()
        await Clients.get_helper_username()
        
        # Start background tasks
        asyncio.create_task(queue_manager.worker())
        asyncio.create_task(ping_server())
        
        LOGGER.info("🚀 LinkerX service ready")
        await idle()
    
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
    except Exception as e:
        LOGGER.critical(f"Fatal error: {e}")
    finally:
        LOGGER.info("Shutting down...")
        try:
            if Clients.bot:
                await Clients.bot.stop()
        except:
            pass
        try:
            if Clients.user_app:
                await Clients.user_app.stop()
        except:
            pass
        try:
            Database.close()
        except:
            pass
        LOGGER.info("✅ Shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())
