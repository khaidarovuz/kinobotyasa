"""
MiniMakerUzBot — Telegram Kino Bot Konstruktori
Barcha interfeys 100% O'zbek tilida.
State Machine arxitekturasi — ConversationHandler ishlatilmaydi.
"""

import os
import asyncio
import logging
import aiosqlite
from datetime import datetime, timedelta
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot, BotCommand
)
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters, ContextTypes
)
from telegram.error import TelegramError

# ─────────────────────────────────────────────────────────
# KONFIGURATSIYA
# ─────────────────────────────────────────────────────────
MAKER_BOT_TOKEN    = os.getenv("MAKER_BOT_TOKEN")
MAKER_ADMIN_ID     = int(os.getenv("MAKER_ADMIN_ID", "0"))
MAKER_ADMIN_USERNAME = os.getenv("MAKER_ADMIN_USERNAME", "admin")
DB_PATH = "minimaker.db"
PORT    = int(os.getenv("PORT", "9000"))

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# HTTP HEALTH CHECK (Render.com uchun)
# ─────────────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *_):
        pass

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# ─────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS maker_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            is_premium INTEGER DEFAULT 0,
            premium_expires TEXT DEFAULT NULL,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS maker_admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS maker_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE NOT NULL,
            channel_title TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS child_bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            bot_name TEXT DEFAULT '',
            bot_username TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            monthly_movie_count INTEGER DEFAULT 0,
            last_reset_date TEXT DEFAULT CURRENT_TIMESTAMP,
            start_message TEXT DEFAULT 'Assalomu alaykum, {full_name}! Kino kodini yuboring 🎬',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS child_bot_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            channel_username TEXT NOT NULL,
            channel_title TEXT DEFAULT '',
            UNIQUE(bot_id, channel_username)
        );
        CREATE TABLE IF NOT EXISTS child_bot_admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            UNIQUE(bot_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS child_movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            file_id TEXT NOT NULL,
            caption TEXT DEFAULT '',
            is_vip INTEGER DEFAULT 0,
            UNIQUE(bot_id, code)
        );
        CREATE TABLE IF NOT EXISTS child_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            is_vip INTEGER DEFAULT 0,
            vip_expires TEXT DEFAULT NULL,
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, user_id)
        );
        """)
        await db.commit()


# ─────────────────────────────────────────────────────────
# DB HELPERS
# ─────────────────────────────────────────────────────────
async def db_fetchone(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return await cur.fetchone()

async def db_fetchall(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return await cur.fetchall()

async def db_execute(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, params)
        await db.commit()

async def db_execute_lastid(sql, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.lastrowid

async def db_executemany(sqls_params):
    """Bir nechta SQL ni bitta tranzaksiyada bajarish."""
    async with aiosqlite.connect(DB_PATH) as db:
        for sql, params in sqls_params:
            await db.execute(sql, params)
        await db.commit()


# ─────────────────────────────────────────────────────────
# MAKER HELPERS
# ─────────────────────────────────────────────────────────
async def register_maker_user(user_id, username, full_name):
    await db_executemany([
        ("INSERT OR IGNORE INTO maker_users (user_id,username,full_name) VALUES (?,?,?)",
         (user_id, username, full_name)),
        ("UPDATE maker_users SET username=?,full_name=? WHERE user_id=?",
         (username, full_name, user_id)),
    ])

async def is_maker_premium(user_id: int) -> bool:
    if user_id == MAKER_ADMIN_ID:
        return True
    row = await db_fetchone(
        "SELECT is_premium,premium_expires FROM maker_users WHERE user_id=?", (user_id,))
    if not row or not row["is_premium"]:
        return False
    if row["premium_expires"]:
        if datetime.now() > datetime.fromisoformat(row["premium_expires"]):
            await db_execute("UPDATE maker_users SET is_premium=0,premium_expires=NULL WHERE user_id=?", (user_id,))
            return False
    return True

async def is_maker_admin(user_id: int) -> bool:
    if user_id == MAKER_ADMIN_ID:
        return True
    row = await db_fetchone("SELECT 1 FROM maker_admins WHERE user_id=?", (user_id,))
    return row is not None

async def get_maker_channels():
    return await db_fetchall("SELECT * FROM maker_channels")

async def get_user_bots(owner_id: int):
    return await db_fetchall("SELECT * FROM child_bots WHERE owner_id=?", (owner_id,))

async def get_bot_by_id(bot_id: int):
    return await db_fetchone("SELECT * FROM child_bots WHERE id=?", (bot_id,))

async def get_all_bots():
    return await db_fetchall("SELECT * FROM child_bots ORDER BY id DESC")


# ─────────────────────────────────────────────────────────
# CHILD HELPERS
# ─────────────────────────────────────────────────────────
async def get_child_channels(bot_id):
    return await db_fetchall("SELECT * FROM child_bot_channels WHERE bot_id=?", (bot_id,))

async def get_child_admins(bot_id):
    return await db_fetchall("SELECT * FROM child_bot_admins WHERE bot_id=?", (bot_id,))

async def is_child_admin(bot_id, user_id, owner_id) -> bool:
    if user_id in (owner_id, MAKER_ADMIN_ID):
        return True
    row = await db_fetchone(
        "SELECT 1 FROM child_bot_admins WHERE bot_id=? AND user_id=?", (bot_id, user_id))
    return row is not None

async def register_child_user(bot_id, user_id, username, full_name):
    await db_executemany([
        ("INSERT OR IGNORE INTO child_users (bot_id,user_id,username,full_name) VALUES (?,?,?,?)",
         (bot_id, user_id, username, full_name)),
        ("UPDATE child_users SET username=?,full_name=? WHERE bot_id=? AND user_id=?",
         (username, full_name, bot_id, user_id)),
    ])

async def is_child_vip(bot_id, user_id) -> bool:
    row = await db_fetchone(
        "SELECT is_vip,vip_expires FROM child_users WHERE bot_id=? AND user_id=?", (bot_id, user_id))
    if not row or not row["is_vip"]:
        return False
    if row["vip_expires"]:
        if datetime.now() > datetime.fromisoformat(row["vip_expires"]):
            await db_execute(
                "UPDATE child_users SET is_vip=0,vip_expires=NULL WHERE bot_id=? AND user_id=?",
                (bot_id, user_id))
            return False
    return True

async def can_add_movie(bot_id, owner_id) -> tuple:
    if await is_maker_premium(owner_id):
        return True, ""
    row = await db_fetchone(
        "SELECT monthly_movie_count,last_reset_date FROM child_bots WHERE id=?", (bot_id,))
    if not row:
        return False, "Bot topilmadi."
    last_reset = datetime.fromisoformat(row["last_reset_date"])
    now = datetime.now()
    if (now - last_reset).days >= 30:
        await db_execute(
            "UPDATE child_bots SET monthly_movie_count=0,last_reset_date=? WHERE id=?",
            (now.isoformat(), bot_id))
        return True, ""
    if row["monthly_movie_count"] >= 25:
        next_reset = last_reset + timedelta(days=30)
        days_left = (next_reset - now).days + 1
        return False, (
            f"❌ Oylik limit tugadi! Bepul tarif: oyiga maksimal 25 ta kino.\n"
            f"🗓 Keyingi reset: {days_left} kundan keyin\n\n"
            f"💎 Premium oling — limit yo'q!")
    return True, ""


# ─────────────────────────────────────────────────────────
# FORCE-SUB TEKSHIRISH
# ─────────────────────────────────────────────────────────
async def check_force_sub(bot: Bot, user_id: int, channels) -> list:
    not_joined = []
    for ch in channels:
        try:
            un = ch["channel_username"]
            chat_id = f"@{un}" if not un.startswith("@") else un
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined

def force_sub_kb(not_joined) -> InlineKeyboardMarkup:
    btns = [[InlineKeyboardButton(
        f"📢 @{ch['channel_username']}",
        url=f"https://t.me/{ch['channel_username'].lstrip('@')}"
    )] for ch in not_joined]
    btns.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(btns)


# ─────────────────────────────────────────────────────────
# STATE MACHINE HELPERS
# ─────────────────────────────────────────────────────────
def set_state(ctx: ContextTypes.DEFAULT_TYPE, state: str, **data):
    ctx.user_data["state"] = state
    ctx.user_data.update(data)

def clear_state(ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("state", None)

def get_state(ctx: ContextTypes.DEFAULT_TYPE) -> str:
    return ctx.user_data.get("state", "")


# ─────────────────────────────────────────────────────────
# MAKER BOT — KLAVIATURALAR
# ─────────────────────────────────────────────────────────
async def maker_main_kb(user_id: int) -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton("➕ Yangi Bot Yaratish", callback_data="create_bot")],
        [InlineKeyboardButton("🤖 Mening Botlarim", callback_data="my_bots")],
        [InlineKeyboardButton("💳 Tariflar va Premium", callback_data="tariffs")],
        [InlineKeyboardButton("👨‍💻 Admin Bilan Aloqa", callback_data="contact")],
    ]
    if await is_maker_admin(user_id):
        btns.append([InlineKeyboardButton("🎛 Maker Admin Paneli", callback_data="maker_admin")])
    return InlineKeyboardMarkup(btns)

def maker_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistika", callback_data="madm_stats")],
        [InlineKeyboardButton("➕ Premium Berish", callback_data="madm_add_premium"),
         InlineKeyboardButton("➖ Premium Olish", callback_data="madm_remove_premium")],
        [InlineKeyboardButton("👤 Admin Qo'shish", callback_data="madm_add_admin"),
         InlineKeyboardButton("🗑 Admin O'chirish", callback_data="madm_remove_admin")],
        [InlineKeyboardButton("📋 Adminlar Ro'yxati", callback_data="madm_list_admins")],
        [InlineKeyboardButton("📢 Kanal Qo'shish", callback_data="madm_add_channel"),
         InlineKeyboardButton("🗑 Kanal O'chirish", callback_data="madm_remove_channel")],
        [InlineKeyboardButton("📋 Kanallar Ro'yxati", callback_data="madm_list_channels")],
        [InlineKeyboardButton("🤖 Barcha Botlar", callback_data="madm_list_all_bots")],
        [InlineKeyboardButton("📣 Ommaviy Xabar", callback_data="madm_broadcast")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")],
    ])

CANCEL_BTN = [[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")]]


# ─────────────────────────────────────────────────────────
# MAKER BOT — /start
# ─────────────────────────────────────────────────────────
async def maker_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    clear_state(ctx)
    await register_maker_user(user.id, user.username or "", user.full_name)

    channels = await get_maker_channels()
    if channels:
        nj = await check_force_sub(ctx.bot, user.id, channels)
        if nj:
            await update.message.reply_text(
                "📢 Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:",
                reply_markup=force_sub_kb(nj))
            return

    premium = await is_maker_premium(user.id)
    tarif = "⭐ Premium" if premium else "🆓 Bepul"
    await update.message.reply_text(
        f"👋 Salom, <b>{user.full_name}</b>!\n\n"
        f"🤖 <b>MiniMakerUzBot</b> — o'z Kino Botingizni yarating!\n"
        f"💳 Tarifingiz: <b>{tarif}</b>\n\n"
        f"Menyudan bo'lim tanlang:",
        parse_mode="HTML",
        reply_markup=await maker_main_kb(user.id))


# ─────────────────────────────────────────────────────────
# MAKER BOT — XABAR ROUTERI (State Machine)
# ─────────────────────────────────────────────────────────
async def maker_message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    state = get_state(ctx)
    user = update.effective_user
    txt = (update.message.text or "").strip()

    # /bekor buyrug'i istalgan holatda bekor qiladi
    if txt == "/bekor" or txt.lower() == "bekor":
        clear_state(ctx)
        await update.message.reply_text(
            "❌ Bekor qilindi.",
            reply_markup=await maker_main_kb(user.id))
        return

    if state == "await_bot_token":
        await _handle_bot_token(update, ctx, txt)

    elif state == "madm_premium_id":
        await _madm_premium_id(update, ctx, txt)
    elif state == "madm_premium_days":
        await _madm_premium_days(update, ctx, txt)
    elif state == "madm_remove_premium":
        await _madm_remove_premium(update, ctx, txt)
    elif state == "madm_add_admin_id":
        await _madm_add_admin_id(update, ctx, txt)
    elif state == "madm_add_admin_username":
        await _madm_add_admin_username(update, ctx, txt)
    elif state == "madm_remove_admin":
        await _madm_remove_admin(update, ctx, txt)
    elif state == "madm_add_channel":
        await _madm_add_channel(update, ctx, txt)
    elif state == "madm_remove_channel":
        await _madm_remove_channel(update, ctx, txt)
    elif state == "madm_broadcast":
        await _madm_broadcast(update, ctx)


# ─────────────────────────────────────────────────────────
# MAKER BOT — CALLBACK ROUTERI
# ─────────────────────────────────────────────────────────
async def maker_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    # — Bekor qilish —
    if data == "cancel":
        clear_state(ctx)
        await query.edit_message_text(
            f"❌ Bekor qilindi.\n\nMenyudan bo'lim tanlang:",
            reply_markup=await maker_main_kb(user.id))
        return

    # — Asosiy menyu —
    if data in ("main_menu", "check_sub"):
        if data == "check_sub":
            channels = await get_maker_channels()
            nj = await check_force_sub(ctx.bot, user.id, channels)
            if nj:
                await query.edit_message_text(
                    "❌ Hali barcha kanallarga a'zo bo'lmadingiz!",
                    reply_markup=force_sub_kb(nj))
                return
        clear_state(ctx)
        premium = await is_maker_premium(user.id)
        tarif = "⭐ Premium" if premium else "🆓 Bepul"
        await query.edit_message_text(
            f"👋 <b>{user.full_name}</b> | 💳 <b>{tarif}</b>\n\nMenyudan bo'lim tanlang:",
            parse_mode="HTML",
            reply_markup=await maker_main_kb(user.id))
        return

    # — Yangi bot yaratish —
    if data == "create_bot":
        bots = await get_user_bots(user.id)
        premium = await is_maker_premium(user.id)
        limit = 10 if premium else 1
        if len(bots) >= limit:
            msg = (f"❌ Siz {len(bots)} ta bot yaratgansiz.\n"
                   f"{'Bepul tarif: maksimal 1 ta bot.' if not premium else 'Premium tarif: maksimal 10 ta bot.'}\n\n"
                   f"{'💎 Premium oling — 10 tagacha bot!' if not premium else ''}")
            await query.edit_message_text(msg,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))
            return
        set_state(ctx, "await_bot_token")
        await query.edit_message_text(
            "🤖 <b>Yangi Kino Bot Yaratish</b>\n\n"
            "1️⃣ @BotFather dan yangi bot yarating\n"
            "2️⃣ <b>HTTP API Token</b>ni nusxalab menga yuboring\n\n"
            "📝 Ko'rinish: <code>1234567890:ABC...</code>\n\n"
            "Bekor qilish uchun /bekor yozing",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    # — Mening botlarim —
    if data == "my_bots":
        await _show_my_bots(query, user)
        return

    if data.startswith("delete_bot_"):
        bot_id = int(data.split("_")[-1])
        await _delete_bot_confirm(query, bot_id)
        return

    if data.startswith("confirm_del_bot_"):
        bot_id = int(data.split("_")[-1])
        await _delete_bot_execute(query, ctx, bot_id)
        return

    # — Tariflar —
    if data == "tariffs":
        await _show_tariffs(query, user)
        return

    # — Aloqa —
    if data == "contact":
        await query.edit_message_text(
            f"👨‍💻 <b>Admin Bilan Aloqa</b>\n\n"
            f"📩 Savol yoki muammo uchun:\n"
            f"👤 @{MAKER_ADMIN_USERNAME}\n\n"
            f"⏰ Javob vaqti: 24 soat ichida",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Admin", url=f"https://t.me/{MAKER_ADMIN_USERNAME}")],
                [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))
        return

    # — Maker Admin paneli —
    if data == "maker_admin":
        if not await is_maker_admin(user.id):
            await query.answer("❌ Ruxsat yo'q!", show_alert=True); return
        await query.edit_message_text(
            "🎛 <b>Maker Admin Paneli</b>", parse_mode="HTML", reply_markup=maker_admin_kb())
        return

    if data == "madm_stats":
        await _madm_stats(query); return

    if data == "madm_add_premium":
        if not await is_maker_admin(user.id): return
        set_state(ctx, "madm_premium_id")
        await query.edit_message_text(
            "⭐ <b>Premium Berish</b>\n\nFoydalanuvchi <b>User ID</b>sini yuboring:\n\n/bekor — bekor qilish",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    if data == "madm_remove_premium":
        if not await is_maker_admin(user.id): return
        set_state(ctx, "madm_remove_premium")
        await query.edit_message_text(
            "➖ <b>Premium Olish</b>\n\nFoydalanuvchi <b>User ID</b>sini yuboring:\n\n/bekor — bekor qilish",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    if data == "madm_add_admin":
        if not await is_maker_admin(user.id): return
        set_state(ctx, "madm_add_admin_id")
        await query.edit_message_text(
            "👤 <b>Admin Qo'shish</b>\n\nYangi admin <b>User ID</b>sini yuboring:\n\n/bekor — bekor qilish",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    if data == "madm_remove_admin":
        if not await is_maker_admin(user.id): return
        set_state(ctx, "madm_remove_admin")
        await query.edit_message_text(
            "🗑 <b>Admin O'chirish</b>\n\nAdmin <b>User ID</b>sini yuboring:\n\n/bekor — bekor qilish",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    if data == "madm_list_admins":
        await _madm_list_admins(query); return

    if data == "madm_add_channel":
        if not await is_maker_admin(user.id): return
        set_state(ctx, "madm_add_channel")
        await query.edit_message_text(
            "📢 <b>Kanal Qo'shish</b>\n\nKanal username'ini yuboring (@kanal):\n\n/bekor — bekor qilish",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    if data == "madm_remove_channel":
        if not await is_maker_admin(user.id): return
        channels = await get_maker_channels()
        if not channels:
            await query.edit_message_text("❌ Hech qanday kanal qo'shilmagan.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="maker_admin")]]))
            return
        txt_list = "\n".join(f"• @{c['channel_username']}" for c in channels)
        set_state(ctx, "madm_remove_channel")
        await query.edit_message_text(
            f"📋 Qaysi kanalni o'chirasiz?\n\n{txt_list}\n\nUsername'ini yuboring:\n/bekor — bekor qilish",
            reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return

    if data == "madm_list_channels":
        await _madm_list_channels(query); return

    if data == "madm_list_all_bots":
        await _madm_list_all_bots(query); return

    if data.startswith("fadmin_del_"):
        bot_id = int(data.split("_")[-1])
        await _force_delete_bot(query, ctx, bot_id); return

    if data == "madm_broadcast":
        if not await is_maker_admin(user.id): return
        set_state(ctx, "madm_broadcast")
        await query.edit_message_text(
            "📣 <b>Ommaviy Xabar</b>\n\nBarcha foydalanuvchilarga yuboriladigan xabarni yozing:\n\n/bekor — bekor qilish",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(CANCEL_BTN))
        return


# ─────────────────────────────────────────────────────────
# MAKER BOT — STATE HANDLERS
# ─────────────────────────────────────────────────────────
async def _handle_bot_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE, token: str):
    user = update.effective_user
    msg = await update.message.reply_text("⏳ Token tekshirilmoqda...")
    try:
        test_bot = Bot(token=token)
        bot_info = await test_bot.get_me()
    except Exception as e:
        await msg.edit_text(
            f"❌ Token noto'g'ri yoki bot bloklangan!\n"
            f"Xatolik: <code>{e}</code>\n\n"
            f"To'g'ri token yuboring yoki /bekor bilan bekor qiling:",
            parse_mode="HTML")
        return
    try:
        new_id = await db_execute_lastid(
            "INSERT INTO child_bots (owner_id,token,bot_name,bot_username,is_active) VALUES (?,?,?,?,1)",
            (user.id, token, bot_info.full_name, bot_info.username))
    except Exception:
        await msg.edit_text(
            "❌ Bu token allaqachon mavjud! Boshqa token yuboring yoki /bekor bilan bekor qiling.")
        return

    clear_state(ctx)
    await multi_bot_manager.start_child_bot(token, new_id)
    await msg.edit_text(
        f"✅ <b>Bot muvaffaqiyatli yaratildi!</b>\n\n"
        f"🤖 Nomi: <b>{bot_info.full_name}</b>\n"
        f"📌 Username: @{bot_info.username}\n"
        f"🟢 Holat: Faol\n\n"
        f"Boshqarish uchun 🤖 Mening Botlarim bo'limiga o'ting!",
        parse_mode="HTML",
        reply_markup=await maker_main_kb(user.id))


async def _madm_premium_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    try:
        uid = int(txt)
        set_state(ctx, "madm_premium_days", premium_uid=uid)
        await update.message.reply_text(
            f"✅ ID: <b>{uid}</b>\n\nNecha kun Premium? (masalan: 30)\n\n/bekor — bekor qilish",
            parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri ID. Raqam yuboring:")

async def _madm_premium_days(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    try:
        days = int(txt)
        uid = ctx.user_data.get("premium_uid")
        exp = (datetime.now() + timedelta(days=days)).isoformat()
        await db_execute("UPDATE maker_users SET is_premium=1,premium_expires=? WHERE user_id=?", (exp, uid))
        clear_state(ctx)
        row = await db_fetchone("SELECT username FROM maker_users WHERE user_id=?", (uid,))
        uname = row["username"] if row else "—"
        await update.message.reply_text(
            f"✅ <b>{uid}</b> (@{uname})ga {days} kunlik Premium berildi!\n"
            f"⏳ Tugash: {datetime.fromisoformat(exp).strftime('%d.%m.%Y')}",
            parse_mode="HTML")
        try:
            await ctx.bot.send_message(uid,
                f"🎉 Sizga <b>{days} kunlik Premium</b> berildi!\n"
                f"⏳ Tugash: {datetime.fromisoformat(exp).strftime('%d.%m.%Y')}",
                parse_mode="HTML")
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri son. Qaytadan:")

async def _madm_remove_premium(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    try:
        uid = int(txt)
        await db_execute("UPDATE maker_users SET is_premium=0,premium_expires=NULL WHERE user_id=?", (uid,))
        clear_state(ctx)
        await update.message.reply_text(f"✅ <b>{uid}</b>dan Premium olindi.", parse_mode="HTML")
        try:
            await ctx.bot.send_message(uid, "ℹ️ Premium tarifingiz bekor qilindi.")
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

async def _madm_add_admin_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    try:
        uid = int(txt)
        set_state(ctx, "madm_add_admin_username", new_admin_id=uid)
        await update.message.reply_text(
            f"✅ ID: <b>{uid}</b>\n\nUsername'ni yuboring (@siz):\n\n/bekor — bekor qilish",
            parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

async def _madm_add_admin_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    username = txt.lstrip("@")
    uid = ctx.user_data.get("new_admin_id")
    await db_execute("INSERT OR REPLACE INTO maker_admins (user_id,username) VALUES (?,?)", (uid, username))
    clear_state(ctx)
    await update.message.reply_text(f"✅ @{username} ({uid}) Maker Admin qilib tayinlandi!", parse_mode="HTML")

async def _madm_remove_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    try:
        uid = int(txt)
        if uid == MAKER_ADMIN_ID:
            await update.message.reply_text("❌ Asosiy adminni o'chirib bo'lmaydi!"); return
        await db_execute("DELETE FROM maker_admins WHERE user_id=?", (uid,))
        clear_state(ctx)
        await update.message.reply_text(f"✅ Admin {uid} o'chirildi.")
    except ValueError:
        await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

async def _madm_add_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    username = txt.lstrip("@")
    try:
        chat = await ctx.bot.get_chat(f"@{username}")
        title = chat.title or username
        await db_execute(
            "INSERT OR IGNORE INTO maker_channels (channel_username,channel_title) VALUES (?,?)",
            (username, title))
        clear_state(ctx)
        await update.message.reply_text(f"✅ <b>{title}</b> (@{username}) qo'shildi!", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(
            f"❌ Kanal topilmadi: {e}\n\nBot kanalda admin bo'lishi kerak!\nQaytadan yuboring yoki /bekor:")

async def _madm_remove_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE, txt: str):
    username = txt.lstrip("@")
    await db_execute("DELETE FROM maker_channels WHERE channel_username=?", (username,))
    clear_state(ctx)
    await update.message.reply_text(f"✅ @{username} kanali o'chirildi.")

async def _madm_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    users = await db_fetchall("SELECT user_id FROM maker_users")
    msg = await update.message.reply_text(f"📤 Yuborilmoqda... (0/{len(users)})")
    sent = failed = 0
    for i, u in enumerate(users):
        try:
            await update.message.copy_to(u["user_id"])
            sent += 1
        except Exception:
            failed += 1
        if i % 20 == 0:
            try:
                await msg.edit_text(f"📤 Yuborilmoqda... ({i}/{len(users)})")
            except Exception:
                pass
        await asyncio.sleep(0.04)
    clear_state(ctx)
    await msg.edit_text(
        f"✅ <b>Broadcast yakunlandi!</b>\n✅ Yuborildi: {sent}\n❌ Xatolik: {failed}",
        parse_mode="HTML")


# ─────────────────────────────────────────────────────────
# MAKER BOT — KICHIK FUNKSIYALAR
# ─────────────────────────────────────────────────────────
async def _show_my_bots(query, user):
    bots = await get_user_bots(user.id)
    if not bots:
        await query.edit_message_text(
            "🤖 Sizda hali bot yo'q.\n\n➕ Yangi bot yarating!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Yangi Bot Yaratish", callback_data="create_bot")],
                [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))
        return
    txt = f"🤖 <b>Sizning botlaringiz</b> ({len(bots)} ta):\n\n"
    btns = []
    for b in bots:
        status = "🟢" if b["is_active"] else "🔴"
        txt += f"{status} @{b['bot_username']}\n"
        btns.append([
            InlineKeyboardButton(f"⚙️ @{b['bot_username']}", callback_data=f"manage_bot_{b['id']}"),
            InlineKeyboardButton("🗑 O'chirish", callback_data=f"delete_bot_{b['id']}"),
        ])
    btns.append([InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")])
    await query.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

async def _delete_bot_confirm(query, bot_id: int):
    bot_row = await get_bot_by_id(bot_id)
    if not bot_row:
        await query.edit_message_text("❌ Bot topilmadi.")
        return
    await query.edit_message_text(
        f"⚠️ <b>@{bot_row['bot_username']}</b> botini o'chirmoqchimisiz?\n\n"
        f"Bu amal qaytarib bo'lmaydi!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Ha, o'chir", callback_data=f"confirm_del_bot_{bot_id}"),
             InlineKeyboardButton("❌ Yo'q", callback_data="my_bots")]]))

async def _delete_bot_execute(query, ctx, bot_id: int):
    bot_row = await get_bot_by_id(bot_id)
    if not bot_row:
        await query.edit_message_text("❌ Bot topilmadi."); return
    await multi_bot_manager.stop_child_bot(bot_row["token"])
    await db_executemany([
        ("DELETE FROM child_bot_channels WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_bot_admins WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_movies WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_users WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_bots WHERE id=?", (bot_id,)),
    ])
    await query.edit_message_text(
        f"✅ @{bot_row['bot_username']} boti o'chirildi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Botlarim", callback_data="my_bots")]]))

async def _show_tariffs(query, user):
    premium = await is_maker_premium(user.id)
    row = await db_fetchone("SELECT premium_expires FROM maker_users WHERE user_id=?", (user.id,))
    exp_info = ""
    if premium and row and row["premium_expires"]:
        exp = datetime.fromisoformat(row["premium_expires"])
        exp_info = f"\n⏳ Tugash: <b>{exp.strftime('%d.%m.%Y')}</b>"
    await query.edit_message_text(
        f"💳 <b>TARIFLAR</b>\n\n"
        f"Sizning holatinigiz: <b>{'⭐ Premium' if premium else '🆓 Bepul'}</b>{exp_info}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🆓 <b>BEPUL:</b>\n"
        f"  • 1 ta bot\n  • Oyiga 25 ta kino (30 kunda reset)\n"
        f"  • Maksimal 2 ta majburiy kanal\n  • VIP boshqaruv ✅\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"⭐ <b>PREMIUM:</b>\n"
        f"  • 10 tagacha bot\n  • Cheksiz kino\n"
        f"  • Cheksiz kanallar\n  • Sub-adminlar\n  • Broadcast\n  • VIP boshqaruv ✅",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👨‍💻 Admin bilan bog'lanish", url=f"https://t.me/{MAKER_ADMIN_USERNAME}")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="main_menu")]]))

async def _madm_stats(query):
    rows = await asyncio.gather(
        db_fetchone("SELECT COUNT(*) as c FROM maker_users"),
        db_fetchone("SELECT COUNT(*) as c FROM child_bots"),
        db_fetchone("SELECT COUNT(*) as c FROM child_bots WHERE is_active=1"),
        db_fetchone("SELECT COUNT(*) as c FROM maker_users WHERE is_premium=1"),
    )
    await query.edit_message_text(
        f"📊 <b>Tizim Statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{rows[0]['c']}</b>\n"
        f"🤖 Jami botlar: <b>{rows[1]['c']}</b>\n"
        f"🟢 Faol botlar: <b>{rows[2]['c']}</b>\n"
        f"⭐ Premium foydalanuvchilar: <b>{rows[3]['c']}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="maker_admin")]]))

async def _madm_list_admins(query):
    admins = await db_fetchall("SELECT * FROM maker_admins")
    txt = f"👤 <b>Maker Adminlar</b> ({len(admins) + 1} ta):\n\n"
    txt += f"👑 @{MAKER_ADMIN_USERNAME} (Asosiy)\n"
    for a in admins:
        txt += f"• @{a['username']} (ID: {a['user_id']})\n"
    await query.edit_message_text(txt, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="maker_admin")]]))

async def _madm_list_channels(query):
    channels = await get_maker_channels()
    txt = f"📢 <b>Maker Kanallar</b> ({len(channels)} ta):\n\n"
    for ch in channels:
        txt += f"• @{ch['channel_username']} — {ch['channel_title']}\n"
    if not channels:
        txt += "Hech qanday kanal qo'shilmagan."
    await query.edit_message_text(txt, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="maker_admin")]]))

async def _madm_list_all_bots(query):
    bots = await get_all_bots()
    txt = f"🤖 <b>Barcha Botlar</b> ({len(bots)} ta):\n\n"
    btns = []
    for b in bots:
        st = "🟢" if b["is_active"] else "🔴"
        txt += f"{st} @{b['bot_username']} (egasi: {b['owner_id']})\n"
        btns.append([InlineKeyboardButton(f"🗑 @{b['bot_username']}", callback_data=f"fadmin_del_{b['id']}")])
    btns.append([InlineKeyboardButton("🔙 Orqaga", callback_data="maker_admin")])
    if not bots:
        txt += "Hech qanday bot yaratilmagan."
    await query.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

async def _force_delete_bot(query, ctx, bot_id: int):
    bot_row = await get_bot_by_id(bot_id)
    if not bot_row:
        await query.answer("❌ Bot topilmadi!", show_alert=True); return
    await multi_bot_manager.stop_child_bot(bot_row["token"])
    await db_executemany([
        ("DELETE FROM child_bot_channels WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_bot_admins WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_movies WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_users WHERE bot_id=?", (bot_id,)),
        ("DELETE FROM child_bots WHERE id=?", (bot_id,)),
    ])
    await query.edit_message_text(
        f"✅ @{bot_row['bot_username']} admin tomonidan o'chirildi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="madm_list_all_bots")]]))


# ─────────────────────────────────────────────────────────
# CHILD BOT — FACTORY
# ─────────────────────────────────────────────────────────
def build_child_app(token: str, bot_id: int) -> Application:
    app = ApplicationBuilder().token(token).build()

    def child_admin_kb():
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Kino Qo'shish", callback_data="c_add_movie")],
            [InlineKeyboardButton("🗑 Kino O'chirish", callback_data="c_del_movie"),
             InlineKeyboardButton("✏️ Kino Tahrir", callback_data="c_edit_movie")],
            [InlineKeyboardButton("📋 Kinolar Ro'yxati", callback_data="c_list_movies")],
            [InlineKeyboardButton("📢 Kanal Qo'sh", callback_data="c_add_ch"),
             InlineKeyboardButton("🗑 Kanal O'chir", callback_data="c_del_ch")],
            [InlineKeyboardButton("📋 Kanallar", callback_data="c_list_ch")],
            [InlineKeyboardButton("👤 Sub-Admin Qo'sh", callback_data="c_add_sub"),
             InlineKeyboardButton("🗑 Sub-Admin O'chir", callback_data="c_del_sub")],
            [InlineKeyboardButton("📋 Sub-Adminlar", callback_data="c_list_sub")],
            [InlineKeyboardButton("⭐ VIP Berish", callback_data="c_add_vip"),
             InlineKeyboardButton("🗑 VIP Olish", callback_data="c_del_vip")],
            [InlineKeyboardButton("📣 Broadcast", callback_data="c_broadcast")],
            [InlineKeyboardButton("📊 Statistika", callback_data="c_stats")],
            [InlineKeyboardButton("✏️ Start Xabari", callback_data="c_start_msg")],
        ])

    def c_cancel_kb():
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="c_cancel")]])

    # ── /start ──────────────────────────────────────────
    async def child_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        ctx.user_data.pop("state", None)
        await register_child_user(bot_id, user.id, user.username or "", user.full_name)

        channels = await get_child_channels(bot_id)
        if channels:
            nj = await check_force_sub(ctx.bot, user.id, channels)
            if nj:
                await update.message.reply_text(
                    "📢 Botdan foydalanish uchun kanallarga a'zo bo'ling:",
                    reply_markup=force_sub_kb(nj))
                return

        row = await db_fetchone("SELECT start_message FROM child_bots WHERE id=?", (bot_id,))
        msg = (row["start_message"] if row else "Assalomu alaykum, {full_name}! Kino kodini yuboring 🎬"
               ).replace("{full_name}", user.full_name)
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Statistika", callback_data="c_user_stats")]]))

    # ── /admin ──────────────────────────────────────────
    async def child_admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        ctx.user_data.pop("state", None)
        bot_row = await get_bot_by_id(bot_id)
        if not bot_row:
            return
        if not await is_child_admin(bot_id, user.id, bot_row["owner_id"]):
            await update.message.reply_text("❌ Siz admin emassiz!")
            return
        await update.message.reply_text(
            "⚙️ <b>Admin Paneli</b>", parse_mode="HTML", reply_markup=child_admin_kb())

    # ── MESSAGE ROUTER ───────────────────────────────────
    async def child_message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        user = update.effective_user
        state = ctx.user_data.get("state", "")
        txt = (update.message.text or "").strip()

        # Bekor qilish
        if txt in ("/bekor", "bekor"):
            ctx.user_data.pop("state", None)
            await update.message.reply_text("❌ Bekor qilindi.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Statistika", callback_data="c_user_stats")]]))
            return

        # Admin paneli holatlari
        if state == "c_add_movie_code":
            if not txt.isdigit():
                await update.message.reply_text("❌ Kod faqat raqamdan iborat bo'lishi kerak:")
                return
            ctx.user_data["state"] = "c_add_movie_video"
            ctx.user_data["mv_code"] = txt
            await update.message.reply_text(
                f"✅ Kod: <b>{txt}</b>\n\nEndi kino videosini yuboring:", parse_mode="HTML")

        elif state == "c_add_movie_video":
            if not (update.message.video or update.message.document):
                await update.message.reply_text("❌ Iltimos, video yuboring:")
                return
            fid = (update.message.video or update.message.document).file_id
            ctx.user_data["state"] = "c_add_movie_caption"
            ctx.user_data["mv_file_id"] = fid
            await update.message.reply_text("✅ Video qabul!\n\nCaption yuboring (yo'q bo'lsa /skip):")

        elif state == "c_add_movie_caption":
            ctx.user_data["state"] = "c_add_movie_vip"
            ctx.user_data["mv_caption"] = "" if txt == "/skip" else txt
            await update.message.reply_text(
                "VIP kinomi?\n\n/ha — VIP\n/yoq — Oddiy")

        elif state == "c_add_movie_vip":
            is_vip = 1 if txt.lower() in ("/ha", "ha") else 0
            code = ctx.user_data.get("mv_code")
            fid = ctx.user_data.get("mv_file_id")
            caption = ctx.user_data.get("mv_caption", "")
            bot_row = await get_bot_by_id(bot_id)
            ok, err = await can_add_movie(bot_id, bot_row["owner_id"])
            if not ok:
                ctx.user_data.pop("state", None)
                await update.message.reply_text(err, parse_mode="HTML"); return
            try:
                await db_execute(
                    "INSERT INTO child_movies (bot_id,code,file_id,caption,is_vip) VALUES (?,?,?,?,?)",
                    (bot_id, code, fid, caption, is_vip))
                await db_execute(
                    "UPDATE child_bots SET monthly_movie_count=monthly_movie_count+1 WHERE id=?", (bot_id,))
                ctx.user_data.pop("state", None)
                vl = "🔒 VIP" if is_vip else "🔓 Oddiy"
                await update.message.reply_text(
                    f"✅ <b>Kino qo'shildi!</b>\n📌 Kod: <b>{code}</b>\n🎬 Turi: {vl}",
                    parse_mode="HTML")
            except Exception:
                ctx.user_data.pop("state", None)
                await update.message.reply_text(f"❌ Kod <b>{code}</b> allaqachon mavjud!", parse_mode="HTML")

        elif state == "c_del_movie_code":
            row = await db_fetchone("SELECT id FROM child_movies WHERE bot_id=? AND code=?", (bot_id, txt))
            if not row:
                await update.message.reply_text(f"❌ {txt} kodli kino topilmadi."); return
            await db_execute("DELETE FROM child_movies WHERE bot_id=? AND code=?", (bot_id, txt))
            ctx.user_data.pop("state", None)
            await update.message.reply_text(f"✅ <b>{txt}</b> kodli kino o'chirildi.", parse_mode="HTML")

        elif state == "c_edit_movie_code":
            row = await db_fetchone("SELECT id FROM child_movies WHERE bot_id=? AND code=?", (bot_id, txt))
            if not row:
                await update.message.reply_text(f"❌ {txt} kodli kino topilmadi."); return
            ctx.user_data["state"] = "c_edit_movie_caption"
            ctx.user_data["edit_code"] = txt
            await update.message.reply_text(
                f"✅ Kod: <b>{txt}</b>\n\nYangi caption yuboring (/skip — bo'sh):", parse_mode="HTML")

        elif state == "c_edit_movie_caption":
            code = ctx.user_data.get("edit_code")
            new_cap = "" if txt == "/skip" else txt
            await db_execute("UPDATE child_movies SET caption=? WHERE bot_id=? AND code=?", (new_cap, bot_id, code))
            ctx.user_data.pop("state", None)
            await update.message.reply_text(f"✅ <b>{code}</b> kodli kino yangilandi!", parse_mode="HTML")

        elif state == "c_add_ch":
            username = txt.lstrip("@")
            try:
                chat = await ctx.bot.get_chat(f"@{username}")
                title = chat.title or username
                await db_execute(
                    "INSERT OR IGNORE INTO child_bot_channels (bot_id,channel_username,channel_title) VALUES (?,?,?)",
                    (bot_id, username, title))
                ctx.user_data.pop("state", None)
                await update.message.reply_text(f"✅ <b>{title}</b> kanali qo'shildi!", parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ Topilmadi: {e}\nQaytadan yoki /bekor:")

        elif state == "c_del_ch":
            username = txt.lstrip("@")
            await db_execute("DELETE FROM child_bot_channels WHERE bot_id=? AND channel_username=?", (bot_id, username))
            ctx.user_data.pop("state", None)
            await update.message.reply_text(f"✅ @{username} o'chirildi.")

        elif state == "c_add_sub_id":
            try:
                uid = int(txt)
                ctx.user_data["state"] = "c_add_sub_username"
                ctx.user_data["sub_id"] = uid
                await update.message.reply_text(
                    f"✅ ID: <b>{uid}</b>\n\nUsername'ni yuboring (@username):", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

        elif state == "c_add_sub_username":
            username = txt.lstrip("@")
            uid = ctx.user_data.get("sub_id")
            await db_execute(
                "INSERT OR IGNORE INTO child_bot_admins (bot_id,user_id,username) VALUES (?,?,?)",
                (bot_id, uid, username))
            ctx.user_data.pop("state", None)
            await update.message.reply_text(f"✅ @{username} sub-admin tayinlandi!", parse_mode="HTML")

        elif state == "c_del_sub":
            try:
                uid = int(txt)
                await db_execute("DELETE FROM child_bot_admins WHERE bot_id=? AND user_id=?", (bot_id, uid))
                ctx.user_data.pop("state", None)
                await update.message.reply_text(f"✅ {uid} sub-admin o'chirildi.")
            except ValueError:
                await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

        elif state == "c_add_vip_id":
            try:
                uid = int(txt)
                ctx.user_data["state"] = "c_add_vip_days"
                ctx.user_data["vip_id"] = uid
                await update.message.reply_text(
                    f"✅ ID: <b>{uid}</b>\n\nNecha kun VIP? (masalan: 30):", parse_mode="HTML")
            except ValueError:
                await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

        elif state == "c_add_vip_days":
            try:
                days = int(txt)
                uid = ctx.user_data.get("vip_id")
                exp = (datetime.now() + timedelta(days=days)).isoformat()
                await db_executemany([
                    ("INSERT OR IGNORE INTO child_users (bot_id,user_id) VALUES (?,?)", (bot_id, uid)),
                    ("UPDATE child_users SET is_vip=1,vip_expires=? WHERE bot_id=? AND user_id=?", (exp, bot_id, uid)),
                ])
                ctx.user_data.pop("state", None)
                await update.message.reply_text(
                    f"✅ <b>{uid}</b>ga {days} kunlik VIP berildi!\n"
                    f"⏳ Tugash: {datetime.fromisoformat(exp).strftime('%d.%m.%Y')}",
                    parse_mode="HTML")
                try:
                    await ctx.bot.send_message(uid,
                        f"🎉 Sizga <b>{days} kunlik VIP</b> berildi!\n"
                        f"⏳ Tugash: {datetime.fromisoformat(exp).strftime('%d.%m.%Y')}\n🎬 VIP kinolarni ko'ring!",
                        parse_mode="HTML")
                except Exception:
                    pass
            except ValueError:
                await update.message.reply_text("❌ Noto'g'ri son. Qaytadan:")

        elif state == "c_del_vip":
            try:
                uid = int(txt)
                await db_execute(
                    "UPDATE child_users SET is_vip=0,vip_expires=NULL WHERE bot_id=? AND user_id=?", (bot_id, uid))
                ctx.user_data.pop("state", None)
                await update.message.reply_text(f"✅ {uid}dan VIP olindi.")
                try:
                    await ctx.bot.send_message(uid, "ℹ️ Sizning VIP statusingiz bekor qilindi.")
                except Exception:
                    pass
            except ValueError:
                await update.message.reply_text("❌ Noto'g'ri ID. Qaytadan:")

        elif state == "c_broadcast":
            users = await db_fetchall("SELECT user_id FROM child_users WHERE bot_id=?", (bot_id,))
            msg_sent = await update.message.reply_text(f"📤 Yuborilmoqda... (0/{len(users)})")
            sent = failed = 0
            for i, u in enumerate(users):
                try:
                    await update.message.copy_to(u["user_id"])
                    sent += 1
                except Exception:
                    failed += 1
                if i % 20 == 0:
                    try:
                        await msg_sent.edit_text(f"📤 Yuborilmoqda... ({i}/{len(users)})")
                    except Exception:
                        pass
                await asyncio.sleep(0.04)
            ctx.user_data.pop("state", None)
            await msg_sent.edit_text(
                f"✅ <b>Broadcast yakunlandi!</b>\n✅ Yuborildi: {sent}\n❌ Xatolik: {failed}",
                parse_mode="HTML")

        elif state == "c_start_msg":
            await db_execute("UPDATE child_bots SET start_message=? WHERE id=?", (txt, bot_id))
            ctx.user_data.pop("state", None)
            await update.message.reply_text(
                f"✅ Start xabari yangilandi!\n\n<i>{txt}</i>", parse_mode="HTML")

        else:
            # Kino qidirish
            if not txt.isdigit():
                return
            channels = await get_child_channels(bot_id)
            if channels:
                nj = await check_force_sub(ctx.bot, user.id, channels)
                if nj:
                    await update.message.reply_text(
                        "📢 Kino olish uchun kanallarga a'zo bo'ling:", reply_markup=force_sub_kb(nj))
                    return
            movie = await db_fetchone(
                "SELECT * FROM child_movies WHERE bot_id=? AND code=?", (bot_id, txt))
            if not movie:
                await update.message.reply_text(
                    f"😔 <b>{txt}</b> kodli kino topilmadi.", parse_mode="HTML")
                return
            if movie["is_vip"]:
                if not await is_child_vip(bot_id, user.id):
                    await update.message.reply_text(
                        "🔒 Bu kino faqat <b>VIP</b> foydalanuvchilar uchun!\n\n"
                        "VIP olish uchun admin bilan bog'laning.", parse_mode="HTML")
                    return
            cap = movie["caption"] or ""
            full_cap = f"🎬 Kod: <b>{txt}</b>\n\n{cap}" if cap else f"🎬 Kod: <b>{txt}</b>"
            try:
                await update.message.reply_video(video=movie["file_id"], caption=full_cap, parse_mode="HTML")
            except Exception as e:
                await update.message.reply_text(f"❌ Xatolik: {e}")

    # ── CALLBACK ROUTER ──────────────────────────────────
    async def child_callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        user = query.from_user

        if data == "check_sub":
            channels = await get_child_channels(bot_id)
            nj = await check_force_sub(ctx.bot, user.id, channels)
            if nj:
                await query.edit_message_text(
                    "❌ Hali barcha kanallarga a'zo bo'lmadingiz!", reply_markup=force_sub_kb(nj))
                return
            row = await db_fetchone("SELECT start_message FROM child_bots WHERE id=?", (bot_id,))
            msg = (row["start_message"] if row else "Kino kodini yuboring 🎬"
                   ).replace("{full_name}", user.full_name)
            await query.edit_message_text(
                f"✅ A'zolik tasdiqlandi!\n\n{msg}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📊 Statistika", callback_data="c_user_stats")]]))
            return

        if data == "c_user_stats":
            r1 = await db_fetchone("SELECT COUNT(*) as c FROM child_movies WHERE bot_id=?", (bot_id,))
            r2 = await db_fetchone("SELECT COUNT(*) as c FROM child_users WHERE bot_id=?", (bot_id,))
            await query.edit_message_text(
                f"📊 <b>Statistika</b>\n\n🎬 Kinolar: <b>{r1['c']}</b>\n👥 A'zolar: <b>{r2['c']}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_back")]]))
            return

        if data == "c_back":
            ctx.user_data.pop("state", None)
            row = await db_fetchone("SELECT start_message FROM child_bots WHERE id=?", (bot_id,))
            msg = (row["start_message"] if row else "Kino kodini yuboring 🎬"
                   ).replace("{full_name}", user.full_name)
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Statistika", callback_data="c_user_stats")]]))
            return

        if data == "c_cancel":
            ctx.user_data.pop("state", None)
            await query.edit_message_text(
                "❌ Bekor qilindi.", reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 Admin Panel", callback_data="c_panel")]]))
            return

        if data == "c_panel":
            bot_row = await get_bot_by_id(bot_id)
            if not bot_row or not await is_child_admin(bot_id, user.id, bot_row["owner_id"]):
                await query.answer("❌ Ruxsat yo'q!", show_alert=True); return
            ctx.user_data.pop("state", None)
            await query.edit_message_text("⚙️ <b>Admin Paneli</b>", parse_mode="HTML",
                reply_markup=child_admin_kb())
            return

        # Admin tekshiruvi
        bot_row = await get_bot_by_id(bot_id)
        if not bot_row or not await is_child_admin(bot_id, user.id, bot_row["owner_id"]):
            await query.answer("❌ Ruxsat yo'q!", show_alert=True)
            return

        # — Kino qo'shish —
        if data == "c_add_movie":
            ok, err = await can_add_movie(bot_id, bot_row["owner_id"])
            if not ok:
                await query.edit_message_text(err, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
                return
            ctx.user_data["state"] = "c_add_movie_code"
            await query.edit_message_text(
                "➕ <b>Kino Qo'shish</b>\n\nKino kodini yuboring (faqat raqam, masalan: 101)\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_del_movie":
            ctx.user_data["state"] = "c_del_movie_code"
            await query.edit_message_text(
                "🗑 <b>Kino O'chirish</b>\n\nO'chiriladigan kino kodini yuboring:\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_edit_movie":
            ctx.user_data["state"] = "c_edit_movie_code"
            await query.edit_message_text(
                "✏️ <b>Kino Tahrirlash</b>\n\nKino kodini yuboring:\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_list_movies":
            movies = await db_fetchall(
                "SELECT code,is_vip FROM child_movies WHERE bot_id=? ORDER BY CAST(code AS INTEGER)", (bot_id,))
            txt = f"🎬 <b>Kinolar</b> ({len(movies)} ta):\n\n"
            for m in movies:
                txt += f"{'🔒' if m['is_vip'] else '🔓'} Kod: <b>{m['code']}</b>\n"
            if not movies:
                txt += "Hech qanday kino yo'q."
            await query.edit_message_text(txt, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
            return

        if data == "c_add_ch":
            premium = await is_maker_premium(bot_row["owner_id"])
            channels = await get_child_channels(bot_id)
            if not premium and len(channels) >= 2:
                await query.edit_message_text(
                    "❌ Bepul tarif: maksimal 2 ta kanal!\n💎 Premium oling — cheksiz kanallar!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
                return
            ctx.user_data["state"] = "c_add_ch"
            await query.edit_message_text(
                "📢 <b>Kanal Qo'shish</b>\n\nKanal username'ini yuboring (@kanal):\n\nBot kanalda admin bo'lishi shart!\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_del_ch":
            channels = await get_child_channels(bot_id)
            if not channels:
                await query.edit_message_text("❌ Kanallar yo'q.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
                return
            txt_list = "\n".join(f"• @{c['channel_username']}" for c in channels)
            ctx.user_data["state"] = "c_del_ch"
            await query.edit_message_text(
                f"📋 Qaysi kanalni o'chirasiz?\n\n{txt_list}\n\nUsername yuboring:\n/bekor — bekor qilish",
                reply_markup=c_cancel_kb())
            return

        if data == "c_list_ch":
            channels = await get_child_channels(bot_id)
            txt = f"📢 <b>Kanallar</b> ({len(channels)} ta):\n\n"
            for ch in channels:
                txt += f"• @{ch['channel_username']} — {ch['channel_title']}\n"
            if not channels:
                txt += "Kanallar yo'q."
            await query.edit_message_text(txt, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
            return

        if data == "c_add_sub":
            ctx.user_data["state"] = "c_add_sub_id"
            await query.edit_message_text(
                "👤 <b>Sub-Admin Qo'shish</b>\n\nUser ID yuboring:\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_del_sub":
            admins = await get_child_admins(bot_id)
            if not admins:
                await query.edit_message_text("❌ Sub-adminlar yo'q.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
                return
            txt_list = "\n".join(f"• @{a['username']} (ID: {a['user_id']})" for a in admins)
            ctx.user_data["state"] = "c_del_sub"
            await query.edit_message_text(
                f"📋 Sub-Adminlar:\n\n{txt_list}\n\nO'chirish uchun User ID yuboring:\n/bekor — bekor qilish",
                reply_markup=c_cancel_kb())
            return

        if data == "c_list_sub":
            admins = await get_child_admins(bot_id)
            txt = f"👤 <b>Sub-Adminlar</b> ({len(admins)} ta):\n\n"
            for a in admins:
                txt += f"• @{a['username']} (ID: {a['user_id']})\n"
            if not admins:
                txt += "Sub-adminlar yo'q."
            await query.edit_message_text(txt, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
            return

        if data == "c_add_vip":
            ctx.user_data["state"] = "c_add_vip_id"
            await query.edit_message_text(
                "⭐ <b>VIP Berish</b>\n\nUser ID yuboring:\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_del_vip":
            ctx.user_data["state"] = "c_del_vip"
            await query.edit_message_text(
                "🗑 <b>VIP Olish</b>\n\nUser ID yuboring:\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_broadcast":
            ctx.user_data["state"] = "c_broadcast"
            r = await db_fetchone("SELECT COUNT(*) as c FROM child_users WHERE bot_id=?", (bot_id,))
            await query.edit_message_text(
                f"📣 <b>Broadcast</b>\n\nJami a'zolar: <b>{r['c']}</b>\n\nYuboriladigan xabarni yozing:\n\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

        if data == "c_stats":
            rows = await asyncio.gather(
                db_fetchone("SELECT COUNT(*) as c FROM child_movies WHERE bot_id=?", (bot_id,)),
                db_fetchone("SELECT COUNT(*) as c FROM child_users WHERE bot_id=?", (bot_id,)),
                db_fetchone("SELECT COUNT(*) as c FROM child_users WHERE bot_id=? AND is_vip=1", (bot_id,)),
                db_fetchone("SELECT COUNT(*) as c FROM child_bot_admins WHERE bot_id=?", (bot_id,)),
                db_fetchone("SELECT monthly_movie_count,last_reset_date FROM child_bots WHERE id=?", (bot_id,)),
            )
            monthly = rows[4]["monthly_movie_count"] if rows[4] else 0
            lr = datetime.fromisoformat(rows[4]["last_reset_date"]).strftime("%d.%m.%Y") if rows[4] else "—"
            premium = await is_maker_premium(bot_row["owner_id"])
            lim = "♾ Cheksiz" if premium else f"{monthly}/25"
            await query.edit_message_text(
                f"📊 <b>Bot Statistikasi</b>\n\n"
                f"🎬 Kinolar: <b>{rows[0]['c']}</b>\n"
                f"👥 A'zolar: <b>{rows[1]['c']}</b>\n"
                f"⭐ VIP a'zolar: <b>{rows[2]['c']}</b>\n"
                f"👤 Sub-Adminlar: <b>{rows[3]['c']}</b>\n\n"
                f"📅 Oylik kino: <b>{lim}</b>\n"
                f"🔄 Oxirgi reset: <b>{lr}</b>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="c_panel")]]))
            return

        if data == "c_start_msg":
            row = await db_fetchone("SELECT start_message FROM child_bots WHERE id=?", (bot_id,))
            cur_msg = row["start_message"] if row else "—"
            ctx.user_data["state"] = "c_start_msg"
            await query.edit_message_text(
                f"✏️ <b>Start Xabarini Tahrirlash</b>\n\n"
                f"Joriy xabar:\n<i>{cur_msg}</i>\n\n"
                f"<code>{{full_name}}</code> — foydalanuvchi ismi\n\n"
                f"Yangi xabarni yuboring:\n/bekor — bekor qilish",
                parse_mode="HTML", reply_markup=c_cancel_kb())
            return

    # ── Handlerlarni ro'yxatdan o'tkazish ───────────────
    app.add_handler(CommandHandler("start", child_start))
    app.add_handler(CommandHandler("admin", child_admin_cmd))
    app.add_handler(CallbackQueryHandler(child_callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, child_message_handler))

    return app


# ─────────────────────────────────────────────────────────
# MULTI BOT MANAGER
# ─────────────────────────────────────────────────────────
class MultiBotManager:
    def __init__(self):
        self._apps: dict[str, Application] = {}

    async def start_child_bot(self, token: str, bot_id: int):
        if token in self._apps:
            return
        try:
            app = build_child_app(token, bot_id)
            await app.initialize()
            await app.updater.start_polling(drop_pending_updates=True)
            await app.start()
            self._apps[token] = app
            await db_execute("UPDATE child_bots SET is_active=1 WHERE token=?", (token,))
            logger.info(f"✅ Child bot {bot_id} (@{app.bot.username}) started.")
        except Exception as e:
            logger.error(f"❌ Child bot {bot_id} start error: {e}")
            await db_execute("UPDATE child_bots SET is_active=0 WHERE token=?", (token,))

    async def stop_child_bot(self, token: str):
        app = self._apps.pop(token, None)
        if not app:
            return
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.warning(f"Stop child bot error: {e}")

    async def start_all_from_db(self):
        bots = await db_fetchall("SELECT id,token FROM child_bots WHERE is_active=1")
        logger.info(f"DB dan {len(bots)} ta faol child bot yuklanmoqda...")
        for b in bots:
            await self.start_child_bot(b["token"], b["id"])


multi_bot_manager = MultiBotManager()


# ─────────────────────────────────────────────────────────
# MAKER BOT — BUILD
# ─────────────────────────────────────────────────────────
def build_maker_app() -> Application:
    app = ApplicationBuilder().token(MAKER_BOT_TOKEN).build()

    async def post_init(application: Application):
        await init_db()
        await multi_bot_manager.start_all_from_db()
        await application.bot.set_my_commands([
            BotCommand("start", "Botni ishga tushirish"),
        ])
        logger.info("✅ MiniMakerUzBot tayyor!")

    app.post_init = post_init

    app.add_handler(CommandHandler("start", maker_start))
    app.add_handler(CallbackQueryHandler(maker_callback_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, maker_message_handler))

    return app


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────
def main():
    Thread(target=run_health_server, daemon=True).start()
    logger.info(f"✅ Health check server port {PORT} da ishga tushdi.")
    maker_app = build_maker_app()
    logger.info("🚀 MiniMakerUzBot ishga tushmoqda...")
    maker_app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
