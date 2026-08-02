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

        chosen = random.choice(all_stickers)
        await bot.send_sticker(chat_id=chat_id, sticker=chosen)
        return True
    except Exception as e:
        print(f"Sticker error for {chat_id}: {e}")
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
    
    if set(active_users) != dm_users:
        dm_users = set(active_users)
        save_json(USERS_FILE, list(dm_users))

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        if context.args and context.args[0] == "dm":
            await dm_countdown(update, context)
            return
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن به گروه", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("🔔 فعال‌سازی کانت‌داون پیوی", callback_data="enable_dm")]
        ])
        
        text = (
            f"سلام {user.first_name}! 👋\n\n"
            "من **ربات کانت‌داون Re:Zero** هستم — قسمت ۱۲ فصل ۴ رو دنبال می‌کنم.\n\n"
            "**قابلیت‌ها:**\n"
            "• **در گروه‌ها:** هر ۳ ساعت کانت‌داون + استیکر رندوم\n"
            "• **در پیوی:** کانت‌داون شخصی (از /dmcountdown استفاده کن)\n"
            "• **/random** — یه استیکر رندوم Re:Zero بفرست\n\n"
            "یکی از گزینه‌ها رو انتخاب کن:"
        )
        
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        text = "ربات کانت‌داون Re:Zero فعاله! 📅\nهر ۳ ساعت کانت‌داون قسمت ۱۲ رو می‌فرستم."
        await update.message.reply_text(text)

async def dm_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type != "private":
        await update.message.reply_text("این دستور فقط در پیوی کار می‌کنه!")
        return
    
    if user.id in dm_users:
        dm_users.discard(user.id)
        save_json(USERS_FILE, list(dm_users))
        await update.message.reply_text(
            "❌ کانت‌داون پیوی **غیرفعال شد**.\nدیگه آپدیتی دریافت نمی‌کنی.",
            parse_mode="Markdown"
        )
    else:
        dm_users.add(user.id)
        save_json(USERS_FILE, list(dm_users))
        await update.message.reply_text(
            "✅ کانت‌داون پیوی **فعال شد**!\n"
            "هر ۳ ساعت کانت‌داون قسمت ۱۲ رو دریافت می‌کنی.\n\n"
            "برای غیرفعال کردن دوباره /dmcountdown بزن.",
            parse_mode="Markdown"
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "enable_dm":
        user = query.from_user
        if user.id in dm_users:
            dm_users.discard(user.id)
            save_json(USERS_FILE, list(dm_users))
            await query.edit_message_text(
                "❌ کانت‌داون پیوی **غیرفعال شد**.\nدیگه آپدیتی دریافت نمی‌کنی.",
                parse_mode="Markdown"
            )
        else:
            dm_users.add(user.id)
            save_json(USERS_FILE, list(dm_users))
            await query.edit_message_text(
                "✅ کانت‌داون پیوی **فعال شد**!\n"
                "هر ۳ ساعت کانت‌داون قسمت ۱۲ رو دریافت می‌کنی.\n\n"
                "برای غیرفعال کردن دوباره /dmcountdown بزن.",
                parse_mode="Markdown"
            )

async def random_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a truly random sticker from all packs."""
    chat_id = update.effective_chat.id
    await send_random_sticker(context.bot, chat_id)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = get_countdown_message()
    
    if chat.type == "private":
        status_text = "✅ فعال" if chat.id in dm_users else "❌ غیرفعال"
        msg += f"\n\n**وضعیت پیوی شما:** {status_text}\nبرای تغییر /dmcountdown بزن."
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when bot is added/removed from groups."""
    result = update.my_chat_member
    chat = result.chat
    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status
    
    if new_status in ("member", "administrator") and old_status in ("left", "kicked"):
        if chat.type in ("group", "supergroup"):
            groups.add(chat.id)
            save_json(GROUPS_FILE, list(groups))
            print(f"Added to group: {chat.title} ({chat.id})")
            try:
                await context.bot.send_message(
                    chat_id=chat.id,
                    text="ممنون که منو اضافه کردی! 🎉\nهر ۳ ساعت کانت‌داون قسمت ۱۲ Re:Zero رو می‌فرستم."
                )
            except:
                pass
    
    elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
        if chat.type in ("group", "supergroup"):
            groups.discard(chat.id)
            save_json(GROUPS_FILE, list(groups))
            print(f"Removed from group: {chat.title} ({chat.id})")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "**دستورات ربات کانت‌داون Re:Zero**\n\n"
        "/start — نمایش پیام خوش‌آمدگویی\n"
        "/countdown — نمایش کانت‌داون فعلی\n"
        "/random — ارسال یه استیکر رندوم Re:Zero\n"
        "/dmcountdown — فعال/غیرفعال کردن نوتیفیکیشن پیوی\n"
        "/help — این پیام\n\n"
        "**قابلیت‌های گروه:**\n"
        "• ربات رو به گروه اضافه کن → کانت‌داون هر ۳ ساعت\n"
        "• استیکرهای رندوم Re:Zero\n\n"
        "**قابلیت‌های پیوی:**\n"
        "• /dmcountdown → کانت‌داون شخصی هر ۳ ساعت"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ========== MAIN ==========
async def post_init(application: Application):
    """Setup scheduler after bot starts."""
    scheduler = AsyncIOScheduler()
    
    scheduler.add_job(
        send_countdown_to_groups,
        IntervalTrigger(hours=3),
        args=[application.bot],
        id="group_countdown",
        replace_existing=True
    )
    
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
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("countdown", status))
    application.add_handler(CommandHandler("dmcountdown", dm_countdown))
    application.add_handler(CommandHandler("random", random_sticker))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    from telegram.ext import CallbackQueryHandler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    
    print("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
