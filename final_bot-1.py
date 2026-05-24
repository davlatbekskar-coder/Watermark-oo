# ============================================================
# KERAK BOT — To'liq birlashtirилган bot (YAKUNIY VERSIYA)
# Texnologiya: Python 3.12 + aiogram 3.x + SQLite + FSM
# pip install aiogram yt-dlp deep-translator qrcode[pil] gtts Pillow
# ============================================================

import asyncio
import logging
import sqlite3
import io
import math
import os
import re
from datetime import datetime, date
from typing import Optional

import qrcode
from gtts import gTTS
from PIL import Image
from deep_translator import GoogleTranslator

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    BufferedInputFile
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# ============================================================
# SOZLAMALAR — O'zgartiring!
# ============================================================

BOT_TOKEN              = "YOUR_BOT_TOKEN_HERE"
ADMIN_IDS              = [123456789]           # O'z Telegram ID'ingizni yozing
BOT_USERNAME           = "your_bot_username"   # Botingiz username'i (@siz)
ADMIN_USERNAME         = "@your_admin"         # Admin username'i
DB_NAME                = "kerak_bot.db"
REFERRAL_PREMIUM_COUNT = 25
FLOOD_LIMIT            = 5

# Kunlik limitlar (oddiy foydalanuvchi)
LIMITS = {
    "password":   7,
    "qr":         5,
    "scientific": 10,
    "tts":        10,
    "compress":   5,
    "watermark":  5,
}

