import os
import sys
import asyncio
import subprocess
from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

def run_command(cmd):
    """Run shell command and return output"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

@Clients.bot.on_message(filters.command("restart") & filters.user(Config.OWNER_ID))
async def restart_handler(client, message):
    """Update from GitHub and restart bot (Owner only)"""
    if Config.OWNER_ID == 0:
        await message.reply_text("❌ This command is disabled (OWNER_ID not set)")
        return
    
    status = await message.reply_text("🔄 **Restarting LinkerX...**")
    
    try:
        LOGGER.info("=" * 60)
        LOGGER.info("RESTART COMMAND RECEIVED")
        LOGGER.info("=" * 60)
        
        # Step 1: Check if git is available
        code, stdout, stderr = run_command("git --version")
        if code != 0:
            LOGGER.warning("Git not available, performing simple restart")
            await status.edit(
                "⚠️ **Git not available**\n\n"
                "Performing restart without code update...\n"
                "⏳ Back online in ~10 seconds"
            )
            await asyncio.sleep(2)
            await perform_restart()
            return
        
        LOGGER.info(f"Git version: {stdout.strip()}")
        
        # Step 2: Check if GITHUB_REPO is configured
        if not Config.GITHUB_REPO:
            LOGGER.warning("GITHUB_REPO not configured, performing simple restart")
            await status.edit(
                "⚠️ **GitHub repo not configured**\n\n"
                "Performing restart without code update...\n"
                "⏳ Back online in ~10 seconds"
            )
            await asyncio.sleep(2)
            await perform_restart()
            return
        
        # Step 3: Check if we're in a git repository
        code, stdout, stderr = run_command("git rev-parse --git-dir")
        if code != 0:
            LOGGER.warning("Not a git repository, performing simple restart")
            await status.edit(
                "⚠️ **Not a git repository**\n\n"
                "Performing restart without code update...\n"
                "⏳ Back online in ~10 seconds"
            )
            await asyncio.sleep(2)
            await perform_restart()
            return
        
        # Step 4: Get current commit hash
        code, old_commit, _ = run_command("git rev-parse --short HEAD")
        old_commit = old_commit.strip() if code == 0 else "unknown"
        LOGGER.info(f"Current commit: {old_commit}")
        
        await status.edit("📥 **Fetching latest code...**")
        
        # Step 5: Fetch from remote
        LOGGER.info(f"Fetching from {Config.GITHUB_REPO} (branch: {Config.GITHUB_BRANCH})")
        code, stdout, stderr = run_command(f"git fetch origin {Config.GITHUB_BRANCH}")
        if code != 0:
            LOGGER.error(f"Git fetch failed: {stderr}")
            await status.edit(
                f"❌ **Fetch failed**\n\n"
                f"Error: `{stderr[:200]}`\n\n"
                f"Performing restart without update..."
            )
            await asyncio.sleep(3)
            await perform_restart()
            return
        
        # Step 6: Check if updates are available
        code, stdout, _ = run_command(f"git diff --name-only HEAD origin/{Config.GITHUB_BRANCH}")
        changed_files = stdout.strip().split('\n') if stdout.strip() else []
        
        if not changed_files or (len(changed_files) == 1 and not changed_files[0]):
            LOGGER.info("No updates available")
            await status.edit(
                "✅ **Already up to date!**\n\n"
                "🔄 Restarting anyway...\n"
                "⏳ Back online in ~10 seconds"
            )
            await asyncio.sleep(2)
            await perform_restart()
            return
        
        LOGGER.info(f"Files to update: {len(changed_files)}")
        await status.edit(
            f"🔄 **Pulling updates...**\n\n"
            f"📝 Files changed: {len(changed_files)}"
        )
        
        # Step 7: Stash local changes (if any)
        LOGGER.info("Stashing local changes...")
        run_command("git stash")
        
        # Step 8: Pull latest code
        code, stdout, stderr = run_command(f"git pull origin {Config.GITHUB_BRANCH}")
        if code != 0:
            LOGGER.error(f"Git pull failed: {stderr}")
            await status.edit(
                f"❌ **Pull failed**\n\n"
                f"Error: `{stderr[:200]}`\n\n"
                f"Restarting with current code..."
            )
            await asyncio.sleep(3)
            await perform_restart()
            return
        
        LOGGER.info("Pull successful")
        
        # Step 9: Get new commit hash
        code, new_commit, _ = run_command("git rev-parse --short HEAD")
        new_commit = new_commit.strip() if code == 0 else "unknown"
        LOGGER.info(f"New commit: {new_commit}")
        
        # Step 10: Check if requirements need update
        if "requirements.txt" in changed_files:
            await status.edit(
                "📦 **Installing dependencies...**\n\n"
                f"⏳ This may take a minute..."
            )
            LOGGER.info("Installing updated requirements...")
            code, stdout, stderr = run_command("pip3 install -r requirements.txt")
            if code != 0:
                LOGGER.warning(f"Requirements install had warnings: {stderr}")
        
        # Step 11: Restart with new code
        await status.edit(
            f"✅ **Update successful!**\n\n"
            f"📝 Updated: {len(changed_files)} files\n"
            f"🔖 Old: `{old_commit}`\n"
            f"🔖 New: `{new_commit}`\n\n"
            f"🔄 Restarting...\n"
            f"⏳ Back online in ~10 seconds"
        )
        
        await asyncio.sleep(2)
        await perform_restart()
        
    except Exception as e:
        LOGGER.error(f"Restart failed: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        try:
            await status.edit(f"❌ **Restart failed:**\n`{str(e)[:200]}`")
        except:
            pass

async def perform_restart():
    """Perform the actual restart"""
    try:
        LOGGER.info("Shutting down clients...")
        
        # Stop clients gracefully
        try:
            await Clients.user_app.stop()
            LOGGER.info("✅ User client stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping user client: {e}")
        
        try:
            await Clients.bot.stop()
            LOGGER.info("✅ Bot client stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping bot client: {e}")
        
        # Close database
        try:
            Database.close()
        except Exception as e:
            LOGGER.error(f"Error closing database: {e}")
        
        LOGGER.info("Restarting process...")
        os.execl(sys.executable, sys.executable, "bot.py")
        
    except Exception as e:
        LOGGER.critical(f"Failed to restart: {e}")
        # Force exit if normal restart fails
        os._exit(1)
