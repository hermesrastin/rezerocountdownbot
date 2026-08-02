#!/usr/bin/env python3
"""Re:Zero Countdown Telegram Bot - Independent bot for Railway deployment."""
import os
import json
import asyncio
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Set, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ========== CONFIG ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_DATE = datetime(2026, 8, 12, 17, 0, 0, tzinfo=timezone.utc)

# Data files
GROUPS_FILE = "data/groups.json"
USERS_FILE = "data/users.json"
STICKER_PACKS = [
    "rasrez_by_fStikBot",
    "b6a3b0ad87f4e5_by_anipackbot",
    "RezeroByLimbo",
    "Emiliatan_by_TgEmojis_bot",
    "Echidna_Rezero_Otakuzdream",
]

# ========== DATA MANAGEMENT ==========
def load_json(filepath: str, default):
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            return json.load(f)
    return default

def save_json(filepath: str, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(data, f)

groups: Set[int] = set(load_json(GROUPS_FILE, []))
dm_users: Set[int] = set(load_json(USERS_FILE, []))

# Track last sent sticker per chat to avoid repeats
last_sticker: Dict[int, str] = defaultdict(str)
# Cached stickers from all packs
all_stickers: List[str] = []
stickers_loaded = False

# ========== COUNTDOWN MESSAGE ==========
def get_countdown_message() -> str:
    now = datetime.now(timezone.utc)
    diff = TARGET_DATE - now
    total_seconds = int(diff.total_seconds())
    
    if total_seconds <= 0:
        return "🎉 Re:Zero Season 4 · Episode 12 is OUT NOW!"
    
    days = diff.days
    hours = total_seconds // 3600
    
    return f"""╔══════════════════════╗
  ◈ Re:Zero · Season 4
  ◈ Episode 12 Incoming
╚══════════════════════╝

  {days} days · {hours} hours

  until the next episode drops

╚══════════════════════╝"""

# ========== STICKER HANDLING ==========
async def load_all_stickers(bot):
    """Load stickers from all packs into one big pool."""
    global all_stickers, stickers_loaded
    all_stickers = []
    for pack_name in STICKER_PACKS:
        try:
            sticker_set = await bot.get_sticker_set(pack_name)
            for s in sticker_set.stickers:
                all_stickers.append(s.file_id)
        except Exception as e:
            print(f"Failed to load sticker pack {pack_name}: {e}")
    stickers_loaded = True
    print(f"Loaded {len(all_stickers)} stickers from {len(STICKER_PACKS)} packs")

async def send_random_sticker(bot, chat_id: int) -> bool:
    global all_stickers, stickers_loaded
    try:
        if not stickers_loaded or not all_stickers:
            await load_all_stickers(bot)
        if not all_stickers:
            return False

        # Pick a sticker different from the last one sent to this chat
        last = last_sticker[chat_id]
        candidates = [s for s in all_stickers if s != last] if last else all_stickers
        if not candidates:
            candidates = all_stickers
        chosen = random.choice(candidates)
        last_sticker[chat_id] = chosen

        await bot.send_sticker(chat_id=chat_id, sticker=chosen)
        return True
    except Exception as e:
        print(f"Sticker error for {chat_id}: {e}")
        # Reset cache on error
        stickers_loaded = False
        return False

# ========== SCHEDULED TASKS ==========
async def send_countdown_to_groups(bot):
    """Send countdown to all groups every 3 hours."""
    global groups
    msg = get_countdown_message()
    active_groups = []
    
    for gid in groups:
        try:
            await bot.send_message(chat_id=gid, text=msg)
            await send_random_sticker(bot, gid)
            active_groups.append(gid)
        except Exception as e:
            print(f"Group {gid} error: {e}")
            # Bot might have been removed
    
    # Save only active groups
    if set(active_groups) != groups:
        groups = set(active_groups)
        save_json(GROUPS_FILE, list(groups))

async def send_countdown_to_users(bot):
    """Send countdown to DM users who enabled it."""
    global dm_users
    msg = get_countdown_message()
    active_users = []
    
    for uid in dm_users:
        try:
            await bot.send_message(chat_id=uid, text=msg)
            await send_random_sticker(bot, uid)
            active_users.append(uid)
        except Exception as e:
            print(f"User {uid} error: {e}")
    
    # Save only active users
    if set(active_users) != dm_users:
        dm_users = set(active_users)
        save_json(USERS_FILE, list(dm_users))

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        # Private chat - show options
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("🔔 Enable DM Countdown (/dmcountdown)", callback_data="enable_dm")]
        ])
        
        await update.message.reply_text(
            f"Hey {user.first_name}! 👋\n\n"
            "I'm the **Re:Zero Countdown Bot** — I track Episode 12 of Season 4.\n\n"
            "**What I can do:**\n"
            "• **In groups:** Send countdown every 3 hours + random stickers\n"
            "• **In DM:** Send countdown to you personally (use /dmcountdown)\n\n"
            "Choose an option below:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    else:
        # Group chat - just acknowledge
        await update.message.reply_text(
            "Re:Zero Countdown Bot active! 📅\n"
            "I'll send Episode 12 countdown every 3 hours."
        )

async def dm_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type != "private":
        await update.message.reply_text("This command only works in private chat!")
        return
    
    if user.id in dm_users:
        dm_users.discard(user.id)
        save_json(USERS_FILE, list(dm_users))
        await update.message.reply_text("❌ DM countdown **disabled**. You won't receive updates.")
    else:
        dm_users.add(user.id)
        save_json(USERS_FILE, list(dm_users))
        await update.message.reply_text(
            "✅ DM countdown **enabled**!\n"
            "You'll receive Episode 12 countdown every 3 hours.\n\n"
            "Use /dmcountdown again to disable."
        )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = get_countdown_message()
    
    if chat.type == "private":
        status_text = "✅ Enabled" if chat.id in dm_users else "❌ Disabled"
        msg += f"\n\n**Your DM Status:** {status_text}\nUse /dmcountdown to toggle."
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when bot is added/removed from groups."""
    result = update.my_chat_member
    chat = result.chat
    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status
    
    # Bot was added to group
    if new_status in ("member", "administrator") and old_status in ("left", "kicked"):
        if chat.type in ("group", "supergroup"):
            groups.add(chat.id)
            save_json(GROUPS_FILE, list(groups))
            print(f"Added to group: {chat.title} ({chat.id})")
            
            # Send welcome message
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text="Thanks for adding me! 🎉\nI'll send Re:Zero Episode 12 countdown every 3 hours."
                )
            except:
                pass
    
    # Bot was removed from group
    elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
        if chat.type in ("group", "supergroup"):
            groups.discard(chat.id)
            save_json(GROUPS_FILE, list(groups))
            print(f"Removed from group: {chat.title} ({chat.id})")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "**Re:Zero Countdown Bot Commands**\n\n"
        "/start — Show welcome message\n"
        "/countdown — Show current countdown\n"
        "/dmcountdown — Toggle DM notifications (private only)\n"
        "/help — This message\n\n"
        "**Group Features:**\n"
        "• Add bot to group → auto countdown every 3h\n"
        "• Random Re:Zero stickers included\n\n"
        "**DM Features:**\n"
        "• /dmcountdown → personal countdown every 3h",
        parse_mode="Markdown"
    )

# ========== MAIN ==========
async def post_init(application: Application):
    """Setup scheduler after bot starts."""
    scheduler = AsyncIOScheduler()
    
    # Every 3 hours for groups
    scheduler.add_job(
        send_countdown_to_groups,
        IntervalTrigger(hours=3),
        args=[application.bot],
        id="group_countdown",
        replace_existing=True
    )
    
    # Every 3 hours for DM users
    scheduler.add_job(
        send_countdown_to_users,
        IntervalTrigger(hours=3),
        args=[application.bot],
        id="dm_countdown",
        replace_existing=True
    )
    
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    print("Scheduler started - countdown every 3 hours")

async def post_shutdown(application: Application):
    scheduler = application.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown()

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN environment variable not set!")
        return
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("countdown", status))
    application.add_handler(CommandHandler("dmcountdown", dm_countdown))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Lifecycle
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    
    # Run
    print("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()