REFERRAL_BONUS = {
    "password":   (5, 5),
    "qr":         (5, 5),
    "scientific": (10, 10),
    "tts":        (10, 10),
    "compress":   (5, 10),
    "watermark":  (5, 5),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("KerakBot")

# ============================================================
# DATABASE
# ============================================================

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db_connect()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id        INTEGER PRIMARY KEY,
        username       TEXT,
        full_name      TEXT,
        joined_at      TEXT DEFAULT (date('now')),
        is_blocked     INTEGER DEFAULT 0,
        ref_by         INTEGER DEFAULT NULL,
        referral_count INTEGER DEFAULT 0,
        is_premium     INTEGER DEFAULT 0,
        special_start_msg TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS movies (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT UNIQUE NOT NULL,
        title       TEXT NOT NULL,
        file_id     TEXT NOT NULL,
        is_premium  INTEGER DEFAULT 0,
        added_at    TEXT DEFAULT (date('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS premium_users (
        user_id   INTEGER PRIMARY KEY,
        added_at  TEXT DEFAULT (datetime('now')),
        added_by  INTEGER
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS referrals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id  INTEGER NOT NULL,
        invited_id  INTEGER NOT NULL,
        joined_at   TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT
    )""")

    defaults = [
        ("channel",     "@yourchannel"),
        ("force_sub",   "1"),
        ("ads_enabled", "0"),
        ("ads_text",    ""),
    ]
    for key, val in defaults:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

    c.execute("""CREATE TABLE IF NOT EXISTS flood (
        user_id   INTEGER PRIMARY KEY,
        count     INTEGER DEFAULT 0,
        last_time TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS passwords (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        label      TEXT,
        password   TEXT,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS usage_stats (
        user_id    INTEGER,
        feature    TEXT,
        used_today INTEGER DEFAULT 0,
        last_reset TEXT,
        PRIMARY KEY (user_id, feature)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS broadcast_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        message    TEXT,
        sent_at    TEXT,
        sent_by    INTEGER
    )""")

    conn.commit()
    conn.close()
    log.info("Database initialized.")

# ============================================================
# DATABASE YORDAMCHI FUNKSIYALAR
# ============================================================

def get_setting(key: str) -> str:
    conn = db_connect()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else ""

def set_setting(key: str, value: str):
    conn = db_connect()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def add_user(user_id: int, username: str, full_name: str, ref_by: Optional[int] = None):
    conn = db_connect()
    existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO users (user_id, username, full_name, ref_by) VALUES (?, ?, ?, ?)",
            (user_id, username or "", full_name or "", ref_by)
        )
        if ref_by and ref_by != user_id:
            already = conn.execute(
                "SELECT id FROM referrals WHERE inviter_id=? AND invited_id=?", (ref_by, user_id)
            ).fetchone()
            if not already:
                conn.execute(
                    "INSERT INTO referrals (inviter_id, invited_id) VALUES (?, ?)", (ref_by, user_id)
                )
                conn.execute(
                    "UPDATE users SET referral_count = referral_count + 1 WHERE user_id=?",
                    (ref_by,)
                )
        conn.commit()
    conn.close()

def user_exists(user_id: int) -> bool:
    conn = db_connect()
    row = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None

def mark_blocked(user_id: int, blocked: bool):
    conn = db_connect()
    conn.execute("UPDATE users SET is_blocked=? WHERE user_id=?", (1 if blocked else 0, user_id))
    conn.commit()
    conn.close()

def is_premium(user_id: int) -> bool:
    conn = db_connect()
    row = conn.execute("SELECT user_id FROM premium_users WHERE user_id=?", (user_id,)).fetchone()
    if row:
        conn.close()
        return True
    row2 = conn.execute("SELECT is_premium FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row2 is not None and row2["is_premium"] == 1

def add_premium(user_id: int, added_by: int):
    conn = db_connect()
    conn.execute(
        "INSERT OR IGNORE INTO premium_users (user_id, added_by) VALUES (?, ?)", (user_id, added_by)
    )
    conn.execute("UPDATE users SET is_premium=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def del_premium(user_id: int):
    conn = db_connect()
    conn.execute("DELETE FROM premium_users WHERE user_id=?", (user_id,))
    conn.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_premium_list() -> list:
    conn = db_connect()
    rows = conn.execute("SELECT * FROM premium_users").fetchall()
    conn.close()
    return rows

def get_all_users() -> list:
    conn = db_connect()
    rows = conn.execute("SELECT user_id, username, full_name, is_blocked FROM users").fetchall()
    conn.close()
    return rows

def get_referral_count(user_id: int) -> int:
    conn = db_connect()
    row = conn.execute("SELECT COUNT(*) FROM referrals WHERE inviter_id=?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else 0

def get_top_referrals(limit: int = 10) -> list:
    conn = db_connect()
    rows = conn.execute("""
        SELECT inviter_id, COUNT(*) as cnt
        FROM referrals GROUP BY inviter_id
        ORDER BY cnt DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return rows

def get_stats() -> dict:
    conn = db_connect()
    today = date.today().isoformat()
    stats = {
        "total_users":   conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "premium_users": conn.execute("SELECT COUNT(*) FROM premium_users").fetchone()[0],
        "total_movies":  conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0],
        "today_users":   conn.execute("SELECT COUNT(*) FROM users WHERE joined_at=?", (today,)).fetchone()[0],
        "blocked":       conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked=1").fetchone()[0],
        "today_movies":  conn.execute("SELECT COUNT(*) FROM movies WHERE added_at=?", (today,)).fetchone()[0],
    }
    conn.close()
    return stats

def get_movie(code: str):
    conn = db_connect()
    row = conn.execute("SELECT * FROM movies WHERE code=?", (code,)).fetchone()
    conn.close()
    return row

def add_movie(code: str, title: str, file_id: str, is_prem: int):
    conn = db_connect()
    conn.execute(
        "INSERT OR REPLACE INTO movies (code, title, file_id, is_premium) VALUES (?, ?, ?, ?)",
        (code, title, file_id, is_prem)
    )
    conn.commit()
    conn.close()

def delete_movie(code: str) -> bool:
    conn = db_connect()
    cur = conn.execute("DELETE FROM movies WHERE code=?", (code,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0

def search_movies(query: str) -> list:
    conn = db_connect()
    rows = conn.execute(
        "SELECT * FROM movies WHERE title LIKE ? OR code LIKE ? LIMIT 10",
        (f"%{query}%", f"%{query}%")
    ).fetchall()
    conn.close()
    return rows

def flood_check(user_id: int) -> bool:
    conn = db_connect()
    now = datetime.now()
    row = conn.execute("SELECT count, last_time FROM flood WHERE user_id=?", (user_id,)).fetchone()
    if row:
        last = datetime.fromisoformat(row["last_time"])
        diff = (now - last).total_seconds()
        if diff < 60:
            if row["count"] >= FLOOD_LIMIT:
                conn.close()
                return True
            conn.execute("UPDATE flood SET count=count+1, last_time=? WHERE user_id=?",
                         (now.isoformat(), user_id))
        else:
            conn.execute("UPDATE flood SET count=1, last_time=? WHERE user_id=?",
                         (now.isoformat(), user_id))
    else:
        conn.execute("INSERT INTO flood (user_id, count, last_time) VALUES (?, 1, ?)",
                     (user_id, now.isoformat()))
    conn.commit()
    conn.close()
    return False

def get_limit(user_id: int, feature: str) -> int:
    base = LIMITS.get(feature, 5)
    if is_premium(user_id):
        return 9999
    ref_count = get_referral_count(user_id)
    needed, bonus = REFERRAL_BONUS.get(feature, (5, 0))
    extra = (ref_count // needed) * bonus if needed > 0 else 0
    return base + extra

def get_used_today(user_id: int, feature: str) -> int:
    conn = db_connect()
    today = datetime.now().strftime("%Y-%m-%d")
    c = conn.cursor()
    c.execute("SELECT used_today, last_reset FROM usage_stats WHERE user_id=? AND feature=?",
              (user_id, feature))
    row = c.fetchone()
    if not row or row["last_reset"] != today:
        c.execute("""INSERT OR REPLACE INTO usage_stats (user_id, feature, used_today, last_reset)
                     VALUES (?,?,0,?)""", (user_id, feature, today))
        conn.commit()
        conn.close()
        return 0
    conn.close()
    return row["used_today"]

def increment_usage(user_id: int, feature: str):
    conn = db_connect()
    today = datetime.now().strftime("%Y-%m-%d")
    conn.execute("""INSERT OR REPLACE INTO usage_stats (user_id, feature, used_today, last_reset)
                 VALUES (?, ?, COALESCE((SELECT used_today FROM usage_stats
                 WHERE user_id=? AND feature=? AND last_reset=?),0)+1, ?)""",
              (user_id, feature, user_id, feature, today, today))
    conn.commit()
    conn.close()

def check_limit(user_id: int, feature: str) -> bool:
    used = get_used_today(user_id, feature)
    limit = get_limit(user_id, feature)
    return used >= limit

def get_passwords(user_id: int) -> list:
    conn = db_connect()
    rows = conn.execute(
        "SELECT label, password, created_at FROM passwords WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    return rows

# ============================================================
# FSM STATES
# ============================================================

class AddMovieStates(StatesGroup):
    waiting_video   = State()
    waiting_title   = State()
    waiting_code    = State()
    waiting_premium = State()

class DeleteMovieState(StatesGroup):
    waiting_code = State()

class SearchMovieState(StatesGroup):
    waiting_query = State()

class BroadcastState(StatesGroup):
    waiting_message = State()

class SettingsState(StatesGroup):
    waiting_channel  = State()
    waiting_ads_text = State()

class PremiumState(StatesGroup):
    waiting_action = State()

class QRState(StatesGroup):
    waiting_text = State()

class TTSState(StatesGroup):
    waiting_text = State()

class SciCalcState(StatesGroup):
    waiting_expr = State()

class CompressState(StatesGroup):
    waiting_photo = State()

class PasswordState(StatesGroup):
    waiting_label    = State()
    waiting_password = State()

class WatermarkState(StatesGroup):
    waiting_url = State()

class ContactAdminState(StatesGroup):
    waiting_message = State()

class SpecialMsgState(StatesGroup):
    waiting_id      = State()
    waiting_message = State()

class FindUserState(StatesGroup):
    waiting_id = State()

# ============================================================
# KLAVIATURALAR
# ============================================================

def main_menu_kb() -> ReplyKeyboardMarkup:
    """Asosiy menyu — Tarjimon va Lug'at o'chirildi"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🧮 Hisob-kitob"),   KeyboardButton(text="🛠 Foydali botlar")],
            [KeyboardButton(text="🔐 Parollar"),       KeyboardButton(text="👥 Do'stlarni taklif")],
            [KeyboardButton(text="📊 Mening hisobim")],
        ],
        resize_keyboard=True
    )

def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Statistika",     callback_data="stat"),
            InlineKeyboardButton(text="🎬 Video qo'shish", callback_data="add_movie"),
        ],
        [
            InlineKeyboardButton(text="🗑 Video o'chirish", callback_data="del_movie"),
            InlineKeyboardButton(text="🔎 Video qidirish",  callback_data="search_movie"),
        ],
        [
            InlineKeyboardButton(text="📢 Tarqatish",  callback_data="broadcast"),
            InlineKeyboardButton(text="⭐ Premium",    callback_data="premium_panel"),
        ],
        [
            InlineKeyboardButton(text="👥 Userlar",   callback_data="users_panel"),
            InlineKeyboardButton(text="🎁 Referal",   callback_data="referral_panel"),
        ],
        [
            InlineKeyboardButton(text="⚙ Sozlamalar", callback_data="settings_panel"),
        ],
    ])

def back_kb(cb: str = "admin_home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data=cb)]
    ])

def sub_check_kb(channel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Kanalga obuna bo'lish",
            url=f"https://t.me/{channel.lstrip('@')}"
        )],
        [InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data="check_sub")],
    ])

def premium_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Premium qo'shish",  callback_data="add_premium")],
        [InlineKeyboardButton(text="➖ Premium o'chirish", callback_data="del_premium")],
        [InlineKeyboardButton(text="📋 Premium list",      callback_data="list_premium")],
        [InlineKeyboardButton(text="◀️ Orqaga",           callback_data="admin_home")],
    ])

def settings_panel_kb() -> InlineKeyboardMarkup:
    force = get_setting("force_sub")
    ads   = get_setting("ads_enabled")
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanal username",  callback_data="set_channel")],
        [InlineKeyboardButton(
            text=f"🔒 Majburiy obuna: {'✅' if force == '1' else '❌'}",
            callback_data="toggle_force_sub"
        )],
        [InlineKeyboardButton(
            text=f"📣 Reklama: {'✅' if ads == '1' else '❌'}",
            callback_data="toggle_ads"
        )],
        [InlineKeyboardButton(text="✏️ Reklama matni",  callback_data="set_ads_text")],
        [InlineKeyboardButton(text="◀️ Orqaga",         callback_data="admin_home")],
    ])

def useful_bots_kb() -> InlineKeyboardMarkup:
    """Foydali botlar menyusi — Instagram link o'chirildi, Watermark qo'shildi"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📷 QR Generator",      callback_data="qr_gen"),
            InlineKeyboardButton(text="🔬 Ilmiy hisob",       callback_data="sci_calc"),
        ],
        [
            InlineKeyboardButton(text="🔊 Matn → Tovush",     callback_data="tts"),
            InlineKeyboardButton(text="🗜 Rasm siqish",       callback_data="compress"),
        ],
        [
            InlineKeyboardButton(text="🚫 Watermark Remover", callback_data="watermark"),
        ],
        [
            InlineKeyboardButton(text="📩 Admin bilan bog'lanish", callback_data="contact_admin"),
        ],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")],
    ])

# ============================================================
# ADMIN FILTER
# ============================================================

class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in ADMIN_IDS

class IsAdminCB(BaseFilter):
    async def __call__(self, call: CallbackQuery) -> bool:
        return call.from_user.id in ADMIN_IDS

# ============================================================
# OBUNA TEKSHIRUV
# ============================================================

async def check_subscription(bot: Bot, user_id: int) -> bool:
    if get_setting("force_sub") != "1":
        return True
    channel = get_setting("channel")
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return True

# ============================================================
# MA'LUMOTLAR
# ============================================================

SCIENCE_HELP = (
    "🔬 <b>Ilmiy hisob-kitob</b>\n\n"
    "Funksiyalar:\n"
    "• <code>sin(x)</code>, <code>cos(x)</code>, <code>tan(x)</code>\n"
    "• <code>log(x)</code>, <code>log10(x)</code>, <code>sqrt(x)</code>\n"
    "• <code>pi</code>, <code>e</code> — konstantalar\n"
    "• <code>x**y</code> — daraja\n"
    "• <code>abs(x)</code>, <code>factorial(x)</code>\n\n"
    "Misol: <code>sin(pi/2) + log(100)</code>"
)

WATERMARK_PLATFORMS = [
    "tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "youtube.com/shorts", "youtu.be",
    "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.watch",
    "instagram.com",
]

# ============================================================
# ROUTERLAR
# ============================================================

user_router  = Router()
admin_router = Router()

# ============================================================
# USER — START
# ============================================================

@user_router.message(CommandStart())
async def start_handler(message: Message, bot: Bot):
    user_id   = message.from_user.id
    username  = message.from_user.username or ""
    full_name = message.from_user.full_name or ""

    args   = message.text.split()
    ref_by = None
    if len(args) > 1:
        try:
            ref_by = int(args[1])
            if ref_by == user_id:
                ref_by = None
        except ValueError:
            ref_by = None

    add_user(user_id, username, full_name, ref_by)

    # Referal premium tekshiruv
    if ref_by and ref_by != user_id:
        ref_count = get_referral_count(ref_by)
        if ref_count >= REFERRAL_PREMIUM_COUNT and not is_premium(ref_by):
            add_premium(ref_by, added_by=0)
            try:
                await bot.send_message(
                    ref_by,
                    f"🎉 Tabriklaymiz! {REFERRAL_PREMIUM_COUNT} ta do'stingizni taklif qildingiz.\n"
                    "⭐ Sizga <b>Premium</b> status berildi!",
                )
            except Exception:
                pass

    # Obuna tekshiruv
    if not await check_subscription(bot, user_id):
        channel = get_setting("channel")
        await message.answer(
            "📢 Botdan foydalanish uchun kanalimizga obuna bo'lishingiz kerak.",
            reply_markup=sub_check_kb(channel)
        )
        return

    # Maxsus start xabari
    conn = db_connect()
    row = conn.execute("SELECT special_start_msg FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    if row and row["special_start_msg"]:
        await message.answer(row["special_start_msg"])

    star = "⭐ " if is_premium(user_id) else ""
    await message.answer(
        f"👋 Salom, <b>{star}{full_name}</b>!\n\n"
        "🤖 <b>Kerak Bot</b>ga xush kelibsiz!\n\n"
        "📌 Quyidagi bo'limlardan birini tanlang:",
        reply_markup=main_menu_kb()
    )

@user_router.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery, bot: Bot):
    if await check_subscription(bot, call.from_user.id):
        await call.message.edit_text("✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
        await call.message.answer(
            "📌 Quyidagi bo'limlardan birini tanlang:",
            reply_markup=main_menu_kb()
        )
    else:
        await call.answer("❌ Siz hali obuna bo'lmagansiz!", show_alert=True)

# ============================================================
# USER — ASOSIY MENU HANDLERLARI
# ============================================================

@user_router.message(F.text == "🧮 Hisob-kitob")
async def calc_menu(message: Message):
    await message.answer(
        "🧮 <b>Hisob-kitob</b>\n\n"
        "Misol yozish uchun:\n"
        "<code>Yechib ber -69+69÷69</code>\n\n"
        "Amallar: <code>+</code>, <code>-</code>, <code>×</code>, "
        "<code>÷</code>, <code>^</code> (daraja), <code>√</code> (ildiz)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]
        ])
    )

@user_router.message(F.text == "🛠 Foydali botlar")
async def useful_bots_menu(message: Message):
    await message.answer(
        "🛠 <b>Foydali botlar</b>\n\nBo'limni tanlang:",
        reply_markup=useful_bots_kb()
    )

@user_router.message(F.text == "🔐 Parollar")
async def passwords_menu(message: Message):
    await show_password_menu_msg(message)

@user_router.message(F.text == "👥 Do'stlarni taklif")
async def referral_menu(message: Message, bot: Bot):
    await show_referral_menu_msg(message, bot)

@user_router.message(F.text == "📊 Mening hisobim")
async def my_account(message: Message, bot: Bot):
    user_id   = message.from_user.id
    ref_count = get_referral_count(user_id)
    premium_status = "✅ Premium" if is_premium(user_id) else "❌ Oddiy"
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={user_id}"
    needed = max(0, REFERRAL_PREMIUM_COUNT - ref_count)
    await message.answer(
        f"📊 <b>Mening hisobim</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"👑 Status: {premium_status}\n"
        f"👥 Taklif qilganlar: {ref_count} ta\n"
        + (f"📌 Premiumga {needed} ta do'st qoldi\n" if not is_premium(user_id) else "") +
        f"\n🔗 Mening havolam:\n<code>{ref_link}</code>"
    )

# ============================================================
# USER — CALLBACK HANDLERLARI
# ============================================================

@user_router.callback_query(F.data == "back_main")
async def back_main_cb(call: CallbackQuery):
    await call.message.answer("🏠 Asosiy menyu:", reply_markup=main_menu_kb())
    try:
        await call.message.delete()
    except Exception:
        pass

@user_router.callback_query(F.data == "back_useful")
async def back_useful_cb(call: CallbackQuery):
    await call.message.edit_text(
        "🛠 <b>Foydali botlar</b>\n\nBo'limni tanlang:",
        reply_markup=useful_bots_kb()
    )

@user_router.callback_query(F.data == "back_passwords")
async def back_passwords_cb(call: CallbackQuery):
    await show_password_menu_cb(call)

# ============================================================
# USER — QR GENERATOR
# ============================================================

@user_router.callback_query(F.data == "qr_gen")
async def qr_gen_cb(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if check_limit(user_id, "qr"):
        limit = get_limit(user_id, "qr")
        await call.message.edit_text(
            f"❌ QR limit tugadi! (Kunlik {limit} ta)\n"
            "5 ta do'st taklif qiling — 5 ta qo'shimcha! 👥",
            reply_markup=back_kb("back_useful")
        )
        return
    used  = get_used_today(user_id, "qr")
    limit = get_limit(user_id, "qr")
    await state.set_state(QRState.waiting_text)
    await call.message.edit_text(
        f"📷 <b>QR Generator</b>\n\n"
        f"📊 Bugungi: {used}/{limit}\n\n"
        "QR kodga aylantirish uchun matn yuboring (link, telefon va h.k.):",
        reply_markup=back_kb("back_useful")
    )

@user_router.message(QRState.waiting_text)
async def qr_process(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if check_limit(user_id, "qr"):
        await message.answer("❌ Limit tugadi!")
        await state.clear()
        return
    increment_usage(user_id, "qr")
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(message.text)
    qr.make(fit=True)
    img = qr.make_image(fill="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Yana QR",  callback_data="qr_gen"),
            InlineKeyboardButton(text="🔙 Orqaga",   callback_data="back_useful"),
        ]
    ])
    await message.answer_photo(
        BufferedInputFile(buf.getvalue(), filename="qr.png"),
        caption=f"✅ QR kod tayyor!\n\n📝 Matn: <code>{message.text}</code>",
        reply_markup=kb
    )
    await state.clear()

# ============================================================
# USER — ILMIY HISOB
# ============================================================

@user_router.callback_query(F.data == "sci_calc")
async def sci_calc_cb(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if check_limit(user_id, "scientific"):
        await call.message.edit_text(
            "❌ Limit tugadi! 10 ta do'st taklif qiling — 10+ qo'shimcha! 👥",
            reply_markup=back_kb("back_useful")
        )
        return
    used  = get_used_today(user_id, "scientific")
    limit = get_limit(user_id, "scientific")
    await state.set_state(SciCalcState.waiting_expr)
    await call.message.edit_text(
        f"📊 Bugungi: {used}/{limit}\n\n" + SCIENCE_HELP,
        reply_markup=back_kb("back_useful")
    )

@user_router.message(SciCalcState.waiting_expr)
async def sci_calc_process(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if check_limit(user_id, "scientific"):
        await message.answer("❌ Limit tugadi!")
        await state.clear()
        return
    try:
        safe_dict = {
            "sin": math.sin, "cos": math.cos, "tan": math.tan,
            "log": math.log, "log10": math.log10, "log2": math.log2,
            "sqrt": math.sqrt, "pi": math.pi, "e": math.e,
            "abs": abs, "factorial": math.factorial,
        }
        result = eval(message.text, {"__builtins__": {}}, safe_dict)
        if isinstance(result, float) and result == int(result):
            result = int(result)
        increment_usage(user_id, "scientific")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Yana", callback_data="sci_calc"),
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_useful"),
            ]
        ])
        await message.answer(
            f"🔬 <b>Natija:</b>\n\n<code>{message.text}</code> = <code>{result}</code>",
            reply_markup=kb
        )
    except Exception:
        await message.answer("❌ Xato! " + SCIENCE_HELP)
    await state.clear()

# ============================================================
# USER — MATN → TOVUSH
# ============================================================

@user_router.callback_query(F.data == "tts")
async def tts_cb(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if check_limit(user_id, "tts"):
        await call.message.edit_text(
            "❌ Limit tugadi! 10 ta do'st taklif qiling — 10+ qo'shimcha! 👥",
            reply_markup=back_kb("back_useful")
        )
        return
    used  = get_used_today(user_id, "tts")
    limit = get_limit(user_id, "tts")
    await state.set_state(TTSState.waiting_text)
    await call.message.edit_text(
        f"🔊 <b>Matn → Tovush</b>\n\n"
        f"📊 Bugungi: {used}/{limit}\n\n"
        "Tovushga aylantirish uchun matn yuboring:",
        reply_markup=back_kb("back_useful")
    )

@user_router.message(TTSState.waiting_text)
async def tts_process(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if check_limit(user_id, "tts"):
        await message.answer("❌ Limit tugadi!")
        await state.clear()
        return
    try:
        tts = gTTS(text=message.text, lang="uz")
        buf = io.BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        increment_usage(user_id, "tts")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Yana",   callback_data="tts"),
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_useful"),
            ]
        ])
        await message.answer_voice(
            BufferedInputFile(buf.getvalue(), filename="audio.mp3"),
            caption=f"🔊 <code>{message.text}</code>",
            reply_markup=kb
        )
    except Exception as e:
        await message.answer(f"❌ Xatolik! Qayta urinib ko'ring. ({e})")
    await state.clear()

