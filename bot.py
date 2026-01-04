import asyncio
from config import Config
from bot.client import Clients
from bot.helpers.database import Database
from bot.helpers.web import start_web_server, ping_server
from bot.helpers.queue import queue_manager
from bot.utils.logger import LOGGER
from pyrogram import idle

async def main():
    """Main entry point for LinkerX bot"""
    try:
        # Validate configuration
        Config.validate()
        LOGGER.info("✅ Environment variables validated")
        LOGGER.info(f"🛡️ Safety delays: {Config.SYNC_ACTION_DELAY}s between bots, {Config.SYNC_CHANNEL_DELAY}s between channels")
        LOGGER.info(f"📊 Max helper user channels: {Config.MAX_USER_CHANNELS} (spam protection)")
        
        # Initialize Pyrogram clients FIRST
        Clients.initialize()
        LOGGER.info("✅ Clients initialized")
        
        # NOW import modules (decorators will work because Clients.bot exists)
        from bot.modules import start, setup, list, sync, stats
        LOGGER.info("✅ Command modules loaded")
        
        # Initialize database
        await Database.initialize()
        
        # Start web server for health checks
        await start_web_server()
        
        # Start bot client
        await Clients.bot.start()
        LOGGER.info("✅ Bot client started")
        
        # Start user session client
        try:
            await Clients.user_app.start()
            LOGGER.info("✅ User session started")
        except Exception as e:
            LOGGER.critical(f"❌ Failed to start user session: {e}")
            await Clients.bot.stop()
            return
        
        # Cache usernames
        await Clients.get_bot_username()
        await Clients.get_helper_username()
        
        # Start background tasks
        asyncio.create_task(queue_manager.worker())
        asyncio.create_task(ping_server())
        
        LOGGER.info("🚀 LinkerX service ready")
        LOGGER.info(f"📝 Configured with {len(Config.BOTS_TO_ADD)} bots to install")
        
        # Keep the bot running
        await idle()
    
    except KeyboardInterrupt:
        LOGGER.info("⚠️ Interrupted by user")
    except Exception as e:
        LOGGER.critical(f"❌ Fatal error: {e}")
        import traceback
        LOGGER.critical(traceback.format_exc())
    finally:
        LOGGER.info("🛑 Shutting down...")
        
        # Stop bot client
        try:
            if Clients.bot:
                await Clients.bot.stop()
                LOGGER.info("✅ Bot client stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping bot: {e}")
        
        # Stop user client
        try:
            if Clients.user_app:
                await Clients.user_app.stop()
                LOGGER.info("✅ User session stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping user session: {e}")
        
        # Close database
        try:
            Database.close()
        except Exception as e:
            LOGGER.error(f"Error closing database: {e}")
        
        LOGGER.info("✅ Shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())
