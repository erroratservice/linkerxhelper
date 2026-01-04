import os
import sys
import asyncio
import re
import subprocess
from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

def sanitize_url(url):
    """Remove tokens from URLs for logging"""
    if not url:
        return url
    return re.sub(r'(https?://)[^@]+@', r'\1***@', url)

def run_git_command(cmd):
    """Run git command safely using subprocess"""
    try:
        # Use subprocess for better output handling and safety
        result = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
        return result.decode().strip()
    except subprocess.CalledProcessError as e:
        return f"Error: {e.output.decode().strip()}"
    except Exception as e:
        return f"Error: {str(e)}"

async def send_restart_notification():
    """Send notification after restart completes (Called by bot.py on startup)"""
    try:
        # Wait for everything to stabilize
        await asyncio.sleep(5)
        
        restart_info = await Database.get_restart_info()
        if not restart_info:
            LOGGER.info("No restart notification to send")
            return
        
        chat_id = restart_info.get("chat_id")
        message_id = restart_info.get("message_id")
        status = restart_info.get("status")
        error = restart_info.get("error")
        
        if not chat_id or not message_id:
            LOGGER.warning("Incomplete restart info, cannot send notification")
            return
        
        # Prepare notification message
        if status == "success":
            text = (
                "✅ **Restart Successful!**\n\n"
                "🚀 LinkerX is back online\n"
                "⏱️ All systems operational"
            )
        elif status == "updated":
            text = (
                "✅ **Restart Successful!**\n\n"
                "📥 Code updated from GitHub\n"
                "🚀 LinkerX is back online\n"
                "⏱️ All systems operational"
            )
        else:
            text = (
                "⚠️ **Restarted with warnings**\n\n"
                "🚀 LinkerX is back online\n"
                f"⚠️ Note: {error or 'Update check had issues'}"
            )
        
        # Send notification
        try:
            await Clients.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text
            )
            LOGGER.info(f"✅ Restart notification sent to {chat_id}")
        except Exception as e:
            LOGGER.error(f"Failed to edit restart message: {e}")
            try:
                await Clients.bot.send_message(chat_id, text)
            except:
                pass
    
    except Exception as e:
        LOGGER.error(f"Failed to send restart notification: {e}")

async def perform_restart(chat_id, message_id, status="success", error=None):
    """Perform the actual restart with safe shutdown"""
    try:
        # Save restart info
        await Database.save_restart_info(chat_id, message_id, status, error)
        
        LOGGER.info("=" * 60)
        LOGGER.info("PERFORMING RESTART")
        LOGGER.info("=" * 60)
        
        # Stop user client
        LOGGER.info("Stopping user client...")
        try:
            if Clients.user_app.is_connected:
                await Clients.user_app.stop()
            LOGGER.info("✅ User client stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping user client: {e}")
        
        # Stop bot client (FIXED: Properly awaited)
        LOGGER.info("Stopping bot client...")
        try:
            if Clients.bot.is_connected:
                await Clients.bot.stop()
            LOGGER.info("✅ Bot client stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping bot client: {e}")
        
        # Close database
        LOGGER.info("Closing database...")
        try:
            Database.close()
        except Exception as e:
            LOGGER.error(f"Error closing database: {e}")
        
        # Restart process
        LOGGER.info("Restarting Python process...")
        LOGGER.info("=" * 60)
        
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable, "-m", "bot"])
        
    except Exception as e:
        LOGGER.critical(f"❌ Failed to restart: {e}")
        import traceback
        LOGGER.critical(traceback.format_exc())
        os._exit(1)

async def check_and_pull_updates():
    """Check for updates and pull if available"""
    try:
        git_version = run_git_command("git --version")
        if "git version" not in git_version.lower():
            LOGGER.warning("Git not available")
            return False, "Git not installed", None
        
        LOGGER.info(f"Git version: {git_version}")
        
        # Setup git if needed
        git_dir = run_git_command("git rev-parse --git-dir")
        if "fatal" in git_dir.lower() or "not a git" in git_dir.lower():
            LOGGER.info("Not a git repo, attempting to initialize...")
            if not Config.GITHUB_REPO or not Config.GITHUB_BRANCH:
                return False, "Repo not configured", None
            
            run_git_command("git init")
            run_git_command(f"git remote add origin {Config.GITHUB_REPO}")
            run_git_command(f"git fetch origin {Config.GITHUB_BRANCH}")
            run_git_command(f"git reset --hard origin/{Config.GITHUB_BRANCH}")
            LOGGER.info("✅ Git repository initialized")
        
        # Safe directory config
        run_git_command("git config --global --add safe.directory /app")
        run_git_command("git config pull.rebase false")
        
        old_commit = run_git_command("git rev-parse --short HEAD")
        LOGGER.info(f"Current commit: {old_commit}")
        
        # Fetch
        safe_url = sanitize_url(Config.GITHUB_REPO)
        LOGGER.info(f"Fetching from: {safe_url}")
        fetch_result = run_git_command(f"git fetch origin {Config.GITHUB_BRANCH}")
        
        if "error" in fetch_result.lower() and "from https" not in fetch_result.lower():
             LOGGER.error(f"Fetch failed: {fetch_result}")
             return False, "Fetch failed", fetch_result
        
        # Check changes
        diff = run_git_command(f"git diff --name-only HEAD origin/{Config.GITHUB_BRANCH}")
        changed_files = [f.strip() for f in diff.split('\n') if f.strip()]
        
        if not changed_files:
            LOGGER.info("Already up to date")
            return False, "Already up to date", None
            
        LOGGER.info(f"Changes in {len(changed_files)} files")
        
        # Pull
        run_git_command("git stash")
        pull_result = run_git_command(f"git pull origin {Config.GITHUB_BRANCH}")
        
        if "error" in pull_result.lower():
            LOGGER.error(f"Pull failed: {pull_result}")
            return False, "Pull failed", pull_result
            
        new_commit = run_git_command("git rev-parse --short HEAD")
        
        # Install requirements
        if "requirements.txt" in changed_files:
            LOGGER.info("Updating requirements...")
            run_git_command("pip install --no-cache-dir -r requirements.txt")
            
        return True, f"📝 Updated {len(changed_files)} files\n🔖 {old_commit} → {new_commit}", None
        
    except Exception as e:
        LOGGER.error(f"Update check failed: {e}")
        return False, "Update check failed", str(e)

@Clients.bot.on_message(filters.command("restart") & filters.user(Config.OWNER_ID))
async def restart_handler(client, message):
    """Restart bot with auto-update (Owner only)"""
    if Config.OWNER_ID == 0:
        return
    
    status = await message.reply_text("🔄 **Restarting LinkerX...**")
    
    try:
        await status.edit("📥 **Checking for updates...**")
        updated, info, error = await check_and_pull_updates()
        
        restart_status = "success"
        if updated:
            await status.edit(f"✅ **Update Successful!**\n\n{info}\n\n🔄 Restarting...")
            restart_status = "updated"
        elif error:
            await status.edit(f"⚠️ **{info}**\n\n🔄 Restarting anyway...")
        else:
            await status.edit(f"ℹ️ **{info}**\n\n🔄 Restarting...")
            
        await asyncio.sleep(2)
        await perform_restart(message.chat.id, status.id, restart_status, error)
        
    except Exception as e:
        LOGGER.error(f"Restart failed: {e}")
        await status.edit(f"❌ **Error:** `{e}`")