# ============================================================
# USER — RASM SIQISH
# ============================================================

@user_router.callback_query(F.data == "compress")
async def compress_cb(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if check_limit(user_id, "compress"):
        await call.message.edit_text(
            "❌ Limit tugadi! 5 ta do'st taklif qiling — 10+ qo'shimcha! 👥",
            reply_markup=back_kb("back_useful")
        )
        return
    used  = get_used_today(user_id, "compress")
    limit = get_limit(user_id, "compress")
    await state.set_state(CompressState.waiting_photo)
    await call.message.edit_text(
        f"🗜 <b>Rasm siqish</b>\n\n"
        f"📊 Bugungi: {used}/{limit}\n\n"
        "Siqmoqchi bo'lgan rasmni yuboring:",
        reply_markup=back_kb("back_useful")
    )

@user_router.message(CompressState.waiting_photo, F.photo)
async def compress_process(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    if check_limit(user_id, "compress"):
        await message.answer("❌ Limit tugadi!")
        await state.clear()
        return
    photo = message.photo[-1]
    file  = await bot.get_file(photo.file_id)
    raw   = io.BytesIO()
    await bot.download_file(file.file_path, destination=raw)
    raw.seek(0)
    img = Image.open(raw).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=40, optimize=True)
    compressed_size = len(out.getvalue())
    out.seek(0)
    increment_usage(user_id, "compress")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Yana siqish", callback_data="compress"),
            InlineKeyboardButton(text="🔙 Orqaga",      callback_data="back_useful"),
        ]
    ])
    await message.answer_document(
        BufferedInputFile(out.getvalue(), filename="compressed.jpg"),
        caption=(
            f"✅ Rasm siqildi!\n\n"
            f"📦 Asl: {photo.file_size // 1024} KB\n"
            f"📦 Siqilgan: {compressed_size // 1024} KB"
        ),
        reply_markup=kb
    )
    await state.clear()

