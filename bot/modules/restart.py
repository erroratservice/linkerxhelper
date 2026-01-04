import os
import sys
import asyncio
from pyrogram import filters
from bot.client import Clients
from bot.helpers.database import Database
from config import Config
from bot.utils.logger import LOGGER

def run_git_command(cmd):
    """Run git command safely"""
    try:
        result = os.popen(f"{cmd} 2>&1").read()
        return result
    except Exception as e:
        return f"Error: {str(e)}"

async def send_restart_notification():
    """Send notification after restart completes"""
    try:
        # Wait a bit for everything to stabilize
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
                f"⚠️ Note: {error or 'Unknown issue'}"
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
            # Try sending new message if edit fails
            try:
                await Clients.bot.send_message(chat_id, text)
            except:
                pass
    
    except Exception as e:
        LOGGER.error(f"Failed to send restart notification: {e}")

async def perform_restart(chat_id, message_id, status="success", error=None):
    """Perform the actual restart"""
    try:
        # Save restart info for post-restart notification
        await Database.save_restart_info(chat_id, message_id, status, error)
        
        LOGGER.info("=" * 60)
        LOGGER.info("PERFORMING RESTART")
        LOGGER.info("=" * 60)
        
        # Stop clients gracefully
        LOGGER.info("Stopping user client...")
        try:
            await Clients.user_app.stop()
            LOGGER.info("✅ User client stopped")
        except Exception as e:
            LOGGER.error(f"Error stopping user client: {e}")
        
        LOGGER.info("Stopping bot client...")
        try:
            # Don't await bot.stop() inside a handler - causes the error you saw
            Clients.bot.stop()
            LOGGER.info("✅ Bot client stop initiated")
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
        
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "bot.py"])
        
    except Exception as e:
        LOGGER.critical(f"❌ Failed to restart: {e}")
        import traceback
        LOGGER.critical(traceback.format_exc())
        os._exit(1)

async def check_and_pull_updates():
    """Check for updates and pull if available"""
    try:
        # Verify git is available
        git_version = run_git_command("git --version")
        if "git version" not in git_version.lower():
            LOGGER.warning("Git not available")
            return False, "Git not installed", None
        
        LOGGER.info(f"Git version: {git_version.strip()}")
        
        # Check if it's a git repo
        git_dir = run_git_command("git rev-parse --git-dir")
        if "fatal" in git_dir.lower() or "not a git" in git_dir.lower():
            # Try to initialize git repo
            LOGGER.info("Not a git repo, attempting to initialize...")
            
            if not Config.GITHUB_REPO or not Config.GITHUB_BRANCH:
                return False, "Not a git repo and GITHUB_REPO not configured", None
            
            # Initialize git
            run_git_command("git init")
            run_git_command(f"git remote add origin {Config.GITHUB_REPO}")
            run_git_command(f"git fetch origin {Config.GITHUB_BRANCH}")
            run_git_command(f"git reset --hard origin/{Config.GITHUB_BRANCH}")
            
            LOGGER.info("✅ Git repository initialized")
        
        # Configure git
        run_git_command("git config --global --add safe.directory /app")
        run_git_command("git config pull.rebase false")
        
        # Get current commit
        old_commit = run_git_command("git rev-parse --short HEAD").strip()
        LOGGER.info(f"Current commit: {old_commit}")
        
        # Fetch from remote
        LOGGER.info(f"Fetching from: {Config.GITHUB_REPO} (branch: {Config.GITHUB_BRANCH})")
        fetch_result = run_git_command(f"git fetch origin {Config.GITHUB_BRANCH}")
        
        if "error" in fetch_result.lower() or "fatal" in fetch_result.lower():
            LOGGER.error(f"Fetch failed: {fetch_result}")
            return False, f"Fetch failed", fetch_result[:100]
        
        # Check for changes
        diff_output = run_git_command(f"git diff --name-only HEAD origin/{Config.GITHUB_BRANCH}")
        changed_files = [f.strip() for f in diff_output.strip().split('\n') if f.strip()]
        
        if not changed_files:
            LOGGER.info("Already up to date")
            return False, "Already up to date", None
        
        LOGGER.info(f"Changes detected in {len(changed_files)} files")
        
        # Stash local changes
        run_git_command("git stash")
        
        # Pull updates
        LOGGER.info("Pulling updates...")
        pull_result = run_git_command(f"git pull origin {Config.GITHUB_BRANCH}")
        
        if "error" in pull_result.lower() or "fatal" in pull_result.lower():
            LOGGER.error(f"Pull failed: {pull_result}")
            return False, "Pull failed", pull_result[:100]
        
        # Get new commit
        new_commit = run_git_command("git rev-parse --short HEAD").strip()
        LOGGER.info(f"New commit: {new_commit}")
        
        # Update requirements if changed
        if "requirements.txt" in changed_files:
            LOGGER.info("Updating requirements...")
            run_git_command("pip install --no-cache-dir -r requirements.txt")
        
        update_info = f"📝 Updated {len(changed_files)} files\n🔖 {old_commit} → {new_commit}"
        return True, update_info, None
        
    except Exception as e:
        LOGGER.error(f"Update check failed: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        return False, "Update check failed", str(e)

@Clients.bot.on_message(filters.command("restart") & filters.user(Config.OWNER_ID))
async def restart_handler(client, message):
    """Restart bot with auto-update (Owner only)"""
    if Config.OWNER_ID == 0:
        await message.reply_text("❌ This command is disabled (OWNER_ID not set)")
        return
    
    status = await message.reply_text("🔄 **Restarting LinkerX...**")
    
    try:
        LOGGER.info("=" * 60)
        LOGGER.info("RESTART COMMAND RECEIVED")
        LOGGER.info(f"Requested by: {message.from_user.id}")
        LOGGER.info("=" * 60)
        
        # Try to update code first
        await status.edit("📥 **Checking for updates...**")
        
        updated, info, error = await check_and_pull_updates()
        
        restart_status = "success"
        
        if updated:
            LOGGER.info(f"✅ Code updated: {info}")
            await status.edit(
                f"✅ **Update Successful!**\n\n"
                f"{info}\n\n"
                f"🔄 Restarting with new code...\n"
                f"⏳ Bot will be back in ~15 seconds"
            )
            restart_status = "updated"
        else:
            if error:
                LOGGER.warning(f"⚠️ Update issue: {info} - {error}")
                await status.edit(
                    f"⚠️ **{info}**\n\n"
                    f"🔄 Restarting anyway...\n"
                    f"⏳ Bot will be back in ~15 seconds"
                )
                restart_status = "warning"
            else:
                LOGGER.info(f"ℹ️ {info}")
                await status.edit(
                    f"ℹ️ **{info}**\n\n"
                    f"🔄 Restarting...\n"
                    f"⏳ Bot will be back in ~15 seconds"
                )
        
        await asyncio.sleep(2)
        
        # Perform restart with notification info
        await perform_restart(
            chat_id=message.chat.id,
            message_id=status.id,
            status=restart_status,
            error=error
        )
        
    except Exception as e:
        LOGGER.error(f"❌ Restart command failed: {e}")
        import traceback
        LOGGER.error(traceback.format_exc())
        try:
            await status.edit(
                f"❌ **Restart Failed**\n\n"
                f"Error: `{str(e)[:150]}`\n\n"
                f"Check logs for details."
            )
        except:
            pass
