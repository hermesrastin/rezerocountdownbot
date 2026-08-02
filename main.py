#!/usr/bin/env python3
"""Re:Zero Countdown Telegram Bot - Independent bot for Railway deployment."""
import os
import json
import asyncio
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Set, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ========== CONFIG ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
TARGET_DATE = datetime(2026, 8, 12, 17, 0, 0, tzinfo=timezone.utc)

# Rate limit config
RANDOM_COOLDOWN = 60
SPAM_THRESHOLD = 3
SPAM_BAN_DURATION = 1800

# Data files
GROUPS_FILE = "data/groups.json"
USERS_FILE = "data/users.json"
SETTINGS_FILE = "data/settings.json"
GROUP_SETTINGS_FILE = "data/group_settings.json"
LAST_SENT_FILE = "data/last_sent.json"

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
user_settings: Dict[int, Dict] = load_json(SETTINGS_FILE, {})
group_settings: Dict[int, Dict] = load_json(GROUP_SETTINGS_FILE, {})
last_sent: Dict[int, Dict] = load_json(LAST_SENT_FILE, {})

# Defaults
DEFAULT_COUNTDOWN_HOURS = 3
DEFAULT_STICKER_MINUTES = 10

# Cached stickers
all_stickers: List[str] = []
stickers_loaded = False

# Rate limiting
random_ratelimit: Dict[str, Dict] = {}

# ========== SETTINGS HELPERS ==========
def get_user_settings(user_id: int) -> Dict:
    if user_id not in user_settings:
        user_settings[user_id] = {
            "countdown_hours": DEFAULT_COUNTDOWN_HOURS,
            "sticker_minutes": DEFAULT_STICKER_MINUTES,
        }
    return user_settings[user_id]

def get_group_settings(chat_id: int) -> Dict:
    if chat_id not in group_settings:
        group_settings[chat_id] = {
            "countdown_hours": DEFAULT_COUNTDOWN_HOURS,
            "sticker_minutes": DEFAULT_STICKER_MINUTES,
        }
    return group_settings[chat_id]

def save_settings():
    save_json(SETTINGS_FILE, user_settings)

def save_group_settings():
    save_json(GROUP_SETTINGS_FILE, group_settings)

def get_last_sent(user_id: int) -> Dict:
    if user_id not in last_sent:
        last_sent[user_id] = {"countdown": 0, "sticker": 0}
    return last_sent[user_id]

def save_last_sent():
    save_json(LAST_SENT_FILE, last_sent)

def format_interval(hours: int) -> str:
    if hours == 1:
        return "هر ۱ ساعت"
    elif hours == 3:
        return "هر ۳ ساعت"
    elif hours == 6:
        return "هر ۶ ساعت"
    elif hours == 12:
        return "هر ۱۲ ساعت"
    elif hours == 24:
        return "هر ۲۴ ساعت"
    return f"هر {hours} ساعت"

def format_sticker_interval(minutes: int) -> str:
    if minutes == 0:
        return "هر بار ارسال کانت‌داون"
    elif minutes == 10:
        return "هر ۱۰ دقیقه"
    elif minutes == 20:
        return "هر ۲۰ دقیقه"
    elif minutes == 30:
        return "هر ۳۰ دقیقه"
    elif minutes == 60:
        return "هر ۱ ساعت"
    return f"هر {minutes} دقیقه"

def cb(label: str, data: str, active_val, this_val) -> InlineKeyboardButton:
    """Create a button with ✅ if it's the active option."""
    prefix = "✅ " if active_val == this_val else ""
    return InlineKeyboardButton(f"{prefix}{label}", callback_data=data)