@user_router.message(CompressState.waiting_photo)
async def compress_no_photo(message: Message):
    await message.answer("❌ Iltimos, rasm yuboring!")

# ============================================================
# USER — WATERMARK REMOVER
# ============================================================

@user_router.callback_query(F.data == "watermark")
async def watermark_cb(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if check_limit(user_id, "watermark"):
        limit = get_limit(user_id, "watermark")
        await call.message.edit_text(
            f"❌ Watermark limiti tugadi! (Kunlik {limit} ta)\n"
            "5 ta do'st taklif qiling — 5 ta qo'shimcha! 👥",
            reply_markup=back_kb("back_useful")
        )
        return
    used  = get_used_today(user_id, "watermark")
    limit = get_limit(user_id, "watermark")
    await state.set_state(WatermarkState.waiting_url)
    await call.message.edit_text(
        f"🚫 <b>Watermark Remover</b>\n\n"
        f"📊 Bugungi: {used}/{limit}\n\n"
        "Video havolasini yuboring:\n\n"
        "✅ Qo'llab-quvvatlanadigan platformalar:\n"
        "• TikTok\n"
        "• YouTube Shorts\n"
        "• Twitter / X\n"
        "• Facebook\n"
        "• Instagram Reels",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Watermark bo'limi", url="https://t.me/My_family_8a_bot/watermark")],
            [InlineKeyboardButton(text="◀️ Orqaga", callback_data="back_useful")],
        ])
    )

