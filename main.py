#!/usr/bin/env python3
"""Re:Zero Countdown Telegram Bot - Independent bot for Railway deployment."""
import os
import json
import asyncio
import random
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Set, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

import urllib.request
import re as re_mod

# ========== CONFIG ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")

LIVECHART_ANIME_ID = 13115
LIVECHART_URL = "https://www.livechart.me/summer-2026/tv"
LIVECHART_CACHE_FILE = "data/livechart_cache.json"
FALLBACK_TARGET = datetime(2026, 8, 12, 17, 0, 0, tzinfo=timezone.utc)
_target_cache = {"timestamp": 0, "target_epoch": 0}

RANDOM_COOLDOWN = 60
SPAM_THRESHOLD = 3
SPAM_BAN_DURATION = 1800

GROUPS_FILE = "data/groups.json"
USERS_FILE = "data/users.json"
SETTINGS_FILE = "data/settings.json"
GROUP_SETTINGS_FILE = "data/group_settings.json"
LAST_SENT_FILE = "data/last_sent.json"
EPISODE_STATE_FILE = "data/episode_state.json"
CELEBRATION_HOURS = 5

STICKER_PACKS = [
    "rasrez_by_fStikBot",
    "b6a3b0ad87f4e5_by_anipackbot",
    "RezeroByLimbo",
    "Emiliatan_by_TgEmojis_bot",
    "Echidna_Rezero_Otakuzdream",
    "Makyowo",
]

# Auto-reply limits: max replies per chat, resets after cooldown
AUTO_REPLY_LIMIT = 2
auto_reply_counter: Dict[int, Dict] = {}  # chat_id -> {"count": int, "reset_at": float}

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
episode_state: Dict = load_json(EPISODE_STATE_FILE, {"current_episode": 12, "last_episode_time": 0})

DEFAULT_COUNTDOWN_HOURS = 3
DEFAULT_STICKER_MINUTES = 10

all_stickers: List[str] = []
stickers_loaded = False

random_ratelimit: Dict[str, Dict] = {}

# ========== MEMORY-EFFICIENT MESSAGE TRACKING ==========
# Track last 7 messages per group (any user/bot) for sticker reply
recent_group_messages: Dict[int, deque] = {}
MAX_RECENT = 7

def track_group_message(chat_id: int, message_id: int):
    """Add a message_id to the recent messages ring buffer for a group."""
    if chat_id not in recent_group_messages:
        recent_group_messages[chat_id] = deque(maxlen=MAX_RECENT)
    recent_group_messages[chat_id].append(message_id)

def pop_random_recent(chat_id: int):
    """Pick and remove a random recent message_id from a group."""
    if chat_id in recent_group_messages and recent_group_messages[chat_id]:
        msgs = list(recent_group_messages[chat_id])
        chosen = random.choice(msgs)
        return chosen
    return None

def cleanup_inactive_chats(active_chats: Set[int]):
    """Remove tracking data for chats no longer active."""
    stale = [cid for cid in recent_group_messages if cid not in active_chats]
    for cid in stale:
        del recent_group_messages[cid]