# ========== RATE LIMITING ==========
def check_random_rate(chat_id: int, user_id: int) -> tuple:
    key = f"{chat_id}:{user_id}"
    now = time.time()

    if key not in random_ratelimit:
        random_ratelimit[key] = {"last": 0, "spam": 0, "ban_until": 0}

    rl = random_ratelimit[key]

    if rl["ban_until"] > 0 and now < rl["ban_until"]:
        remaining = int(rl["ban_until"] - now)
        mins = remaining // 60
        secs = remaining % 60
        return False, f"⛔ دسترسی به /random به مدت **{mins} دقیقه و {secs} ثانیه** محدود شده.\nدلیل: اسپم بیش از حد."

    if rl["ban_until"] > 0 and now >= rl["ban_until"]:
        rl["ban_until"] = 0
        rl["spam"] = 0

    elapsed = now - rl["last"]
    if elapsed < RANDOM_COOLDOWN:
        remaining = int(RANDOM_COOLDOWN - elapsed)
        rl["spam"] += 1

        if rl["spam"] >= SPAM_THRESHOLD:
            rl["ban_until"] = now + SPAM_BAN_DURATION
            rl["spam"] = 0
            return False, f"⛔ دسترسی به /random به مدت **۳۰ دقیقه** محدود شد.\nدلیل: اسپم بیش از حد (۳ بار پشت سر هم)."

        warns_left = SPAM_THRESHOLD - rl["spam"]
        return False, f"⏳ لطفا **{remaining} ثانیه** صبر کنید.\n({warns_left} اخطار دیگه = محدودیت ۳۰ دقیقه‌ای)"

    rl["last"] = now
    return True, None

# ========== COUNTDOWN MESSAGE ==========
def get_countdown_message() -> str:
    now = datetime.now(timezone.utc)
    diff = TARGET_DATE - now
    total_seconds = int(diff.total_seconds())
    
    if total_seconds <= 0:
        return "🎉 Re:Zero Season 4 · Episode 12 is OUT NOW!"
    
    days = diff.days
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    return (
        "╔══════════════════════╗\n"
        "  ◈ Re:Zero · Season 4\n"
        "  ◈ Episode 12 Incoming\n"
        "╚══════════════════════╝\n\n"
        f"  {days} days - {hours} hours and {minutes} minutes\n\n"
        "  until the next episode drops\n\n"
        "╚══════════════════════╝"
    )

# ========== STICKER HANDLING ==========
async def load_all_stickers(bot):
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

async def send_random_sticker(bot, chat_id: int, reply_to: int = None) -> bool:
    global all_stickers, stickers_loaded
    try:
        if not stickers_loaded or not all_stickers:
            await load_all_stickers(bot)
        if not all_stickers:
            return False
        chosen = random.choice(all_stickers)
        kwargs = {"chat_id": chat_id, "sticker": chosen}
        if reply_to:
            kwargs["reply_to_message_id"] = reply_to
        await bot.send_sticker(**kwargs)
        return True
    except Exception as e:
        print(f"Sticker error for {chat_id}: {e}")
        stickers_loaded = False
        return False