@user_router.message(WatermarkState.waiting_url)
async def watermark_process(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    url     = message.text.strip()

    is_valid = any(p in url for p in WATERMARK_PLATFORMS)
    if not url.startswith("http") or not is_valid:
        await message.answer(
            "❌ Noto'g'ri havola!\n\n"
            "TikTok, YouTube Shorts, Twitter, Facebook yoki Instagram havolasini yuboring."
        )
        return

    if check_limit(user_id, "watermark"):
        await message.answer("❌ Limit tugadi!")
        await state.clear()
        return

    loading = await message.answer("⏳ Video yuklanmoqda, kuting...")

    try:
        import yt_dlp
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "format": "mp4/bestvideo+bestaudio/best",
                "outtmpl": f"{tmpdir}/video.%(ext)s",
                "merge_output_format": "mp4",
                "postprocessors": [{
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                }],
                "extractor_args": {
                    "tiktok": {"webpage_download": ["1"]},
                },
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title") or "Video"

            video_path = None
            for f in os.listdir(tmpdir):
                if f.endswith((".mp4", ".webm", ".mkv")):
                    video_path = os.path.join(tmpdir, f)
                    break

            if not video_path:
                raise FileNotFoundError("Video fayl topilmadi")

            file_size = os.path.getsize(video_path)

            if file_size > 50 * 1024 * 1024:
                await loading.delete()
                await message.answer(
                    "❌ Video hajmi juda katta (50MB dan oshadi).\n"
                    "Qisqaroq video yuboring."
                )
                await state.clear()
                return

            with open(video_path, "rb") as vf:
                video_data = vf.read()

        increment_usage(user_id, "watermark")
        await loading.delete()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Yana",   callback_data="watermark"),
                InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_useful"),
            ]
        ])

        await message.answer_video(
            BufferedInputFile(video_data, filename="video.mp4"),
            caption=f"✅ <b>Watermarksiz video!</b>\n\n📌 {title[:100]}",
            reply_markup=kb
        )

    except Exception as e:
        try:
            await loading.delete()
        except Exception:
            pass
        log.error(f"Watermark xatosi: {e}")
        await message.answer(
            "❌ Video yuklab olishda xatolik!\n\n"
            "Sabab:\n"
            "• Havola eskirgan yoki noto'g'ri\n"
            "• Video yopiq/xususiy\n"
            "• Platform bloklangan\n\n"
            "Boshqa havola bilan sinab ko'ring."
        )
    await state.clear()

# ============================================================
# USER — ADMIN BILAN BOG'LANISH
# ============================================================

@user_router.callback_query(F.data == "contact_admin")
async def contact_admin_cb(call: CallbackQuery, state: FSMContext):
    await state.set_state(ContactAdminState.waiting_message)
    await call.message.edit_text(
        f"📩 <b>Admin bilan bog'lanish</b>\n\n"
        f"Xabaringizni yozing, admin {ADMIN_USERNAME} ko'radi:",
        reply_markup=back_kb("back_useful")
    )

@user_router.message(ContactAdminState.waiting_message)
async def contact_admin_msg(message: Message, state: FSMContext, bot: Bot):
    admin_id = ADMIN_IDS[0]
    user     = message.from_user
    msg = (
        f"📩 <b>Yangi xabar!</b>\n\n"
        f"👤 Foydalanuvchi: {user.full_name}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📝 Xabar:\n{message.text}"
    )
    try:
        await bot.send_message(admin_id, msg)
    except Exception:
        pass
    await message.answer(
        "✅ Xabaringiz adminga yuborildi!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]
        ])
    )
    await state.clear()

# ============================================================
# USER — PAROLLAR
# ============================================================

async def show_password_menu_msg(message: Message):
    user_id   = message.from_user.id
    passwords = get_passwords(user_id)
    limit     = get_limit(user_id, "password")
    text = f"🔐 <b>Parollar</b> ({len(passwords)}/{limit})\n\n"
    if passwords:
        for i, row in enumerate(passwords, 1):
            text += f"{i}. 🏷 <b>{row['label']}</b>\n<code>{row['password']}</code>\n\n"
    else:
        text += "📭 Hali parol saqlanmagan\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi parol saqlash", callback_data="add_password")],
        [InlineKeyboardButton(text="🗑 Barchasini o'chirish", callback_data="clear_passwords")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")],
    ])
    await message.answer(text, reply_markup=kb)

async def show_password_menu_cb(call: CallbackQuery):
    user_id   = call.from_user.id
    passwords = get_passwords(user_id)
    limit     = get_limit(user_id, "password")
    text = f"🔐 <b>Parollar</b> ({len(passwords)}/{limit})\n\n"
    if passwords:
        for i, row in enumerate(passwords, 1):
            text += f"{i}. 🏷 <b>{row['label']}</b>\n<code>{row['password']}</code>\n\n"
    else:
        text += "📭 Hali parol saqlanmagan\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Yangi parol saqlash", callback_data="add_password")],
        [InlineKeyboardButton(text="🗑 Barchasini o'chirish", callback_data="clear_passwords")],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

@user_router.callback_query(F.data == "add_password")
async def add_password_cb(call: CallbackQuery, state: FSMContext):
    await state.set_state(PasswordState.waiting_label)
    await call.message.edit_text(
        "🏷 Parol uchun nom kiriting:\nMisol: <code>Instagram</code>",
        reply_markup=back_kb("back_passwords")
    )

@user_router.message(PasswordState.waiting_label)
async def password_label_handler(message: Message, state: FSMContext):
    await state.update_data(label=message.text)
    await state.set_state(PasswordState.waiting_password)
    await message.answer("🔑 Parolni kiriting:")

@user_router.message(PasswordState.waiting_password)
async def password_value_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data    = await state.get_data()
    label   = data.get("label", "Nomsiz")

    conn = db_connect()
    count = conn.execute("SELECT COUNT(*) FROM passwords WHERE user_id=?", (user_id,)).fetchone()[0]
    limit = get_limit(user_id, "password")

    if count >= limit:
        conn.close()
        await message.answer(
            f"❌ Parol limiti {limit} ta!\n"
            "Ko'proq joy uchun do'stlarni taklif qiling 👥"
        )
        await state.clear()
        return

    conn.execute(
        "INSERT INTO passwords (user_id, label, password, created_at) VALUES (?,?,?,?)",
        (user_id, label, message.text, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer(
        f"✅ Parol saqlandi!\n\n🏷 Nom: <b>{label}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Parollarga qaytish", callback_data="back_passwords")]
        ])
    )

@user_router.callback_query(F.data == "clear_passwords")
async def clear_passwords_cb(call: CallbackQuery):
    conn = db_connect()
    conn.execute("DELETE FROM passwords WHERE user_id=?", (call.from_user.id,))
    conn.commit()
    conn.close()
    await call.message.edit_text(
        "✅ Barcha parollar o'chirildi!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main")]
        ])
    )

# ============================================================
# USER — REFERAL MENYUSI
# ============================================================

