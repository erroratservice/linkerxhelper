# --- ADD THIS TO CONFIGURATION ---
# Delay (in seconds) between processing each channel to prevent FloodWait
SYNC_CHANNEL_DELAY = int(os.environ.get("SYNC_CHANNEL_DELAY", 10)) 
# Delay (in seconds) between adding/promoting each bot within a channel
SYNC_ACTION_DELAY = 2 

# --- UPDATED HELPER: ADD/REMOVE BOTS ---
async def manage_bots(chat_id, action, bot_usernames):
    if not bot_usernames:
        return []

    success_list = []
    
    privileges = ChatPrivileges(
        can_manage_chat=True,
        can_delete_messages=True,
        can_restrict_members=True,
        can_promote_members=False,
        can_invite_users=True,
        can_pin_messages=True
    )

    for username in bot_usernames:
        try:
            if action == 'add':
                try: 
                    await user_app.add_chat_members(chat_id, username)
                except Exception: 
                    pass
                
                # Small safety sleep before promotion
                await asyncio.sleep(0.5) 
                
                await user_app.promote_chat_member(chat_id, username, privileges=privileges)
                success_list.append(username)
                
            elif action == 'remove':
                await user_app.promote_chat_member(
                    chat_id, username, 
                    privileges=ChatPrivileges(can_manage_chat=False)
                )
                await user_app.ban_chat_member(chat_id, username)
                await user_app.unban_chat_member(chat_id, username)
                success_list.append(username)

            # --- SAFETY SLEEP 1: Between actions in the same channel ---
            # Prevents hitting limits when adding 5+ bots at once
            await asyncio.sleep(SYNC_ACTION_DELAY)

        except Exception as e:
            logger.error(f"Error {action}ing {username} in {chat_id}: {e}")
    
    return success_list

# --- UPDATED SYNC COMMAND ---
@bot.on_message(filters.command("sync") & filters.user(OWNER_ID))
async def sync_all_channels(client, message):
    status_msg = await message.reply_text("🔄 **Starting Global Sync...**\nThis may take a while to prevent flood limits.")
    
    wanted_bots = set(BOTS_TO_ADD)
    processed = 0
    errors = 0
    total_channels = await channels_col.count_documents({})
    
    current_index = 0

    # PyMongo Async Cursor
    async for doc in channels_col.find():
        current_index += 1
        chat_id = doc['channel_id']
        owner_id = doc['owner_id']
        current_bots = set(doc.get('installed_bots', []))
        
        to_add = list(wanted_bots - current_bots)
        to_remove = list(current_bots - wanted_bots)
        
        # If nothing to do, skip without sleeping
        if not to_add and not to_remove:
            continue

        # Update Status every 5 channels so you know it's alive
        if processed % 5 == 0:
            await status_msg.edit(f"🔄 **Syncing...**\nProgress: {current_index}/{total_channels}\nProcessed: {processed}")

        try:
            added = await manage_bots(chat_id, 'add', to_add)
            removed = await manage_bots(chat_id, 'remove', to_remove)
            
            new_state = list((current_bots - set(removed)) | set(added))
            
            await channels_col.update_one(
                {"channel_id": chat_id},
                {"$set": {"installed_bots": new_state}}
            )
            processed += 1

            # --- SAFETY SLEEP 2: Between Channels ---
            # This is crucial. It gives the Telegram API a "breather" 
            # so it doesn't flag your user account as a spam bot.
            logger.info(f"Sleeping {SYNC_CHANNEL_DELAY}s before next channel...")
            await asyncio.sleep(SYNC_CHANNEL_DELAY)

        except Exception as e:
            errors += 1
            logger.error(f"Sync failed for {chat_id}: {e}")
            
            # Notify owner logic...
            try:
                await bot.send_message(
                    owner_id,
                    f"⚠️ **LinkerX Alert**: Sync failed for channel `{chat_id}`. Check permissions."
                )
            except Exception:
                pass 

    await status_msg.edit(f"✅ **Sync Finished**\nChannels Updated: {processed}\nErrors: {errors}")