# ========== SMART SCHEDULER ==========
async def smart_scheduler(bot):
    now = time.time()
    
    active_users = []
    for uid in list(dm_users):
        settings = get_user_settings(uid)
        ls = get_last_sent(uid)
        
        countdown_due = (now - ls["countdown"]) >= (settings["countdown_hours"] * 3600)
        sticker_due = settings["sticker_minutes"] == 0 or (now - ls["sticker"]) >= (settings["sticker_minutes"] * 60)
        
        try:
            if countdown_due:
                await bot.send_message(chat_id=uid, text=get_countdown_message())
                ls["countdown"] = now
                active_users.append(uid)
                if sticker_due:
                    await send_random_sticker(bot, uid)
                    ls["sticker"] = now
            elif sticker_due and settings["sticker_minutes"] > 0:
                await send_random_sticker(bot, uid)
                ls["sticker"] = now
            active_users.append(uid)
        except Exception as e:
            print(f"User {uid} error: {e}")
    
    new_dm = set(active_users)
    if new_dm != dm_users:
        dm_users.intersection_update(new_dm)
        save_json(USERS_FILE, list(dm_users))
    save_last_sent()
    
    msg = get_countdown_message()
    active_groups = []
    for gid in list(groups):
        g_settings = get_group_settings(gid)
        g_ls = get_last_sent(gid + 1000000000)
        
        countdown_due = (now - g_ls["countdown"]) >= (g_settings["countdown_hours"] * 3600)
        sticker_due = g_settings["sticker_minutes"] == 0 or (now - g_ls["sticker"]) >= (g_settings["sticker_minutes"] * 60)
        
        try:
            if countdown_due:
                await bot.send_message(chat_id=gid, text=msg)
                g_ls["countdown"] = now
                active_groups.append(gid)
                if sticker_due:
                    await send_random_sticker(bot, gid)
                    g_ls["sticker"] = now
            elif sticker_due and g_settings["sticker_minutes"] > 0:
                await send_random_sticker(bot, gid)
                g_ls["sticker"] = now
            active_groups.append(gid)
        except Exception as e:
            print(f"Group {gid} error: {e}")
    
    save_last_sent()
    
    if set(active_groups) != groups:
        groups.clear()
        groups.update(active_groups)
        save_json(GROUPS_FILE, list(groups))

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        if context.args and context.args[0] == "dm":
            await dm_countdown(update, context)
            return
        
        settings = get_user_settings(user.id)
        cd_text = format_interval(settings["countdown_hours"])
        sk_text = format_sticker_interval(settings["sticker_minutes"])
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن به گروه", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("🔔 فعال‌سازی کانت‌داون پیوی", callback_data="enable_dm")],
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        
        text = (
            f"سلام {user.first_name}! 👋\n\n"
            "من **ربات کانت‌داون Re:Zero** هستم — قسمت ۱۲ فصل ۴ رو دنبال می‌کنم.\n\n"
            f"**وضعیت فعلی:**\n"
            f"• کانت‌داون: {cd_text}\n"
            f"• استیکر: {sk_text}\n\n"
            "**قابلیت‌ها:**\n"
            "• **در گروه‌ها:** کانت‌داون + استیکر رندوم\n"
            "• **در پیوی:** کانت‌داون شخصی با تنظیمات دلخواه\n"
            "• **/random** — یه استیکر رندوم Re:Zero\n"
            "• **/settings** — تنظیمات ارسال\n\n"
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
        settings = get_user_settings(user.id)
        cd_text = format_interval(settings["countdown_hours"])
        await update.message.reply_text(
            f"✅ کانت‌داون پیوی **فعال شد**!\n"
            f"زمان‌بندی: {cd_text}\n\n"
            "برای تغییر زمان‌بندی از /settings استفاده کن.",
            parse_mode="Markdown"
        )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == "private":
        s = get_user_settings(user.id)
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        
        text = (
            "⚙️ **تنظیمات ربات**\n\n"
            f"**ارسال کانت‌داون:** {cd_text}\n"
            f"**ارسال استیکر:** {sk_text}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_u")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_u")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    
    elif chat.type in ("group", "supergroup"):
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status not in ("administrator", "creator"):
                await update.message.reply_text("فقط ادمین‌ها می‌تونن تنظیمات رو تغییر بدن!")
                return
        except Exception:
            await update.message.reply_text("خطا در بررسی دسترسی. ربات باید ادمین گروه باشه!")
            return
        
        s = get_group_settings(chat.id)
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        
        text = (
            "⚙️ **تنظیمات ربات در گروه**\n\n"
            f"**ارسال کانت‌داون:** {cd_text}\n"
            f"**ارسال استیکر:** {sk_text}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_g")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_g")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    # --- Enable/Disable DM ---
    if data == "enable_dm":
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
            s = get_user_settings(user.id)
            cd_text = format_interval(s["countdown_hours"])
            await query.edit_message_text(
                f"✅ کانت‌داون پیوی **فعال شد**!\n"
                f"زمان‌بندی: {cd_text}\n\n"
                "برای تغییر زمان‌بندی از /settings استفاده کن.",
                parse_mode="Markdown"
            )
        return
    
    # --- Close Menu ---
    if data == "close_menu":
        await query.edit_message_text("✅ منو بسته شد.", reply_markup=None)
        return
    
    # --- Open Settings ---
    if data == "open_settings":
        s = get_user_settings(user.id)
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        
        text = (
            "⚙️ **تنظیمات ربات**\n\n"
            f"**ارسال کانت‌داون:** {cd_text}\n"
            f"**ارسال استیکر:** {sk_text}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_u")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_u")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
        return
    
    if data == "back_to_start":
        await query.edit_message_text("✅ منو بسته شد.", reply_markup=None)
        return
    
    # ==================== USER COUNTDOWN ====================
    if data == "set_countdown_u":
        s = get_user_settings(user.id)
        cur = s["countdown_hours"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱ ساعت", "cd_u_1", cur, 1)],
            [cb("۳ ساعت (پیش‌فرض)", "cd_u_3", cur, 3)],
            [cb("۶ ساعت", "cd_u_6", cur, 6)],
            [cb("۱۲ ساعت", "cd_u_12", cur, 12)],
            [cb("۲۴ ساعت", "cd_u_24", cur, 24)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            "⏰ **زمان‌بندی کانت‌даون**\n\nهر چند ساعت کانت‌داون برات بفرستم؟",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    if data.startswith("cd_u_"):
        hours = int(data.split("_")[2])
        if user.id not in user_settings:
            user_settings[user.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES}
        user_settings[user.id]["countdown_hours"] = hours
        save_settings()
        
        # Rebuild menu with new selection
        s = get_user_settings(user.id)
        cur = s["countdown_hours"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱ ساعت", "cd_u_1", cur, 1)],
            [cb("۳ ساعت (پیش‌فرض)", "cd_u_3", cur, 3)],
            [cb("۶ ساعت", "cd_u_6", cur, 6)],
            [cb("۱۲ ساعت", "cd_u_12", cur, 12)],
            [cb("۲۴ ساعت", "cd_u_24", cur, 24)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            f"✅ تنظیم شد!\n\nزمان‌بندی کانت‌داون: **{format_interval(hours)}**",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    # ==================== GROUP COUNTDOWN ====================
    if data == "set_countdown_g":
        chat = query.message.chat
        s = get_group_settings(chat.id)
        cur = s["countdown_hours"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱ ساعت", "cd_g_1", cur, 1)],
            [cb("۳ ساعت (پیش‌فرض)", "cd_g_3", cur, 3)],
            [cb("۶ ساعت", "cd_g_6", cur, 6)],
            [cb("۱۲ ساعت", "cd_g_12", cur, 12)],
            [cb("۲۴ ساعت", "cd_g_24", cur, 24)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            "⏰ **زمان‌بندی کانت‌داون**\n\nهر چند ساعت کانت‌داون در گروه بفرستم؟",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    if data.startswith("cd_g_"):
        hours = int(data.split("_")[2])
        chat = query.message.chat
        if chat.id not in group_settings:
            group_settings[chat.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES}
        group_settings[chat.id]["countdown_hours"] = hours
        save_group_settings()
        
        s = get_group_settings(chat.id)
        cur = s["countdown_hours"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱ ساعت", "cd_g_1", cur, 1)],
            [cb("۳ ساعت (پیش‌فرض)", "cd_g_3", cur, 3)],
            [cb("۶ ساعت", "cd_g_6", cur, 6)],
            [cb("۱۲ ساعت", "cd_g_12", cur, 12)],
            [cb("۲۴ ساعت", "cd_g_24", cur, 24)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            f"✅ تنظیم شد!\n\nزمان‌بندی کانت‌داون گروه: **{format_interval(hours)}**",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    # ==================== USER STICKER ====================
    if data == "set_sticker_u":
        s = get_user_settings(user.id)
        cur = s["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_u_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_u_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_u_30", cur, 30)],
            [cb("۱ ساعت", "sk_u_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_u_0", cur, 0)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            "🎨 **زمان‌بندی استیکر**\n\nهر چند وقت یه استیکر رندوم برات بفرستم؟",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    if data.startswith("sk_u_"):
        minutes = int(data.split("_")[2])
        if user.id not in user_settings:
            user_settings[user.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES}
        user_settings[user.id]["sticker_minutes"] = minutes
        save_settings()
        
        s = get_user_settings(user.id)
        cur = s["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_u_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_u_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_u_30", cur, 30)],
            [cb("۱ ساعت", "sk_u_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_u_0", cur, 0)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            f"✅ تنظیم شد!\n\nزمان‌بندی استیکر: **{format_sticker_interval(minutes)}**",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    # ==================== GROUP STICKER ====================
    if data == "set_sticker_g":
        chat = query.message.chat
        s = get_group_settings(chat.id)
        cur = s["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_g_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_g_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_g_30", cur, 30)],
            [cb("۱ ساعت", "sk_g_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_g_0", cur, 0)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            "🎨 **زمان‌بندی استیکر**\n\nهر چند وقت یه استیکر رندوم در گروه بفرستم؟",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return
    
    if data.startswith("sk_g_"):
        minutes = int(data.split("_")[2])
        chat = query.message.chat
        if chat.id not in group_settings:
            group_settings[chat.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES}
        group_settings[chat.id]["sticker_minutes"] = minutes
        save_group_settings()
        
        s = get_group_settings(chat.id)
        cur = s["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_g_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_g_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_g_30", cur, 30)],
            [cb("۱ ساعت", "sk_g_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_g_0", cur, 0)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            f"✅ تنظیم شد!\n\nزمان‌بندی استیکر گروه: **{format_sticker_interval(minutes)}**",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return

async def random_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    reply_to = None

    if chat.type in ("group", "supergroup"):
        allowed, msg = check_random_rate(chat_id, user.id)
        if not allowed:
            await update.message.reply_text(msg, parse_mode="Markdown")
            return
        if update.message and update.message.reply_to_message:
            reply_to = update.message.reply_to_message.message_id
    elif chat.type == "private":
        if update.message and update.message.reply_to_message:
            reply_to = update.message.reply_to_message.message_id

    await send_random_sticker(context.bot, chat_id, reply_to=reply_to)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = get_countdown_message()
    
    if chat.type == "private":
        user = update.effective_user
        s = get_user_settings(user.id)
        status_text = "✅ فعال" if chat.id in dm_users else "❌ غیرفعال"
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        msg += (
            f"\n\n**وضعیت پیوی شما:** {status_text}\n"
            f"**زمان‌بندی:** {cd_text}\n"
            f"**استیکر:** {sk_text}\n\n"
            "برای تغییر از /settings استفاده کن."
        )
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "/settings — تنظیمات زمان‌بندی ارسال\n"
        "/help — این پیام\n\n"
        "**قابلیت‌های گروه:**\n"
        "• ربات رو به گروه اضافه کن → کانت‌داون هر ۳ ساعت\n"
        "• استیکرهای رندوم Re:Zero\n\n"
        "**قابلیت‌های پیوی:**\n"
        "• /dmcountdown → کانت‌داون شخصی\n"
        "• /settings → تنظیم زمان‌بندی دلخواه"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ========== MAIN ==========
async def post_init(application: Application):
    scheduler = AsyncIOScheduler()
    
    scheduler.add_job(
        smart_scheduler,
        IntervalTrigger(minutes=10),
        args=[application.bot],
        id="smart_scheduler",
        replace_existing=True
    )
    
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    print("Scheduler started - smart check every 10 minutes")

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
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.post_init = post_init
    application.post_shutdown = post_shutdown
    
    print("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