async def show_referral_menu_msg(message: Message, bot: Bot):
    user_id   = message.from_user.id
    ref_count = get_referral_count(user_id)
    me        = await bot.get_me()
    ref_link  = f"https://t.me/{me.username}?start={user_id}"
    text = (
        f"👥 <b>Do'stlarni taklif qilish</b>\n\n"
        f"📊 Taklif qilganlar: <b>{ref_count}</b> ta\n\n"
        f"🎁 <b>Bonuslar:</b>\n"
        f"• 5 ta taklif → Parol: +5 joy\n"
        f"• 5 ta taklif → QR: +5 ta\n"
        f"• 10 ta taklif → Ilmiy hisob: +10 ta\n"
        f"• 10 ta taklif → TTS: +10 ta\n"
        f"• 5 ta taklif → Rasm siqish: +10 ta\n"
        f"• 5 ta taklif → Watermark: +5 ta\n"
        f"• {REFERRAL_PREMIUM_COUNT} ta taklif → ⭐ <b>Premium!</b>\n\n"
        f"🔗 Sizning havolangiz:\n<code>{ref_link}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📤 Havolani ulashish",
            url=f"https://t.me/share/url?url={ref_link}&text=Bu%20botga%20qo%27shiling!"
        )],
        [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")],
    ])
    await message.answer(text, reply_markup=kb)

# ============================================================
# USER — HISOB-KITOB (TEXT HANDLER)
# ============================================================

@user_router.message(F.text.lower().startswith("yechib ber"))
async def calc_handler(message: Message):
    expr = message.text[10:].strip()
    try:
        expr_clean = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
        expr_clean = re.sub(r'√(\d+)', r'math.sqrt(\1)', expr_clean)
        result     = eval(expr_clean, {"__builtins__": {}, "math": math})
        if isinstance(result, float) and result == int(result):
            result = int(result)
        await message.answer(
            f"🧮 <b>Natija:</b>\n\n<code>{expr}</code> = <code>{result}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]
            ])
        )
    except Exception:
        await message.answer(
            "❌ Noto'g'ri misol!\n\nMisol: <code>Yechib ber 5+3*2</code>"
        )

# ============================================================
# USER — VIDEO KODI HANDLER
# ============================================================

@user_router.message(F.text & ~F.text.startswith("/"))
async def movie_code_handler(message: Message, bot: Bot):
    user_id = message.from_user.id

    if flood_check(user_id):
        await message.answer("⚠️ Juda ko'p so'rov yubordingiz. Biroz kuting.")
        return

    if not await check_subscription(bot, user_id):
        channel = get_setting("channel")
        await message.answer(
            "📢 Video olish uchun kanalimizga obuna bo'ling.",
            reply_markup=sub_check_kb(channel)
        )
        return

    code  = message.text.strip()
    movie = get_movie(code)

    if not movie:
        await message.answer("❌ Video topilmadi. Kodni tekshiring.")
        return

    if movie["is_premium"] and not is_premium(user_id):
        await message.answer(
            "⭐ Bu video faqat <b>Premium</b> foydalanuvchilar uchun.\n\n"
            f"{REFERRAL_PREMIUM_COUNT} ta do'st taklif qilib premium oling.\n"
            f"🔗 <code>https://t.me/{BOT_USERNAME}?start={user_id}</code>"
        )
        return

    try:
        loading = await message.answer("⏳ Yuklanmoqda...")
        await bot.send_video(
            chat_id=user_id,
            video=movie["file_id"],
            caption=f"🎬 <b>{movie['title']}</b>\n📌 Kod: <code>{movie['code']}</code>",
        )
        await loading.delete()

        if get_setting("ads_enabled") == "1":
            ads_text = get_setting("ads_text")
            if ads_text:
                await message.answer(ads_text)
    except Exception as e:
        log.error(f"Video yuborishda xato: {e}")
        await message.answer("❌ Video yuborishda xato yuz berdi.")

# ============================================================
# ADMIN HANDLERLARI
# ============================================================

@admin_router.message(Command("admin"), IsAdmin())
async def admin_cmd(message: Message):
    await message.answer(
        "🔐 <b>Admin Panel</b>",
        reply_markup=admin_panel_kb()
    )

@admin_router.callback_query(F.data == "admin_home", IsAdminCB())
async def admin_home(call: CallbackQuery):
    await call.message.edit_text(
        "🔐 <b>Admin Panel</b>",
        reply_markup=admin_panel_kb()
    )

# -- Statistika --

@admin_router.callback_query(F.data == "stat", IsAdminCB())
async def stat_panel(call: CallbackQuery):
    s = get_stats()
    text = (
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami userlar:    <b>{s['total_users']}</b>\n"
        f"⭐ Premium userlar: <b>{s['premium_users']}</b>\n"
        f"🎬 Jami videolar:   <b>{s['total_movies']}</b>\n"
        f"📅 Bugungi userlar: <b>{s['today_users']}</b>\n"
        f"🚫 Block qilganlar: <b>{s['blocked']}</b>\n"
        f"🎞 Bugungi videolar: <b>{s['today_movies']}</b>"
    )
    await call.message.edit_text(text, reply_markup=back_kb())

# -- Video qo'shish --

@admin_router.callback_query(F.data == "add_movie", IsAdminCB())
async def add_movie_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddMovieStates.waiting_video)
    await call.message.edit_text(
        "🎬 <b>Video qo'shish</b>\n\nVideo faylni yuboring (Telegram orqali):",
        reply_markup=back_kb()
    )

@admin_router.message(AddMovieStates.waiting_video, IsAdmin(), F.video)
async def add_movie_video(message: Message, state: FSMContext):
    await state.update_data(file_id=message.video.file_id)
    await state.set_state(AddMovieStates.waiting_title)
    await message.answer("✏️ Video nomini kiriting:")

@admin_router.message(AddMovieStates.waiting_title, IsAdmin())
async def add_movie_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AddMovieStates.waiting_code)
    await message.answer("🔑 Video kodini kiriting (masalan: 101):")

@admin_router.message(AddMovieStates.waiting_code, IsAdmin())
async def add_movie_code(message: Message, state: FSMContext):
    await state.update_data(code=message.text.strip())
    await state.set_state(AddMovieStates.waiting_premium)
    await message.answer("⭐ Premium video:\n<code>1</code> = Ha\n<code>0</code> = Yo'q\n\nKiriting:")

@admin_router.message(AddMovieStates.waiting_premium, IsAdmin())
async def add_movie_premium(message: Message, state: FSMContext):
    val = message.text.strip()
    if val not in ("0", "1"):
        await message.answer("❌ Faqat 1 yoki 0 kiriting.")
        return
    data = await state.get_data()
    try:
        add_movie(data["code"], data["title"], data["file_id"], int(val))
        await message.answer(
            f"✅ <b>Video qo'shildi!</b>\n\n"
            f"📌 Nom: {data['title']}\n"
            f"🔑 Kod: {data['code']}\n"
            f"⭐ Premium: {'Ha' if val == '1' else 'Yoq'}",
            reply_markup=back_kb()
        )
    except Exception as e:
        await message.answer(f"❌ Xato: {e}")
    await state.clear()

# -- Video o'chirish --

@admin_router.callback_query(F.data == "del_movie", IsAdminCB())
async def del_movie_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(DeleteMovieState.waiting_code)
    await call.message.edit_text(
        "🗑 <b>Video o'chirish</b>\n\nVideo kodini kiriting:",
        reply_markup=back_kb()
    )

@admin_router.message(DeleteMovieState.waiting_code, IsAdmin())
async def del_movie_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if delete_movie(code):
        await message.answer(f"✅ <code>{code}</code> kodli video o'chirildi.")
    else:
        await message.answer("❌ Bunday kodli video topilmadi.")
    await state.clear()

