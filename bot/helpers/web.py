import asyncio
from aiohttp import web, ClientSession
from config import Config
from bot.utils.logger import LOGGER

async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="✅ LinkerX is alive")

async def start_web_server():
    """Start web server"""
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    LOGGER.info(f"🌐 Web server started on port {Config.PORT}")

async def ping_server():
    """Self-ping to prevent sleep"""
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(600)
        try:
            async with ClientSession() as session:
                async with session.get(Config.URL, timeout=10) as resp:
                    LOGGER.info(f"🏓 Self-ping: {resp.status}")
        except Exception as e:
            LOGGER.error(f"Ping failed: {e}")