# ========== LIVECHART SCRAPING ==========
def fetch_target_date() -> datetime:
    global _target_cache
    now = time.time()
    if _target_cache["target_epoch"] > 0 and (now - _target_cache["timestamp"]) < 3600:
        return datetime.fromtimestamp(_target_cache["target_epoch"], tz=timezone.utc)
    try:
        file_cache = load_json(LIVECHART_CACHE_FILE, {})
        if file_cache.get("target_epoch", 0) > 0 and (now - file_cache.get("timestamp", 0)) < 3600:
            _target_cache = file_cache
            return datetime.fromtimestamp(file_cache["target_epoch"], tz=timezone.utc)
        req = urllib.request.Request(LIVECHART_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        pattern = rf'data-anime-id="{LIVECHART_ANIME_ID}".*?data-timestamp="(\d+)"'
        match = re_mod.search(pattern, html, re_mod.DOTALL)
        if match:
            epoch = int(match.group(1))
            _target_cache = {"timestamp": now, "target_epoch": epoch}
            save_json(LIVECHART_CACHE_FILE, _target_cache)
            print(f"LiveChart: target = {datetime.fromtimestamp(epoch, tz=timezone.utc)}")
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        else:
            print("LiveChart: Re:Zero not found, using fallback")
    except Exception as e:
        print(f"LiveChart error: {e}, using fallback")
    return FALLBACK_TARGET

# ========== SETTINGS HELPERS ==========
def get_user_settings(user_id: int) -> Dict:
    if user_id not in user_settings:
        user_settings[user_id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES, "countdown_enabled": True, "sticker_enabled": True}
    return user_settings[user_id]

def get_group_settings(chat_id: int) -> Dict:
    if chat_id not in group_settings:
        group_settings[chat_id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES, "countdown_enabled": True, "sticker_enabled": True}
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

def get_current_episode() -> int:
    return episode_state.get("current_episode", 12)

def save_episode_state():
    save_json(EPISODE_STATE_FILE, episode_state)

def format_interval(hours: int) -> str:
    m = {1: "هر ۱ ساعت", 3: "هر ۳ ساعت", 6: "هر ۶ ساعت", 12: "هر ۱۲ ساعت", 24: "هر ۲۴ ساعت"}
    return m.get(hours, f"هر {hours} ساعت")

def format_sticker_interval(minutes: int) -> str:
    if minutes == 0: return "هر بار ارسال کانت‌داون"
    m = {10: "هر ۱۰ دقیقه", 20: "هر ۲۰ دقیقه", 30: "هر ۳۰ دقیقه", 60: "هر ۱ ساعت"}
    return m.get(minutes, f"هر {minutes} دقیقه")

def cb(label: str, data: str, active_val, this_val) -> InlineKeyboardButton:
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
        return False, f"⛔ دسترسی به /random به مدت **{remaining // 60} دقیقه و {remaining % 60} ثانیه** محدود شده.\nدلیل: اسپم بیش از حد."
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
        return False, f"⏳ لطفا **{remaining} ثانیه** صبر کنید.\n({SPAM_THRESHOLD - rl['spam']} اخطار دیگه = محدودیت ۳۰ دقیقه‌ای)"
    rl["last"] = now
    return True, None

# ========== COUNTDOWN MESSAGE ==========
def get_countdown_message() -> str:
    ep = get_current_episode()
    target = fetch_target_date()
    now = datetime.now(timezone.utc)
    diff = target - now
    total_seconds = int(diff.total_seconds())

    # Episode just aired — within CELEBRATION_HOURS window
    if total_seconds <= 0:
        last_time = episode_state.get("last_episode_time", 0)

        if last_time == 0:
            # First detection that episode aired — record the time
            episode_state["last_episode_time"] = time.time()
            save_episode_state()
            last_time = episode_state["last_episode_time"]

        time_since_recorded = (time.time() - last_time) / 3600

        if time_since_recorded < CELEBRATION_HOURS:
            # 🎉 CELEBRATION MODE
            next_remaining = target - now
            nr_total = int(next_remaining.total_seconds())
            if nr_total > 0:
                nr_days = next_remaining.days
                nr_hours = nr_total // 3600
                nr_minutes = (nr_total % 3600) // 60
                next_time = f"{nr_days}d {nr_hours}h {nr_minutes}m"
            else:
                next_time = "..."
            return (
                "╔══════════════════════╗\n"
                "║  ◗ EPISODE DROP ◖    ║\n"
                "╠══════════════════════╣\n"
                "║                      ║\n"
                f"║  ◆ Re:Zero · S4      ║\n"
                f"║  ◆ Episode {ep} OUT    ║\n"
                "║                      ║\n"
                "╠══════════════════════╣\n"
                "║  • New episode aired  ║\n"
                "║  • Sub available soon ║\n"
                "║  ▸ Stay tuned ✓      ║\n"
                "║                      ║\n"
                "╠══════════════════════╣\n"
                "║  ▵ Next episode in:   ║\n"
                f"║    ◇ {next_time}          ║\n"
                "║                      ║\n"
                "╚══════════════════════╝"
            )
        else:
            # Celebration window over — auto-increment episode
            episode_state["current_episode"] = ep + 1
            episode_state["last_episode_time"] = 0
            save_episode_state()
            ep = get_current_episode()
            # Re-fetch target for new episode
            target = fetch_target_date()
            diff = target - now
            total_seconds = int(diff.total_seconds())
            if total_seconds <= 0:
                return f"🎉 Re:Zero Season 4 · Episode {ep} is OUT NOW!"

    days = diff.days
    total_hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return (
        "╔══════════════════════╗\n"
        "  ◈ Re:Zero · Season 4\n"
        f"  ◈ Episode {ep} Incoming\n"
        "╚══════════════════════╝\n\n"
        f"  {days} days - {total_hours} hours and {minutes} minutes\n\n"
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

async def send_random_sticker(bot, chat_id: int, reply_to=None) -> bool:
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

    # --- DM Users ---
    active_users = []
    for uid in list(dm_users):
        settings = get_user_settings(uid)
        ls = get_last_sent(uid)
        countdown_due = (now - ls["countdown"]) >= (settings["countdown_hours"] * 3600)
        sticker_due = settings["sticker_minutes"] == 0 or (now - ls["sticker"]) >= (settings["sticker_minutes"] * 60)
        cd_enabled = settings.get("countdown_enabled", True)
        sk_enabled = settings.get("sticker_enabled", True)
        try:
            if countdown_due and cd_enabled:
                await bot.send_message(chat_id=uid, text=get_countdown_message())
                ls["countdown"] = now
                if sticker_due and sk_enabled:
                    await send_random_sticker(bot, uid)
                    ls["sticker"] = now
            elif sticker_due and settings["sticker_minutes"] > 0 and sk_enabled:
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

    # --- Groups: sticker replies to last 7 messages ---
    msg = get_countdown_message()
    active_groups = []
    for gid in list(groups):
        g_settings = get_group_settings(gid)
        g_ls = get_last_sent(gid + 1000000000)
        cd_enabled = g_settings.get("countdown_enabled", True)
        sk_enabled = g_settings.get("sticker_enabled", True)
        countdown_due = (now - g_ls["countdown"]) >= (g_settings["countdown_hours"] * 3600)
        sticker_due = g_settings["sticker_minutes"] == 0 or (now - g_ls["sticker"]) >= (g_settings["sticker_minutes"] * 60)
        try:
            sticker_reply_to = pop_random_recent(gid) if sticker_due and sk_enabled else None

            if countdown_due and cd_enabled:
                await bot.send_message(chat_id=gid, text=msg)
                g_ls["countdown"] = now
                active_groups.append(gid)
                if sticker_due and sk_enabled:
                    await send_random_sticker(bot, gid, reply_to=sticker_reply_to)
                    g_ls["sticker"] = now
            elif sticker_due and g_settings["sticker_minutes"] > 0 and sk_enabled:
                await send_random_sticker(bot, gid, reply_to=sticker_reply_to)
                g_ls["sticker"] = now
            active_groups.append(gid)
        except Exception as e:
            print(f"Group {gid} error: {e}")

    save_last_sent()

    if set(active_groups) != groups:
        groups.clear()
        groups.update(active_groups)
        save_json(GROUPS_FILE, list(groups))

    # Clean memory for inactive chats
    cleanup_inactive_chats(groups)

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        if context.args and context.args[0] == "dm":
            await dm_countdown(update, context)
            return
        s = get_user_settings(user.id)
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ افزودن به گروه", url=f"https://t.me/{context.bot.username}?startgroup=true")],
            [InlineKeyboardButton("🔔 فعال‌سازی کانت‌داون پیوی", callback_data="enable_dm")],
            [InlineKeyboardButton("⚙️ تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        text = (
            f"سلام {user.first_name}! 👋\n\n"
            f"من **ربات کانت‌داون Re:Zero** هستم — قسمت {get_current_episode()} فصل ۴ رو دنبال می‌کنم.\n\n"
            f"**وضعیت فعلی:**\n• کانت‌داون: {cd_text}\n• استیکر: {sk_text}\n\n"
            "**قابلیت‌ها:**\n"
            "• **در گروه‌ها:** کانت‌داون + استیکر رندوم\n"
            "• **در پیوی:** کانت‌داون شخصی با تنظیمات دلخواه\n"
            "• **/random** — یه استیکر رندوم Re:Zero\n"
            "• **/settings** — تنظیمات ارسال\n\n"
            "یکی از گزینه‌ها رو انتخاب کن:"
        )
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        if chat.id not in groups:
            groups.add(chat.id)
            save_json(GROUPS_FILE, list(groups))
        ep = get_current_episode()
        await update.message.reply_text(f"ربات کانت‌داون Re:Zero فعاله! 📅\nهر ۳ ساعت کانت‌داون قسمت {ep} رو می‌فرستم.")

async def dm_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type != "private":
        await update.message.reply_text("این دستور فقط در پیوی کار می‌کنه!")
        return
    if user.id in dm_users:
        dm_users.discard(user.id)
        save_json(USERS_FILE, list(dm_users))
        await update.message.reply_text("❌ کانت‌داون پیوی **غیرفعال شد**.\nدیگه آپدیتی دریافت نمی‌کنی.", parse_mode="Markdown")
    else:
        dm_users.add(user.id)
        save_json(USERS_FILE, list(dm_users))
        s = get_user_settings(user.id)
        await update.message.reply_text(
            f"✅ کانت‌داون پیوی **فعال شد**!\nزمان‌بندی: {format_interval(s['countdown_hours'])}\n\n"
            "برای تغییر زمان‌بندی از /settings استفاده کن.", parse_mode="Markdown"
        )

async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if chat.type in ("group", "supergroup") and chat.id not in groups:
        groups.add(chat.id)
        save_json(GROUPS_FILE, list(groups))
    if chat.type == "private":
        s = get_user_settings(user.id)
        text = (
            "⚙️ **تنظیمات ربات**\n\n"
            f"**ارسال کانت‌داون:** {format_interval(s['countdown_hours'])}\n"
            f"**ارسال استیکر:** {format_sticker_interval(s['sticker_minutes'])}\n\n"
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
        text = (
            "⚙️ **تنظیمات ربات در گروه**\n\n"
            f"**ارسال کانت‌داون:** {format_interval(s['countdown_hours'])}\n"
            f"**ارسال استیکر:** {format_sticker_interval(s['sticker_minutes'])}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_g")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_g")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")

async def toggle_countdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        s = get_user_settings(update.effective_user.id)
        s["countdown_enabled"] = not s.get("countdown_enabled", True)
        save_settings()
        st = "✅ فعال شد" if s["countdown_enabled"] else "❌ غیرفعال شد"
        await update.message.reply_text(f"ارسال کانت‌داون **{st}**.", parse_mode="Markdown")
    elif chat.type in ("group", "supergroup"):
        s = get_group_settings(chat.id)
        s["countdown_enabled"] = not s.get("countdown_enabled", True)
        save_group_settings()
        st = "✅ فعال شد" if s["countdown_enabled"] else "❌ غیرفعال شد"
        await update.message.reply_text(f"ارسال کانت‌داون گروه **{st}**.", parse_mode="Markdown")

async def toggle_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        s = get_user_settings(update.effective_user.id)
        s["sticker_enabled"] = not s.get("sticker_enabled", True)
        save_settings()
        st = "✅ فعال شد" if s["sticker_enabled"] else "❌ غیرفعال شد"
        await update.message.reply_text(f"ارسال استیکر **{st}**.", parse_mode="Markdown")
    elif chat.type in ("group", "supergroup"):
        s = get_group_settings(chat.id)
        s["sticker_enabled"] = not s.get("sticker_enabled", True)
        save_group_settings()
        st = "✅ فعال شد" if s["sticker_enabled"] else "❌ غیرفعال شد"
        await update.message.reply_text(f"ارسال استیکر گروه **{st}**.", parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "enable_dm":
        if user.id in dm_users:
            dm_users.discard(user.id)
            save_json(USERS_FILE, list(dm_users))
            await query.edit_message_text("❌ کانت‌داون پیوی **غیرفعال شد**.\nدیگه آپدیتی دریافت نمی‌کنی.", parse_mode="Markdown")
        else:
            dm_users.add(user.id)
            save_json(USERS_FILE, list(dm_users))
            s = get_user_settings(user.id)
            await query.edit_message_text(
                f"✅ کانت‌داون پیوی **فعال شد**!\nزمان‌بندی: {format_interval(s['countdown_hours'])}\n\n"
                "برای تغییر زمان‌بندی از /settings استفاده کن.", parse_mode="Markdown"
            )
        return
    # --- Toggle Countdown (User) ---
    if data == "toggle_cd_u":
        s = get_user_settings(user.id)
        s["countdown_enabled"] = not s.get("countdown_enabled", True)
        save_settings()
        on = s["countdown_enabled"]
        sk_on = "🟢" if s.get("sticker_enabled", True) else "🔴"
        cd_on = "🟢" if on else "🔴"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{cd_on} کانت‌داون پیوی", callback_data="toggle_cd_u"),
             InlineKeyboardButton(f"{sk_on} استیکر پیوی", callback_data="toggle_sk_u")],
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_u")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_u")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        await query.edit_message_text(
            "⚙️ **تنظیمات ربات**\n\n"
            f"**ارسال کانت‌داون:** {cd_text}\n"
            f"**ارسال استیکر:** {sk_text}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return

    # --- Toggle Sticker (User) ---
    if data == "toggle_sk_u":
        s = get_user_settings(user.id)
        s["sticker_enabled"] = not s.get("sticker_enabled", True)
        save_settings()
        on = s["sticker_enabled"]
        sk_on = "🟢" if on else "🔴"
        cd_on = "🟢" if s.get("countdown_enabled", True) else "🔴"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{cd_on} کانت‌داون پیوی", callback_data="toggle_cd_u"),
             InlineKeyboardButton(f"{sk_on} استیکر پیوی", callback_data="toggle_sk_u")],
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_u")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_u")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        cd_text = format_interval(s["countdown_hours"])
        sk_text = format_sticker_interval(s["sticker_minutes"])
        await query.edit_message_text(
            "⚙️ **تنظیمات ربات**\n\n"
            f"**ارسال کانت‌داون:** {cd_text}\n"
            f"**ارسال استیکر:** {sk_text}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return

    # --- Close Menu ---
    if data == "close_menu":
        await query.edit_message_text("✅ منو بسته شد.", reply_markup=None)
        return

    if data == "open_settings":
        s = get_user_settings(user.id)
        text = (
            "⚙️ **تنظیمات ربات**\n\n"
            f"**ارسال کانت‌داون:** {format_interval(s['countdown_hours'])}\n"
            f"**ارسال استیکر:** {format_sticker_interval(s['sticker_minutes'])}\n\n"
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

    # ==================== GROUP TOGGLE ====================
    if data == "toggle_cd_g":
        chat = query.message.chat
        s = get_group_settings(chat.id)
        s["countdown_enabled"] = not s.get("countdown_enabled", True)
        save_group_settings()
        cd_on = "🟢" if s["countdown_enabled"] else "🔴"
        sk_on = "🟢" if s.get("sticker_enabled", True) else "🔴"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{cd_on} کانت‌داون گروه", callback_data="toggle_cd_g"),
             InlineKeyboardButton(f"{sk_on} استیکر گروه", callback_data="toggle_sk_g")],
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_g")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_g")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            "⚙️ **تنظیمات ربات در گروه**\n\n"
            f"**ارسال کانت‌داون:** {format_interval(s['countdown_hours'])}\n"
            f"**ارسال استیکر:** {format_sticker_interval(s['sticker_minutes'])}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return

    if data == "toggle_sk_g":
        chat = query.message.chat
        s = get_group_settings(chat.id)
        s["sticker_enabled"] = not s.get("sticker_enabled", True)
        save_group_settings()
        cd_on = "🟢" if s.get("countdown_enabled", True) else "🔴"
        sk_on = "🟢" if s["sticker_enabled"] else "🔴"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{cd_on} کانت‌داون گروه", callback_data="toggle_cd_g"),
             InlineKeyboardButton(f"{sk_on} استیکر گروه", callback_data="toggle_sk_g")],
            [InlineKeyboardButton("⏰ زمان‌بندی کانت‌داون", callback_data="set_countdown_g")],
            [InlineKeyboardButton("🎨 زمان‌بندی استیکر", callback_data="set_sticker_g")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(
            "⚙️ **تنظیمات ربات در گروه**\n\n"
            f"**ارسال کانت‌داون:** {format_interval(s['countdown_hours'])}\n"
            f"**ارسال استیکر:** {format_sticker_interval(s['sticker_minutes'])}\n\n"
            "روی یکی از گزینه‌ها کلیک کن:",
            reply_markup=keyboard, parse_mode="Markdown"
        )
        return

    # USER COUNTDOWN
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
        await query.edit_message_text("⏰ **زمان‌بندی کانت‌داون**\n\nهر چند ساعت کانت‌داون برات بفرستم؟", reply_markup=keyboard, parse_mode="Markdown")
        return

    if data.startswith("cd_u_"):
        hours = int(data.split("_")[2])
        if user.id not in user_settings:
            user_settings[user.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES, "countdown_enabled": True, "sticker_enabled": True}
        user_settings[user.id]["countdown_hours"] = hours
        save_settings()
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
        await query.edit_message_text(f"✅ تنظیم شد!\n\nزمان‌بندی کانت‌داون: **{format_interval(hours)}**", reply_markup=keyboard, parse_mode="Markdown")
        return

    # GROUP COUNTDOWN
    if data == "set_countdown_g":
        chat = query.message.chat
        cur = get_group_settings(chat.id)["countdown_hours"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱ ساعت", "cd_g_1", cur, 1)],
            [cb("۳ ساعت (پیش‌فرض)", "cd_g_3", cur, 3)],
            [cb("۶ ساعت", "cd_g_6", cur, 6)],
            [cb("۱۲ ساعت", "cd_g_12", cur, 12)],
            [cb("۲۴ ساعت", "cd_g_24", cur, 24)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text("⏰ **زمان‌بندی کانت‌داون**\n\nهر چند ساعت کانت‌داون در گروه بفرستم؟", reply_markup=keyboard, parse_mode="Markdown")
        return

    if data.startswith("cd_g_"):
        hours = int(data.split("_")[2])
        chat = query.message.chat
        if chat.id not in group_settings:
            group_settings[chat.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES, "countdown_enabled": True, "sticker_enabled": True}
        group_settings[chat.id]["countdown_hours"] = hours
        save_group_settings()
        cur = get_group_settings(chat.id)["countdown_hours"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱ ساعت", "cd_g_1", cur, 1)],
            [cb("۳ ساعت (پیش‌فرض)", "cd_g_3", cur, 3)],
            [cb("۶ ساعت", "cd_g_6", cur, 6)],
            [cb("۱۲ ساعت", "cd_g_12", cur, 12)],
            [cb("۲۴ ساعت", "cd_g_24", cur, 24)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(f"✅ تنظیم شد!\n\nزمان‌بندی کانت‌داون گروه: **{format_interval(hours)}**", reply_markup=keyboard, parse_mode="Markdown")
        return

    # USER STICKER
    if data == "set_sticker_u":
        cur = get_user_settings(user.id)["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_u_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_u_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_u_30", cur, 30)],
            [cb("۱ ساعت", "sk_u_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_u_0", cur, 0)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text("🎨 **زمان‌بندی استیکر**\n\nهر چند وقت یه استیکر رندوم برات بفرستم؟", reply_markup=keyboard, parse_mode="Markdown")
        return

    if data.startswith("sk_u_"):
        minutes = int(data.split("_")[2])
        if user.id not in user_settings:
            user_settings[user.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES, "countdown_enabled": True, "sticker_enabled": True}
        user_settings[user.id]["sticker_minutes"] = minutes
        save_settings()
        cur = get_user_settings(user.id)["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_u_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_u_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_u_30", cur, 30)],
            [cb("۱ ساعت", "sk_u_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_u_0", cur, 0)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(f"✅ تنظیم شد!\n\nزمان‌بندی استیکر: **{format_sticker_interval(minutes)}**", reply_markup=keyboard, parse_mode="Markdown")
        return

    # GROUP STICKER
    if data == "set_sticker_g":
        cur = get_group_settings(query.message.chat.id)["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_g_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_g_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_g_30", cur, 30)],
            [cb("۱ ساعت", "sk_g_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_g_0", cur, 0)],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text("🎨 **زمان‌بندی استیکر**\n\nهر چند وقت یه استیکر رندوم در گروه بفرستم؟", reply_markup=keyboard, parse_mode="Markdown")
        return

    if data.startswith("sk_g_"):
        minutes = int(data.split("_")[2])
        chat = query.message.chat
        if chat.id not in group_settings:
            group_settings[chat.id] = {"countdown_hours": DEFAULT_COUNTDOWN_HOURS, "sticker_minutes": DEFAULT_STICKER_MINUTES, "countdown_enabled": True, "sticker_enabled": True}
        group_settings[chat.id]["sticker_minutes"] = minutes
        save_group_settings()
        cur = get_group_settings(chat.id)["sticker_minutes"]
        keyboard = InlineKeyboardMarkup([
            [cb("۱۰ دقیقه", "sk_g_10", cur, 10)],
            [cb("۲۰ دقیقه", "sk_g_20", cur, 20)],
            [cb("۳۰ دقیقه", "sk_g_30", cur, 30)],
            [cb("۱ ساعت", "sk_g_60", cur, 60)],
            [cb("هر بار ارسال کانت‌داون", "sk_g_0", cur, 0)],
            [InlineKeyboardButton("🏠 بازگشت به تنظیمات", callback_data="open_settings")],
            [InlineKeyboardButton("✖️ بستن منو", callback_data="close_menu")],
        ])
        await query.edit_message_text(f"✅ تنظیم شد!\n\nزمان‌بندی استیکر گروه: **{format_sticker_interval(minutes)}**", reply_markup=keyboard, parse_mode="Markdown")
        return

# ========== /random COMMAND ==========
async def random_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    chat_id = chat.id
    reply_to = None

    if chat.type in ("group", "supergroup") and chat.id not in groups:
        groups.add(chat.id)
        save_json(GROUPS_FILE, list(groups))

    if chat.type in ("group", "supergroup"):
        allowed, msg = check_random_rate(chat_id, user.id)
        if not allowed:
            await update.message.reply_text(msg, parse_mode="Markdown")
            return
        if update.message and update.message.reply_to_message:
            reply_to = update.message.reply_to_message.message_id
        else:
            reply_to = pop_random_recent(chat_id)
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
        msg += (
            f"\n\n**وضعیت پیوی شما:** {status_text}\n"
            f"**زمان‌بندی:** {format_interval(s['countdown_hours'])}\n"
            f"**استیکر:** {format_sticker_interval(s['sticker_minutes'])}\n\n"
            "برای تغییر از /settings استفاده کن."
        )
    await update.message.reply_text(msg, parse_mode="Markdown")

# ========== AUTO-REPLY + MESSAGE TRACKING ==========
async def track_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track ALL group messages + auto-reply to bot replies with sticker."""
    message = update.message
    chat = update.effective_chat
    if not message or chat.type not in ("group", "supergroup"):
        return

    chat_id = chat.id

    # Track every message in the group (last 7)
    track_group_message(chat_id, message.message_id)

    # Check auto-reply limit (max 2 per chat, resets after 1 hour)
    chat_id = chat.id
    now = time.time()
    if chat_id not in auto_reply_counter:
        auto_reply_counter[chat_id] = {"count": 0, "reset_at": 0}
    arc = auto_reply_counter[chat_id]
    if now - arc["reset_at"] > 3600:
        arc["count"] = 0
        arc["reset_at"] = now

    # If someone replied to a bot message → send random sticker (with limit)
    if message.reply_to_message:
        bot_id = context.bot_data.get("bot_id")
        if bot_id is None:
            bot_id = (await context.bot.get_me()).id
            context.bot_data["bot_id"] = bot_id
        replied_to = message.reply_to_message
        if replied_to.from_user and replied_to.from_user.id == bot_id:
            sk_enabled = get_group_settings(chat_id).get("sticker_enabled", True)
            if arc["count"] < AUTO_REPLY_LIMIT and sk_enabled:
                arc["count"] += 1
                await send_random_sticker(context.bot, chat_id, reply_to=message.message_id)

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
                ep = get_current_episode()
                await context.bot.send_message(chat_id=chat.id, text=f"ممنون که منو اضافه کردی! 🎉\nهر ۳ ساعت کانت‌داون قسمت {ep} Re:Zero رو می‌فرستم.")
            except:
                pass
    elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
        if chat.type in ("group", "supergroup"):
            groups.discard(chat.id)
            save_json(GROUPS_FILE, list(groups))
            recent_group_messages.pop(chat.id, None)
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
    me = await application.bot.get_me()
    application.bot_data["bot_id"] = me.id
    print(f"Bot: @{me.username} (id={me.id})")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(smart_scheduler, IntervalTrigger(minutes=10), args=[application.bot], id="smart_scheduler", replace_existing=True)
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
    application.add_handler(CommandHandler("togglecountdown", toggle_countdown))
    application.add_handler(CommandHandler("togglesticker", toggle_sticker))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.REPLY & filters.ChatType.GROUPS, track_and_reply))
    application.add_handler(CallbackQueryHandler(button_callback))

    application.post_init = post_init
    application.post_shutdown = post_shutdown

    print("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