# -- Video qidirish --

@admin_router.callback_query(F.data == "search_movie", IsAdminCB())
async def search_movie_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(SearchMovieState.waiting_query)
    await call.message.edit_text(
        "🔎 <b>Video qidirish</b>\n\nNom yoki kod kiriting:",
        reply_markup=back_kb()
    )

@admin_router.message(SearchMovieState.waiting_query, IsAdmin())
async def search_movie_query(message: Message, state: FSMContext):
    results = search_movies(message.text.strip())
    if not results:
        await message.answer("❌ Hech narsa topilmadi.")
    else:
        text = "🔎 <b>Natijalar:</b>\n\n"
        for m in results:
            text += (
                f"🎬 <b>{m['title']}</b>\n"
                f"   Kod: <code>{m['code']}</code> | "
                f"{'⭐ Premium' if m['is_premium'] else '🆓 Oddiy'}\n\n"
            )
        await message.answer(text)
    await state.clear()

# -- Tarqatish (broadcast) --

@admin_router.callback_query(F.data == "broadcast", IsAdminCB())
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastState.waiting_message)
    await call.message.edit_text(
        "📢 <b>Tarqatish</b>\n\n"
        "Barcha foydalanuvchilarga yuboriladigan xabarni yuboring.\n\n"
        "📌 Quyidagilarni yuborish mumkin:\n"
        "• 📝 Matn (HTML qo'llab-quvvatlanadi)\n"
        "• 🖼 Rasm\n"
        "• 🎥 Video\n"
        "• 🎞 GIF\n\n"
        "Xabarni yuboring:",
    )

@admin_router.message(BroadcastState.waiting_message, IsAdmin())
async def broadcast_send(message: Message, state: FSMContext, bot: Bot):
    users   = get_all_users()
    success = 0
    blocked = 0
    total   = len(users)

    status_msg = await message.answer(f"⏳ Tarqatilmoqda... 0/{total}")

    for i, user in enumerate(users):
        try:
            await bot.copy_message(
                chat_id=user["user_id"],
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )
            success += 1
        except TelegramForbiddenError:
            mark_blocked(user["user_id"], True)
            blocked += 1
        except Exception:
            blocked += 1

        if (i + 1) % 20 == 0 or i + 1 == total:
            try:
                await status_msg.edit_text(
                    f"⏳ Tarqatilmoqda... {i+1}/{total}\n"
                    f"✅ Yuborildi: {success}\n"
                    f"❌ Block/Xato: {blocked}"
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)

    conn = db_connect()
    conn.execute(
        "INSERT INTO broadcast_log (message, sent_at, sent_by) VALUES (?,?,?)",
        (f"msg_id:{message.message_id}", datetime.now().isoformat(), message.from_user.id)
    )
    conn.commit()
    conn.close()

    await status_msg.edit_text(
        f"✅ <b>Tarqatish yakunlandi!</b>\n\n"
        f"Jami: {total}\n"
        f"✅ Yuborildi: {success}\n"
        f"❌ Block/Xato: {blocked}"
    )
    await state.clear()

@admin_router.message(Command("reklama"), IsAdmin())
async def cmd_reklama(message: Message, state: FSMContext):
    await state.set_state(BroadcastState.waiting_message)
    await message.answer("📢 Tarqatmoqchi bo'lgan xabaringizni yuboring:")

# -- Premium panel --

@admin_router.callback_query(F.data == "premium_panel", IsAdminCB())
async def premium_panel_cb(call: CallbackQuery):
    await call.message.edit_text(
        "⭐ <b>Premium boshqaruv</b>",
        reply_markup=premium_panel_kb()
    )

@admin_router.callback_query(F.data == "add_premium", IsAdminCB())
async def add_premium_cb(call: CallbackQuery, state: FSMContext):
    await state.set_state(PremiumState.waiting_action)
    await state.update_data(action="add")
    await call.message.edit_text("➕ <b>Premium qo'shish</b>\n\nUser ID kiriting:")

@admin_router.callback_query(F.data == "del_premium", IsAdminCB())
async def del_premium_cb(call: CallbackQuery, state: FSMContext):
    await state.set_state(PremiumState.waiting_action)
    await state.update_data(action="del")
    await call.message.edit_text("➖ <b>Premium o'chirish</b>\n\nUser ID kiriting:")

@admin_router.message(PremiumState.waiting_action, IsAdmin())
async def premium_action_handler(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID.")
        await state.clear()
        return

    if data["action"] == "add":
        add_premium(uid, message.from_user.id)
        await message.answer(f"✅ <b>{uid}</b> ga premium berildi.", reply_markup=back_kb("premium_panel"))
        try:
            await bot.send_message(uid, "⭐ Sizga <b>Premium</b> berildi!")
        except Exception:
            pass
    else:
        del_premium(uid)
        await message.answer(f"✅ <b>{uid}</b> dan premium olindi.", reply_markup=back_kb("premium_panel"))
        try:
            await bot.send_message(uid, "⚠️ Sizning <b>Premium</b> statusingiz bekor qilindi.")
        except Exception:
            pass
    await state.clear()

@admin_router.callback_query(F.data == "list_premium", IsAdminCB())
async def list_premium_cb(call: CallbackQuery):
    pl = get_premium_list()
    if not pl:
        await call.message.edit_text(
            "📋 Premium userlar yo'q.",
            reply_markup=back_kb("premium_panel")
        )
        return
    text = "📋 <b>Premium userlar:</b>\n\n"
    for row in pl:
        text += f"• <code>{row['user_id']}</code> — {str(row['added_at'])[:10]}\n"
    await call.message.edit_text(text, reply_markup=back_kb("premium_panel"))

@admin_router.message(Command("addpremium"), IsAdmin())
async def cmd_addpremium(message: Message, bot: Bot):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Foydalanish: /addpremium user_id")
        return
    try:
        uid = int(parts[1])
        add_premium(uid, message.from_user.id)
        await message.answer(f"✅ {uid} ga premium berildi.")
        await bot.send_message(uid, "⭐ Sizga <b>Premium</b> berildi!")
    except Exception as e:
        await message.answer(f"❌ Xato: {e}")

@admin_router.message(Command("delpremium"), IsAdmin())
async def cmd_delpremium(message: Message, bot: Bot):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Foydalanish: /delpremium user_id")
        return
    try:
        uid = int(parts[1])
        del_premium(uid)
        await message.answer(f"✅ {uid} dan premium olindi.")
        await bot.send_message(uid, "⚠️ Premiumingiz bekor qilindi.")
    except Exception as e:
        await message.answer(f"❌ Xato: {e}")

# -- Userlar paneli --

@admin_router.callback_query(F.data == "users_panel", IsAdminCB())
async def users_panel(call: CallbackQuery):
    users   = get_all_users()
    total   = len(users)
    blocked = sum(1 for u in users if u["is_blocked"])
    text = (
        f"👥 <b>Foydalanuvchilar</b>\n\n"
        f"Jami: <b>{total}</b>\n"
        f"Bloklangan: <b>{blocked}</b>\n\n"
        f"/users — ro'yxatni ko'rish\n"
        f"/finduser ID — foydalanuvchini topish"
    )
    await call.message.edit_text(text, reply_markup=back_kb())

@admin_router.message(Command("users"), IsAdmin())
async def cmd_users(message: Message):
    users = get_all_users()
    if not users:
        await message.answer("Hali foydalanuvchi yo'q.")
        return
    text = "👥 <b>Foydalanuvchilar ro'yxati:</b>\n\n"
    for u in users[:50]:
        name = u["full_name"] or u["username"] or "Nomsiz"
        blk  = " 🚫" if u["is_blocked"] else ""
        text += f"• <code>{u['user_id']}</code> — {name}{blk}\n"
    if len(users) > 50:
        text += f"\n... va yana {len(users)-50} ta"
    await message.answer(text)

@admin_router.message(Command("movies"), IsAdmin())
async def cmd_movies(message: Message):
    conn = db_connect()
    rows = conn.execute("SELECT * FROM movies ORDER BY id DESC LIMIT 30").fetchall()
    conn.close()
    if not rows:
        await message.answer("Hali video yo'q.")
        return
    text = "🎬 <b>Videolar ro'yxati:</b>\n\n"
    for m in rows:
        text += f"• <code>{m['code']}</code> — {m['title']} {'⭐' if m['is_premium'] else ''}\n"
    await message.answer(text)

# -- Referal paneli --

@admin_router.callback_query(F.data == "referral_panel", IsAdminCB())
async def referral_panel(call: CallbackQuery):
    top    = get_top_referrals(10)
    medals = ["🥇", "🥈", "🥉"]
    text   = "🎁 <b>Top Referallar</b>\n\n"
    for i, row in enumerate(top):
        icon  = medals[i] if i < 3 else f"{i+1}."
        text += f"{icon} <code>{row['inviter_id']}</code> — <b>{row['cnt']}</b> ta\n"
    if not top:
        text += "Hali referal yo'q."
    conn       = db_connect()
    total_refs = conn.execute("SELECT COUNT(*) FROM referrals").fetchone()[0]
    conn.close()
    text += f"\n📊 Jami referallar: <b>{total_refs}</b>"
    text += f"\n⭐ Premium olish uchun: <b>{REFERRAL_PREMIUM_COUNT}</b> ta taklif"
    await call.message.edit_text(text, reply_markup=back_kb())

# -- Sozlamalar --

@admin_router.callback_query(F.data == "settings_panel", IsAdminCB())
async def settings_panel(call: CallbackQuery):
    channel = get_setting("channel")
    await call.message.edit_text(
        f"⚙ <b>Sozlamalar</b>\n\nJoriy kanal: <code>{channel}</code>",
        reply_markup=settings_panel_kb()
    )

@admin_router.callback_query(F.data == "set_channel", IsAdminCB())
async def set_channel_cb(call: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_channel)
    await call.message.edit_text(
        "📢 Yangi kanal username kiriting (masalan: @mychannel):"
    )

@admin_router.message(SettingsState.waiting_channel, IsAdmin())
async def save_channel(message: Message, state: FSMContext):
    ch = message.text.strip()
    set_setting("channel", ch)
    await message.answer(
        f"✅ Kanal yangilandi: <code>{ch}</code>",
        reply_markup=back_kb("settings_panel")
    )
    await state.clear()

@admin_router.callback_query(F.data == "toggle_force_sub", IsAdminCB())
async def toggle_force_sub(call: CallbackQuery):
    current = get_setting("force_sub")
    new_val = "0" if current == "1" else "1"
    set_setting("force_sub", new_val)
    channel = get_setting("channel")
    await call.message.edit_text(
        f"⚙ <b>Sozlamalar</b>\n\n"
        f"Joriy kanal: <code>{channel}</code>\n\n"
        f"Majburiy obuna: {'✅ Yoqildi' if new_val == '1' else "❌ O'chirildi"}",
        reply_markup=settings_panel_kb()
    )

@admin_router.callback_query(F.data == "toggle_ads", IsAdminCB())
async def toggle_ads(call: CallbackQuery):
    current = get_setting("ads_enabled")
    new_val = "0" if current == "1" else "1"
    set_setting("ads_enabled", new_val)
    channel = get_setting("channel")
    await call.message.edit_text(
        f"⚙ <b>Sozlamalar</b>\n\n"
        f"Joriy kanal: <code>{channel}</code>\n\n"
        f"Reklama: {'✅ Yoqildi' if new_val == '1' else "❌ O'chirildi"}",
        reply_markup=settings_panel_kb()
    )

@admin_router.callback_query(F.data == "set_ads_text", IsAdminCB())
async def set_ads_text_cb(call: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_ads_text)
    await call.message.edit_text("✏️ Reklama matnini kiriting (HTML qo'llab-quvvatlanadi):")

@admin_router.message(SettingsState.waiting_ads_text, IsAdmin())
async def save_ads_text(message: Message, state: FSMContext):
    set_setting("ads_text", message.text or "")
    await message.answer(
        "✅ Reklama matni saqlandi.",
        reply_markup=back_kb("settings_panel")
    )
    await state.clear()

# -- Foydalanuvchi topish --

@admin_router.message(Command("finduser"), IsAdmin())
async def cmd_finduser(message: Message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("Foydalanish: /finduser user_id")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("❌ Noto'g'ri ID!")
        return
    conn = db_connect()
    row  = conn.execute(
        "SELECT user_id, username, full_name, joined_at, referral_count, is_premium FROM users WHERE user_id=?",
        (target_id,)
    ).fetchone()
    conn.close()
    if row:
        await message.answer(
            f"👤 <b>Foydalanuvchi ma'lumoti</b>\n\n"
            f"🆔 ID: <code>{row['user_id']}</code>\n"
            f"👤 Ism: {row['full_name']}\n"
            f"📛 Username: @{row['username'] or "yo'q"}\n"
            f"📅 Qo'shilgan: {str(row['joined_at'])[:10]}\n"
            f"👥 Takliflar: {row['referral_count']}\n"
            f"👑 Premium: {'✅' if row['is_premium'] else '❌'}"
        )
    else:
        await message.answer("❌ Foydalanuvchi topilmadi!")

# -- Maxsus start xabari --

@admin_router.message(Command("setmsg"), IsAdmin())
async def cmd_setmsg(message: Message, state: FSMContext):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Foydalanish: /setmsg user_id\nKeyin xabarni yuboring")
        await state.set_state(SpecialMsgState.waiting_id)
        return
    await state.set_state(SpecialMsgState.waiting_message)
    try:
        uid = int(parts[1])
        await state.update_data(target_id=uid)
        await message.answer(f"✅ ID: <code>{uid}</code>\n\nEndi maxsus xabarni yozing:")
    except ValueError:
        await message.answer("❌ Noto'g'ri ID!")
        await state.clear()

@admin_router.message(SpecialMsgState.waiting_id, IsAdmin())
async def special_msg_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
        await state.update_data(target_id=uid)
        await state.set_state(SpecialMsgState.waiting_message)
        await message.answer(f"✅ ID: <code>{uid}</code>\n\nEndi maxsus xabarni yozing:")
    except ValueError:
        await message.answer("❌ Noto'g'ri ID!")
        await state.clear()

@admin_router.message(SpecialMsgState.waiting_message, IsAdmin())
async def special_msg_save(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_id")
    conn = db_connect()
    conn.execute("UPDATE users SET special_start_msg=? WHERE user_id=?", (message.text, target_id))
    conn.commit()
    conn.close()
    await message.answer(f"✅ {target_id} uchun maxsus xabar o'rnatildi!")
    await state.clear()

# ============================================================
# MAIN
# ============================================================

async def main():
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin_router)
    dp.include_router(user_router)

    log.info("Bot ishga tushdi...")
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
