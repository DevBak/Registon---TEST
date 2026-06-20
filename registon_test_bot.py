"""
Registon | TEST Bot
===================
Majburiy obuna + Test yechish + Baza yig'ish

O'rnatish:
    pip install python-telegram-bot==20.7

Ishga tushirish:
    python registon_test_bot.py

Kerakli o'zgarishlar:
    BOT_TOKEN  — @BotFather dan olingan token
    CHANNEL_ID — Majburiy obuna kanali (@username yoki -100xxxxx)
    ADMIN_IDS  — Admin foydalanuvchilar ID ro'yxati
"""

import asyncio
import logging
import os
import sqlite3
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ─────────────────────────────────────────────
#  SOZLAMALAR — bu yerdan o'zgartiring
# ─────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")          # Render → Environment Variables
CHANNEL_IDS = [                              # Majburiy obuna kanallari
    "@registan_dangara",
    "@registan_bagdad",
    "@registan_kokand",
]
CHANNEL_ID = "\n".join(CHANNEL_IDS)          # Eski xabar matnlari uchun
ADMIN_IDS   = [int(x) for x in os.environ.get("ADMIN_IDS", "8307855834").split(",") if x.strip()]
REQUIRE_SUBSCRIPTION = True  # Majburiy obuna yoqilgan
TEST_TIME_SECONDS = 60  # Har bir test uchun vaqt (soniya)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "registon_bot.db")))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")   # Render → Environment Variables (https://sizning-app.onrender.com)
PORT = int(os.environ.get("PORT", "8443"))

# Conversation holatlari
(
    SUB_CHECK, REG_NAME, REG_PHONE,
    ADMIN_QUESTION, ADMIN_OPTIONS, ADMIN_ANSWER, ADMIN_SAVE,
    TEST_SOLVING
) = range(8)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  MA'LUMOTLAR BAZASI
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Foydalanuvchilar
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            full_name   TEXT,
            phone       TEXT,
            registered  TEXT,
            last_seen   TEXT,
            tests_taken INTEGER DEFAULT 0
        )
    """)
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_seen TEXT")
    except sqlite3.OperationalError:
        pass

    # Botdan foydalanayotganlar aktivligi (ro'yxatdan o'tmaganlar ham kiradi)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            user_id   INTEGER PRIMARY KEY,
            username  TEXT,
            full_name TEXT,
            last_seen TEXT
        )
    """)

    # Testlar
    c.execute("""
        CREATE TABLE IF NOT EXISTS tests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            created_by  INTEGER,
            created_at  TEXT,
            is_active   INTEGER DEFAULT 1
        )
    """)

    # Savollar
    c.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            test_id     INTEGER,
            question    TEXT NOT NULL,
            option_a    TEXT,
            option_b    TEXT,
            option_c    TEXT,
            option_d    TEXT,
            correct     TEXT NOT NULL,
            FOREIGN KEY(test_id) REFERENCES tests(id)
        )
    """)

    # Natijalar
    c.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER,
            test_id     INTEGER,
            score       INTEGER,
            total       INTEGER,
            answers     TEXT,
            finished_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id),
            FOREIGN KEY(test_id) REFERENCES tests(id)
        )
    """)

    # Qayta yechish uchun admin beradigan bir martalik ruxsatlar
    c.execute("""
        CREATE TABLE IF NOT EXISTS retake_permissions (
            user_id    INTEGER,
            test_id    INTEGER,
            granted_at TEXT,
            PRIMARY KEY(user_id, test_id)
        )
    """)

    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)


# ─────────────────────────────────────────────
#  YORDAMCHI FUNKSIYALAR
# ─────────────────────────────────────────────
async def check_subscription(user_id: int, bot) -> bool:
    """Foydalanuvchi kanalga obuna bo'lganligini tekshiradi."""
    missing = await get_unsubscribed_channels(user_id, bot)
    return not missing

async def get_unsubscribed_channels(user_id: int, bot):
    """Foydalanuvchi obuna bo'lmagan kanallar ro'yxatini qaytaradi."""
    if not REQUIRE_SUBSCRIPTION:
        return []

    missing = []
    allowed_statuses = ("member", "administrator", "creator")
    for channel_id in CHANNEL_IDS:
        try:
            member = await bot.get_chat_member(channel_id, user_id)
            if member.status not in allowed_statuses:
                missing.append(channel_id)
        except Exception as e:
            logger.warning("%s kanalida obunani tekshirishda xatolik: %s", channel_id, e)
            missing.append(channel_id)
    return missing

def channel_url(channel_id: str) -> str:
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id.lstrip('@')}"
    return "https://t.me/"


def escape_md(text) -> str:
    """Telegram Markdown (legacy) uchun maxsus belgilarni ekranlaydi."""
    if text is None:
        return ""
    value = str(text)
    for ch in ("\\", "_", "*", "[", "`"):
        value = value.replace(ch, f"\\{ch}")
    return value

async def send_subscription_prompt(message, ctx: ContextTypes.DEFAULT_TYPE, edit=False):
    buttons = [
        [InlineKeyboardButton(f"Kanal {i}: {channel_id}", url=channel_url(channel_id))]
        for i, channel_id in enumerate(CHANNEL_IDS, start=1)
    ]
    buttons.append([InlineKeyboardButton("Obunani tekshirish", callback_data="check_sub")])

    text = (
        "*Davom etish uchun kanallarga obuna bo'ling*\n\n"
        "Quyidagi tugmalar orqali kanallarga kiring. "
        "Obuna bo'lgach, *Obunani tekshirish* tugmasini bosing."
    )
    reply_markup = InlineKeyboardMarkup(buttons)

    if edit:
        try:
            await message.edit_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            return
        except Exception:
            pass
    await message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_user(user_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    db.close()
    return row

def save_user(user_id, username, full_name, phone):
    db = get_db()
    c = db.cursor()
    now = datetime.now().isoformat()
    c.execute("""
        INSERT INTO users (user_id, username, full_name, phone, registered, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name,
            phone=excluded.phone,
            last_seen=excluded.last_seen
    """, (user_id, username, full_name, phone, now, now))
    db.commit()
    db.close()

def touch_user_activity(user):
    """Har bir harakatda foydalanuvchining oxirgi aktiv vaqtini yozadi."""
    if not user:
        return
    db = get_db()
    c = db.cursor()
    now = datetime.now().isoformat()
    c.execute("""
        INSERT INTO user_activity (user_id, username, full_name, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name,
            last_seen=excluded.last_seen
    """, (user.id, user.username, user.full_name, now))
    c.execute("UPDATE users SET last_seen=? WHERE user_id=?", (now, user.id))
    db.commit()
    db.close()


def touch_user(user_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("UPDATE users SET last_seen=? WHERE user_id=?", (datetime.now().isoformat(), user_id))
    db.commit()
    db.close()

def get_live_stats_text():
    now = datetime.now()
    five_min = (now - timedelta(minutes=5)).isoformat()
    one_hour = (now - timedelta(hours=1)).isoformat()
    one_day = (now - timedelta(days=1)).isoformat()
    today = now.date().isoformat()

    db = get_db()
    c = db.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_activity WHERE last_seen>=?", (five_min,))
    active_5m = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_activity WHERE last_seen>=?", (one_hour,))
    active_1h = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_activity WHERE last_seen>=?", (one_day,))
    active_24h = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_activity")
    total_seen = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE registered LIKE ?", (f"{today}%",))
    registered_today = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM results")
    total_results = c.fetchone()[0]
    db.close()

    return (
        "*Jonli statistika*\n\n"
        f"Hozir botda aktiv: {active_5m} ta\n"
        f"Oxirgi 1 soat: {active_1h} ta\n"
        f"Oxirgi 24 soat: {active_24h} ta\n\n"
        f"Botga kirgan jami: {total_seen} ta\n"
        f"Ro'yxatdan o'tgan jami: {total_users} ta\n"
        f"Bugun ro'yxatdan o'tgan: {registered_today} ta\n"
        f"Yechilgan testlar: {total_results} ta\n\n"
        f"Yangilandi: {now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

def get_active_tests():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT id, title FROM tests WHERE is_active=1")
    rows = c.fetchall()
    db.close()
    return rows

def get_test_questions(test_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT * FROM questions WHERE test_id=?", (test_id,))
    rows = c.fetchall()
    db.close()
    return rows

def save_test_to_db(title: str, questions: list, created_by: int) -> int:
    db = get_db()
    c = db.cursor()
    c.execute(
        "INSERT INTO tests (title, created_by, created_at) VALUES (?, ?, ?)",
        (title, created_by, datetime.now().isoformat())
    )
    test_id = c.lastrowid

    for q in questions:
        opts = q.get("options", ["", "", "", ""])
        c.execute("""
            INSERT INTO questions (test_id, question, option_a, option_b, option_c, option_d, correct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (test_id, q["question"], opts[0], opts[1], opts[2], opts[3], q["correct"]))

    db.commit()
    db.close()
    return test_id


def save_result(user_id, test_id, score, total, answers: dict):
    import json
    db = get_db()
    c = db.cursor()
    c.execute("""
        INSERT INTO results (user_id, test_id, score, total, answers, finished_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, test_id, score, total, json.dumps(answers), datetime.now().isoformat()))
    c.execute("UPDATE users SET tests_taken=tests_taken+1 WHERE user_id=?", (user_id,))
    db.commit()
    db.close()


def has_test_result(user_id: int, test_id: int) -> bool:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT 1 FROM results WHERE user_id=? AND test_id=? LIMIT 1", (user_id, test_id))
    exists = c.fetchone() is not None
    db.close()
    return exists


def has_retake_permission(user_id: int, test_id: int) -> bool:
    db = get_db()
    c = db.cursor()
    c.execute("SELECT 1 FROM retake_permissions WHERE user_id=? AND test_id=?", (user_id, test_id))
    exists = c.fetchone() is not None
    db.close()
    return exists


def grant_retake_permission(user_id: int, test_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("""
        INSERT INTO retake_permissions (user_id, test_id, granted_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, test_id) DO UPDATE SET granted_at=excluded.granted_at
    """, (user_id, test_id, datetime.now().isoformat()))
    db.commit()
    db.close()


def consume_retake_permission(user_id: int, test_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("DELETE FROM retake_permissions WHERE user_id=? AND test_id=?", (user_id, test_id))
    db.commit()
    db.close()


def get_retake_users():
    db = get_db()
    c = db.cursor()
    c.execute("""
        SELECT u.user_id, COALESCE(u.full_name, u.username, u.user_id), COUNT(DISTINCT r.test_id)
        FROM results r
        LEFT JOIN users u ON u.user_id=r.user_id
        GROUP BY r.user_id
        ORDER BY MAX(r.finished_at) DESC
        LIMIT 30
    """)
    rows = c.fetchall()
    db.close()
    return rows


def get_user_completed_tests(user_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("""
        SELECT t.id, t.title, MAX(r.finished_at),
               CASE WHEN rp.user_id IS NULL THEN 0 ELSE 1 END AS allowed
        FROM results r
        JOIN tests t ON t.id=r.test_id
        LEFT JOIN retake_permissions rp ON rp.user_id=r.user_id AND rp.test_id=r.test_id
        WHERE r.user_id=? AND t.is_active=1
        GROUP BY t.id, t.title, allowed
        ORDER BY MAX(r.finished_at) DESC
    """, (user_id,))
    rows = c.fetchall()
    db.close()
    return rows


def get_admin_users(limit: int = 30):
    db = get_db()
    c = db.cursor()
    c.execute("""
        SELECT u.user_id, COALESCE(u.full_name, u.username, u.user_id), u.phone,
               u.registered, u.tests_taken, COUNT(r.id) AS result_count
        FROM users u
        LEFT JOIN results r ON r.user_id=u.user_id
        GROUP BY u.user_id
        ORDER BY u.registered DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    db.close()
    return rows


def get_user_result_details(user_id: int):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT full_name, username, phone, registered, tests_taken FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    c.execute("""
        SELECT r.id, t.title, r.score, r.total, r.finished_at
        FROM results r
        JOIN tests t ON t.id=r.test_id
        WHERE r.user_id=?
        ORDER BY r.finished_at DESC
        LIMIT 20
    """, (user_id,))
    results = c.fetchall()
    db.close()
    return user, results


def get_result_answer_details(result_id: int):
    import json
    db = get_db()
    c = db.cursor()
    c.execute("""
        SELECT r.user_id, r.test_id, r.score, r.total, r.answers, r.finished_at,
               t.title, COALESCE(u.full_name, u.username, u.user_id)
        FROM results r
        JOIN tests t ON t.id=r.test_id
        LEFT JOIN users u ON u.user_id=r.user_id
        WHERE r.id=?
    """, (result_id,))
    result = c.fetchone()
    if not result:
        db.close()
        return None, []

    test_id = result[1]
    c.execute("""
        SELECT id, question, option_a, option_b, option_c, option_d, correct
        FROM questions
        WHERE test_id=?
        ORDER BY id
    """, (test_id,))
    questions = c.fetchall()
    db.close()

    try:
        answers = json.loads(result[4] or "{}")
    except json.JSONDecodeError:
        answers = {}

    details = []
    for index, q in enumerate(questions):
        answer = answers.get(str(index), answers.get(index, "-"))
        correct = (q[6] or "").upper()
        answer = (answer or "-").upper()
        options = {"A": q[2], "B": q[3], "C": q[4], "D": q[5]}
        details.append({
            "number": index + 1,
            "question": q[1],
            "answer": answer,
            "answer_text": options.get(answer, ""),
            "correct": correct,
            "correct_text": options.get(correct, ""),
            "is_correct": answer == correct,
        })
    return result, details


def read_docx_lines(file_bytes: bytes) -> list:
    """DOCX ichidagi paragraph matnlarini standart kutubxona orqali o'qiydi."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = []
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as docx:
        xml_data = docx.read("word/document.xml")
    root = ET.fromstring(xml_data)
    for paragraph in root.findall(".//w:p", ns):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{{{ns['w']}}}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{{{ns['w']}}}tab":
                parts.append(" ")
            elif node.tag == f"{{{ns['w']}}}br":
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            lines.extend(part.strip() for part in line.split("\n") if part.strip())
    return lines


def parse_docx_test(file_bytes: bytes):
    lines = read_docx_lines(file_bytes)
    if not lines:
        raise ValueError("Word faylda matn topilmadi.")

    title = "Word orqali qo'shilgan test"
    first = lines[0].strip()
    title_match = re.match(r"^(?:test|test nomi|nomi)\s*[:\-]\s*(.+)$", first, re.IGNORECASE)
    question_like = re.match(r"^(?:savol\s*[:\-]?|\d+[\.)\:\-])\s*", first, re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()
        lines = lines[1:]
    elif not question_like:
        title = first
        lines = lines[1:]

    questions = []
    current = None
    last_field = None

    def complete_question(q):
        return q and q.get("question") and all(q["options"]) and q.get("correct") in ("A", "B", "C", "D")

    def finish_current():
        nonlocal current
        if complete_question(current):
            questions.append(current)
            current = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        q_match = re.match(r"^(?:savol\s*[:\-]?|\d+[\.)\:\-])\s*(.+)$", line, re.IGNORECASE)
        opt_match = re.match(r"^([ABCD])\s*[\)\.\:\-]\s*(.+)$", line, re.IGNORECASE)
        ans_match = re.match(r"^(?:javob|to['’`]?g['’`]?ri\s+javob|correct|answer)\s*[:\-]\s*([ABCD])\b", line, re.IGNORECASE)

        if q_match:
            finish_current()
            current = {"question": q_match.group(1).strip(), "options": ["", "", "", ""], "correct": ""}
            last_field = "question"
            continue

        if opt_match and current:
            letter = opt_match.group(1).upper()
            option_index = "ABCD".index(letter)
            current["options"][option_index] = opt_match.group(2).strip()
            last_field = f"option_{letter}"
            continue

        if ans_match and current:
            current["correct"] = ans_match.group(1).upper()
            last_field = "correct"
            finish_current()
            continue

        if current and last_field == "question":
            current["question"] += " " + line
        elif current and last_field and last_field.startswith("option_"):
            letter = last_field[-1]
            option_index = "ABCD".index(letter)
            current["options"][option_index] += " " + line

    finish_current()
    if not questions:
        raise ValueError("Savollar topilmadi yoki format to'liq emas.")
    return title, questions

# ─────────────────────────────────────────────
#  /start — BOSHLASH
# ─────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    touch_user_activity(user)

    # Obunani tekshir
    subscribed = await check_subscription(user.id, ctx.bot)
    if not subscribed:
        await send_subscription_prompt(update.message, ctx)
        ctx.user_data["reg_step"] = "check_sub"
        return

    # Ro'yxatdan o'tganmi?
    existing = get_user(user.id)
    if not existing:
        await update.message.reply_text(
            f"👋 Salom! *Registon | TEST* botiga xush kelibsiz!\n\n"
            "Testni boshlash uchun avval ro'yxatdan o'ting.\n\n"
            "📝 *Ism va familiyangizni* kiriting:",
            parse_mode=None,
            reply_markup=ReplyKeyboardRemove()
        )
        ctx.user_data["reg_step"] = "name"
        return

    touch_user(user.id)
    await show_main_menu(update, ctx)
    return ConversationHandler.END


async def check_sub_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    touch_user_activity(user)

    subscribed = await check_subscription(user.id, ctx.bot)
    if not subscribed:
        await query.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True)
        ctx.user_data["reg_step"] = "check_sub"
        return

    await query.answer("Obuna tasdiqlandi!")
    existing = get_user(user.id)
    if not existing:
        await query.message.reply_text(
            "✅ Obuna tasdiqlandi!\n\n"
            "📝 *Ism va familiyangizni* kiriting:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove()
        )
        ctx.user_data["reg_step"] = "name"
        return

    touch_user(user.id)
    await query.message.delete()
    ctx.user_data.pop("reg_step", None)
    await show_main_menu_message(query.message, ctx, user.id)
    return ConversationHandler.END


# ─────────────────────────────────────────────
#  RO'YXATDAN O'TISH
# ─────────────────────────────────────────────
async def reg_get_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["full_name"] = update.message.text.strip()
    ctx.user_data["reg_step"] = "phone"
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True
    )
    await update.message.reply_text(
        "📱 *Telefon raqamingizni* yuboring:",
        parse_mode="Markdown",
        reply_markup=kb
    )
    return REG_PHONE


async def reg_get_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    full_name = ctx.user_data.get("full_name", user.full_name)
    save_user(user.id, user.username, full_name, phone)
    ctx.user_data.pop("reg_step", None)
    ctx.user_data.pop("full_name", None)

    await update.message.reply_text(
        f"✅ *Ro'yxatdan muvaffaqiyatli o'tdingiz!*\n\n"
        f"👤 Ism: {full_name}\n"
        f"📱 Tel: {phone}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    await show_main_menu(update, ctx)
    return ConversationHandler.END


async def registration_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ro'yxatdan o'tish xabarlarini oddiy holat orqali boshqaradi."""
    user = update.effective_user
    touch_user_activity(user)
    step = ctx.user_data.get("reg_step")
    if step == "name" and update.message and update.message.text:
        await reg_get_name(update, ctx)
        return
    if step == "phone" and update.message:
        await reg_get_phone(update, ctx)
        return

    if get_user(user.id) and not ctx.user_data.get("test_active") and not ctx.user_data.get("new_test"):
        await show_main_menu(update, ctx)


# ─────────────────────────────────────────────
#  ASOSIY MENYU
# ─────────────────────────────────────────────
async def show_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    buttons = [
        [InlineKeyboardButton("📝 Test yechish", callback_data="test_list")],
        [InlineKeyboardButton("📊 Mening natijalarim", callback_data="my_results")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_panel")])

    kb = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "🏫 *Registon | TEST*\n\n"
        "Quyidagi bo'limlardan birini tanlang:",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def show_main_menu_message(message, ctx, user_id):
    buttons = [
        [InlineKeyboardButton("📝 Test yechish", callback_data="test_list")],
        [InlineKeyboardButton("📊 Mening natijalarim", callback_data="my_results")],
    ]
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_panel")])
    kb = InlineKeyboardMarkup(buttons)
    await message.reply_text(
        "🏫 *Registon | TEST*\n\n"
        "Quyidagi bo'limlardan birini tanlang:",
        parse_mode="Markdown",
        reply_markup=kb
    )


async def admin_live_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    touch_user_activity(user)
    if not is_admin(user.id):
        return
    await update.message.reply_text(
        get_live_stats_text(),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Yangilash", callback_data="admin_live")],
            [InlineKeyboardButton("Admin panel", callback_data="admin_panel")],
        ])
    )


async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inline tugma callbacklarini boshqaradi."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    touch_user_activity(query.from_user)

    # ── Test ro'yxati ──
    if data == "test_list":
        clear_test_session(ctx)
        tests = get_active_tests()
        if not tests:
            await query.edit_message_text("❌ Hozircha faol testlar yo'q.")
            return
        buttons = [[InlineKeyboardButton(f"📄 {t[1]}", callback_data=f"start_test_{t[0]}")] for t in tests]
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")])
        await query.edit_message_text(
            "📋 *Mavjud testlar:*\nBirini tanlang:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # ── Test boshlash ──
    elif data.startswith("start_test_"):
        test_id = int(data.split("_")[-1])
        already_taken = has_test_result(user_id, test_id)
        retake_allowed = has_retake_permission(user_id, test_id)
        if already_taken and not retake_allowed:
            await query.edit_message_text(
                "❌ Siz bu testni oldin yechgansiz.\n\n"
                "Qayta yechish uchun admin ruxsat berishi kerak.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="test_list")]])
            )
            return

        questions = get_test_questions(test_id)
        if not questions:
            await query.edit_message_text("❌ Bu testda savollar yo'q.")
            return
        cancel_test_timer(ctx)
        ctx.user_data["test_id"] = test_id
        ctx.user_data["questions"] = questions
        ctx.user_data["current_q"] = 0
        ctx.user_data["answers"] = {}
        ctx.user_data["retake_permission_used"] = already_taken and retake_allowed
        ctx.user_data["test_active"] = True
        ctx.user_data["test_finished"] = False
        ctx.user_data["test_deadline"] = datetime.now() + timedelta(seconds=TEST_TIME_SECONDS)
        ctx.user_data["test_timer_task"] = asyncio.create_task(
            finish_test_timeout(ctx, ctx.bot, query.message.chat_id, query.message.message_id, user_id)
        )
        await send_question(query.message, ctx, edit=True)

    # ── Javob tanlash ──
    elif data.startswith("ans_"):
        if ctx.user_data.get("test_finished"):
            await query.answer("Bu test yakunlangan.", show_alert=True)
            return
        if datetime.now() >= ctx.user_data.get("test_deadline", datetime.now()):
            await show_result(query.message, ctx, user_id)
            return

        parts = data.split("_")
        q_index = int(parts[1])
        chosen = parts[2]
        questions = ctx.user_data.get("questions", [])
        ctx.user_data["answers"][q_index] = chosen
        ctx.user_data["current_q"] = q_index + 1

        if ctx.user_data["current_q"] >= len(questions):
            await show_result(query.message, ctx, user_id)
        else:
            await send_question(query.message, ctx, edit=True)

    # ── Mening natijalarim ──
    elif data == "my_results":
        db = get_db()
        c = db.cursor()
        c.execute("""
            SELECT r.id, r.score, r.total, r.finished_at, t.title
            FROM results r JOIN tests t ON r.test_id=t.id
            WHERE r.user_id=?
            ORDER BY r.finished_at DESC LIMIT 10
        """, (user_id,))
        rows = c.fetchall()
        db.close()
        if not rows:
            await query.edit_message_text(
                "📊 Hali birorta test yechmadingiz.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")]])
            )
            return

        text = "📊 *Sizning natijalaringiz:*\n\nTo'liq tahlilni ko'rish uchun testni tanlang:"
        buttons = []
        for result_id, score, total, finished_at, title in rows:
            pct = round(score / total * 100) if total else 0
            date = finished_at[:10] if finished_at else "-"
            buttons.append([InlineKeyboardButton(f"{title} | {score}/{total} ({pct}%) | {date}", callback_data=f"my_result_full_{result_id}")])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("my_result_full_"):
        result_id = int(data.replace("my_result_full_", ""))
        result, details = get_result_answer_details(result_id)
        if not result or result[0] != user_id:
            await query.edit_message_text(
                "❌ Natija topilmadi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Natijalarim", callback_data="my_results")]])
            )
            return

        result_user_id, test_id, score, total, answers_json, finished_at, test_title, user_name = result
        pct = round(score / total * 100) if total else 0
        if pct >= 85:
            emoji = "🏆"
            baho = "A'lo!"
        elif pct >= 70:
            emoji = "🥈"
            baho = "Yaxshi"
        elif pct >= 50:
            emoji = "🥉"
            baho = "Qoniqarli"
        else:
            emoji = "📚"
            baho = "Ko'proq o'qish kerak"

        text = (
            f"{emoji} *{test_title}*\n\n"
            f"✅ To'g'ri: {score} ta\n"
            f"❌ Noto'g'ri: {total - score} ta\n"
            f"📊 Natija: *{pct}%* — {baho}\n"
            f"🕒 Sana: {(finished_at or '-')[:16].replace('T', ' ')}\n\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"*Batafsil tahlil:*\n\n"
        )
        for item in details:
            mark = "✅" if item["is_correct"] else "❌"
            text += (
                f"{mark} *{item['number']}.* {escape_md(item['question'])}\n"
                f"   Sizning javob: *{item['answer']}* {escape_md(item['answer_text'])}\n"
                f"   To'g'ri javob: *{item['correct']}* {escape_md(item['correct_text'])}\n\n"
            )

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Natijalarim", callback_data="my_results")]])
        if len(text) > 4000:
            short_text = (
                f"{emoji} *{test_title}*\n\n"
                f"✅ To'g'ri: {score}/{total}\n"
                f"📊 Natija: *{pct}%* — {baho}\n\n"
                "Batafsil tahlil alohida xabar qilib yuborildi."
            )
            await query.edit_message_text(short_text, parse_mode="Markdown", reply_markup=kb)
            detail_text = "*Batafsil tahlil:*\n\n" + text.split("*Batafsil tahlil:*\n\n", 1)[1]
            chunks = [detail_text[i:i+3900] for i in range(0, len(detail_text), 3900)]
            for chunk in chunks:
                await query.message.reply_text(chunk, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

    # ── Admin panel ──
    elif data == "admin_panel" and is_admin(user_id):
        buttons = [
            [InlineKeyboardButton("Jonli statistika", callback_data="admin_live")],
            [InlineKeyboardButton("➕ Test qo'shish", callback_data="admin_add_test")],
            [InlineKeyboardButton("📋 Testlar ro'yxati", callback_data="admin_list_tests")],
            [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
            [InlineKeyboardButton("🔄 Qayta yechishga ruxsat", callback_data="admin_retake_users")],
            [InlineKeyboardButton("📥 CSV yuklab olish", callback_data="admin_csv")],
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")],
        ]
        await query.edit_message_text(
            "⚙️ *Admin panel*\nNimani qilmoqchisiz?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_live" and is_admin(user_id):
        await query.edit_message_text(
            get_live_stats_text(),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Yangilash", callback_data="admin_live")],
                [InlineKeyboardButton("Orqaga", callback_data="admin_panel")],
            ])
        )

    elif data == "admin_users" and is_admin(user_id):
        rows = get_admin_users()
        if not rows:
            await query.edit_message_text(
                "👥 Hali foydalanuvchilar yo'q.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]])
            )
            return

        text = f"👥 *Foydalanuvchilar ({len(rows)} ta)*\n\nNatijalarini ko'rish uchun foydalanuvchini tanlang:"
        buttons = []
        for uid, name, phone, registered, tests_taken, result_count in rows:
            label = f"{name} | {result_count} natija"
            buttons.append([InlineKeyboardButton(label, callback_data=f"admin_user_results_{uid}")])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("admin_user_results_") and is_admin(user_id):
        target_user_id = int(data.replace("admin_user_results_", ""))
        user, results = get_user_result_details(target_user_id)
        if not user:
            await query.edit_message_text(
                "Foydalanuvchi topilmadi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_users")]])
            )
            return

        full_name, username, phone, registered, tests_taken = user
        text = (
            f"👤 *{full_name or username or target_user_id}*\n"
            f"ID: `{target_user_id}`\n"
            f"Telefon: {phone or '-'}\n"
            f"Ro'yxatdan o'tgan: {(registered or '-')[:10]}\n"
            f"Jami urinishlar: {tests_taken or 0}\n\n"
        )
        buttons = []
        if not results:
            text += "📊 Hali test natijasi yo'q."
        else:
            text += "📊 *Test natijalari:*\n\n"
            for result_id, title, score, total, finished_at in results:
                pct = round(score / total * 100) if total else 0
                date = finished_at[:16].replace("T", " ") if finished_at else "-"
                text += f"• *{title}*\n  {score}/{total} ({pct}%) | {date}\n"
                buttons.append([InlineKeyboardButton(f"Javoblarni ko'rish: {title}", callback_data=f"admin_result_answers_{result_id}")])

        buttons.append([InlineKeyboardButton("🔄 Qayta yechishga ruxsat", callback_data=f"admin_retake_user_{target_user_id}")])
        buttons.append([InlineKeyboardButton("🔙 Foydalanuvchilar", callback_data="admin_users")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("admin_result_answers_") and is_admin(user_id):
        result_id = int(data.replace("admin_result_answers_", ""))
        result, details = get_result_answer_details(result_id)
        if not result:
            await query.edit_message_text(
                "Natija topilmadi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Foydalanuvchilar", callback_data="admin_users")]])
            )
            return

        target_user_id, test_id, score, total, answers_json, finished_at, test_title, user_name = result
        pct = round(score / total * 100) if total else 0
        date = finished_at[:16].replace("T", " ") if finished_at else "-"
        text = (
            f"📋 *{test_title}*\n"
            f"👤 {user_name}\n"
            f"Natija: {score}/{total} ({pct}%)\n"
            f"Sana: {date}\n\n"
            "*Berilgan javoblar:*\n\n"
        )
        for item in details:
            mark = "✅" if item["is_correct"] else "❌"
            text += (
                f"{mark} *{item['number']}.* {escape_md(item['question'])}\n"
                f"   Javobi: *{item['answer']}* {escape_md(item['answer_text'])}\n"
                f"   To'g'ri: *{item['correct']}* {escape_md(item['correct_text'])}\n\n"
            )

        buttons = [[InlineKeyboardButton("🔙 Natijalarga qaytish", callback_data=f"admin_user_results_{target_user_id}")]]
        if len(text) > 4000:
            short_text = (
                f"📋 *{test_title}*\n"
                f"👤 {user_name}\n"
                f"Natija: {score}/{total} ({pct}%)\n"
                f"Sana: {date}\n\n"
                "Savollar ko'p bo'lgani uchun javoblar alohida xabar qilib yuborildi."
            )
            await query.edit_message_text(short_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
            chunks = [text[i:i+3900] for i in range(0, len(text), 3900)]
            for chunk in chunks:
                await query.message.reply_text(chunk, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "admin_retake_users" and is_admin(user_id):
        rows = get_retake_users()
        if not rows:
            await query.edit_message_text(
                "🔄 Hali test yechgan foydalanuvchilar yo'q.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]])
            )
            return
        buttons = [
            [InlineKeyboardButton(f"{name} ({count} test)", callback_data=f"admin_retake_user_{uid}")]
            for uid, name, count in rows
        ]
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
        await query.edit_message_text(
            "🔄 *Kimga qayta yechish ruxsati berilsin?*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_retake_user_") and is_admin(user_id):
        target_user_id = int(data.replace("admin_retake_user_", ""))
        rows = get_user_completed_tests(target_user_id)
        if not rows:
            await query.edit_message_text(
                "Bu foydalanuvchida faol test natijasi topilmadi.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_retake_users")]])
            )
            return
        buttons = []
        text = "🔄 *Qaysi testga ruxsat beramiz?*\n\n"
        for test_id, title, finished_at, allowed in rows:
            status = "Ruxsat berilgan" if allowed else "Ruxsat berish"
            date = finished_at[:10] if finished_at else ""
            text += f"• {title} | oxirgi: {date}"
            if allowed:
                text += " | ruxsat bor"
            text += "\n"
            buttons.append([InlineKeyboardButton(f"{status}: {title}", callback_data=f"admin_allow_retake_{target_user_id}_{test_id}")])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_retake_users")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("admin_allow_retake_") and is_admin(user_id):
        parts = data.replace("admin_allow_retake_", "").split("_")
        target_user_id = int(parts[0])
        test_id = int(parts[1])
        grant_retake_permission(target_user_id, test_id)
        try:
            await ctx.bot.send_message(
                chat_id=target_user_id,
                text="✅ Sizga testni qayta yechishga ruxsat berildi.\n\nBotga kirib testni qaytadan boshlashingiz mumkin."
            )
        except Exception as e:
            logger.warning("Ruxsat xabarini yuborib bo'lmadi: %s", e)
        await query.answer("✅ Qayta yechishga ruxsat berildi", show_alert=True)
        rows = get_user_completed_tests(target_user_id)
        buttons = []
        text = "✅ Ruxsat berildi.\n\n*Foydalanuvchi testlari:*\n\n"
        for row_test_id, title, finished_at, allowed in rows:
            status = "Ruxsat berilgan" if allowed else "Ruxsat berish"
            text += f"• {title}"
            if allowed:
                text += " | ruxsat bor"
            text += "\n"
            buttons.append([InlineKeyboardButton(f"{status}: {title}", callback_data=f"admin_allow_retake_{target_user_id}_{row_test_id}")])
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_retake_users")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "admin_csv" and is_admin(user_id):
        await export_csv(query, ctx)

    elif data == "admin_list_tests" and is_admin(user_id):
        tests = get_active_tests()
        if not tests:
            text = "📋 Hozircha testlar yo'q."
            buttons = [[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]
        else:
            text = "📋 *Mavjud testlar:*\n\n"
            buttons = []
            for t in tests:
                db = get_db()
                c = db.cursor()
                c.execute("SELECT COUNT(*) FROM questions WHERE test_id=?", (t[0],))
                qcount = c.fetchone()[0]
                db.close()
                text += f"• *{t[1]}* — {qcount} ta savol\n"
                buttons.append([InlineKeyboardButton(f"🗑 {t[1]} ni o'chirish", callback_data=f"del_test_{t[0]}")])
            buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("del_test_") and is_admin(user_id):
        test_id = int(data.split("_")[-1])
        db = get_db()
        c = db.cursor()
        c.execute("UPDATE tests SET is_active=0 WHERE id=?", (test_id,))
        db.commit()
        db.close()
        await query.answer("✅ Test o'chirildi", show_alert=True)
        await query.edit_message_text("✅ Test o'chirildi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_panel")]]))

    # ── Orqaga ──
    elif data == "back_main":
        clear_test_session(ctx)
        buttons = [
            [InlineKeyboardButton("📝 Test yechish", callback_data="test_list")],
            [InlineKeyboardButton("📊 Mening natijalarim", callback_data="my_results")],
        ]
        if is_admin(user_id):
            buttons.append([InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_panel")])
        await query.edit_message_text(
            "🏫 *Registon | TEST*\n\nQuyidagi bo'limlardan birini tanlang:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


# ─────────────────────────────────────────────
#  TEST YECHISH
# ─────────────────────────────────────────────
def cancel_test_timer(ctx: ContextTypes.DEFAULT_TYPE):
    task = ctx.user_data.pop("test_timer_task", None)
    if task and not task.done():
        task.cancel()


def clear_test_session(ctx: ContextTypes.DEFAULT_TYPE):
    cancel_test_timer(ctx)
    for key in (
        "test_id", "questions", "current_q", "answers", "retake_permission_used",
        "test_active", "test_finished", "test_deadline", "test_timer_task",
    ):
        ctx.user_data.pop(key, None)


def format_option_button(letter: str, value: str) -> str:
    value = (value or "").strip()
    if len(value) > 38:
        value = value[:35] + "..."
    return f"{letter}) {value}" if value else letter


def build_result_summary(ctx: ContextTypes.DEFAULT_TYPE, user_id: int, timed_out: bool = False):
    if ctx.user_data.get("test_finished"):
        return None
    ctx.user_data["test_finished"] = True
    ctx.user_data["test_active"] = False

    questions = ctx.user_data.get("questions", [])
    answers = ctx.user_data.get("answers", {})
    test_id = ctx.user_data.get("test_id")

    correct_count = 0
    result_lines = []
    for i, q in enumerate(questions):
        user_ans = answers.get(i, "-")
        correct_ans = (q[7] or "").upper()
        is_correct = str(user_ans).upper() == correct_ans
        if is_correct:
            correct_count += 1
            mark = "✅"
        else:
            mark = "❌"
        opt_map = {"A": q[3], "B": q[4], "C": q[5], "D": q[6]}
        result_lines.append(
            f"{mark} {i+1}. {q[2]}\n"
            f"   Sizning javob: {user_ans} {opt_map.get(str(user_ans).upper(), '')}\n"
            f"   To'g'ri javob: {correct_ans} {opt_map.get(correct_ans, '')}"
        )

    total = len(questions)
    pct = round(correct_count / total * 100) if total else 0
    if pct >= 85:
        emoji = "🏆"
        baho = "A'lo!"
    elif pct >= 70:
        emoji = "🥈"
        baho = "Yaxshi"
    elif pct >= 50:
        emoji = "🥉"
        baho = "Qoniqarli"
    else:
        emoji = "📚"
        baho = "Ko'proq o'qish kerak"

    header = "⏰ Vaqt tugadi!" if timed_out else f"{emoji} Test yakunlandi!"
    summary = (
        f"{header}\n\n"
        f"✅ To'g'ri: {correct_count} ta\n"
        f"❌ Noto'g'ri: {total - correct_count} ta\n"
        f"📊 Natija: {pct}% — {baho}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Batafsil tahlil:\n\n"
        + "\n\n".join(result_lines)
    )

    if test_id is not None:
        save_result(user_id, test_id, correct_count, total, answers)
        if ctx.user_data.pop("retake_permission_used", False):
            consume_retake_permission(user_id, test_id)

    return summary, result_lines, emoji, baho, correct_count, total, pct


async def finish_test_timeout(ctx: ContextTypes.DEFAULT_TYPE, bot, chat_id: int, message_id: int, user_id: int):
    try:
        await asyncio.sleep(TEST_TIME_SECONDS)
        if not ctx.user_data.get("test_active") or ctx.user_data.get("test_finished"):
            return
        result = build_result_summary(ctx, user_id, timed_out=True)
        if not result:
            return
        summary, result_lines, emoji, baho, correct_count, total, pct = result
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")]])
        if len(summary) > 4000:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"⏰ Vaqt tugadi!\n\n✅ To'g'ri: {correct_count}/{total}\n📊 Natija: {pct}% — {baho}",
                reply_markup=kb
            )
            detail = "Batafsil tahlil:\n\n" + "\n\n".join(result_lines)
            for chunk in [detail[i:i+3900] for i in range(0, len(detail), 3900)]:
                await bot.send_message(chat_id=chat_id, text=chunk)
        else:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=summary, reply_markup=kb)
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.exception("Test timer xatosi: %s", e)


async def send_question(message, ctx: ContextTypes.DEFAULT_TYPE, edit=False):
    questions = ctx.user_data["questions"]
    index = ctx.user_data["current_q"]
    q = questions[index]
    total = len(questions)
    deadline = ctx.user_data.get("test_deadline", datetime.now())
    remaining = max(0, int((deadline - datetime.now()).total_seconds()))
    text = (
        f"📋 Savol {index + 1}/{total}\n"
        f"⏱ Qolgan vaqt: {remaining} soniya\n\n"
        f"{q[2]}\n\n"
        f"A) {q[3]}\n"
        f"B) {q[4]}\n"
        f"C) {q[5]}\n"
        f"D) {q[6]}"
    )
    buttons = [
        [InlineKeyboardButton(format_option_button("A", q[3]), callback_data=f"ans_{index}_A")],
        [InlineKeyboardButton(format_option_button("B", q[4]), callback_data=f"ans_{index}_B")],
        [InlineKeyboardButton(format_option_button("C", q[5]), callback_data=f"ans_{index}_C")],
        [InlineKeyboardButton(format_option_button("D", q[6]), callback_data=f"ans_{index}_D")],
    ]
    kb = InlineKeyboardMarkup(buttons)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            await message.reply_text(text, reply_markup=kb)
    else:
        await message.reply_text(text, reply_markup=kb)


async def show_result(message, ctx: ContextTypes.DEFAULT_TYPE, user_id: int):
    cancel_test_timer(ctx)
    result = build_result_summary(ctx, user_id, timed_out=False)
    if not result:
        return
    summary, result_lines, emoji, baho, correct_count, total, pct = result
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Bosh menyu", callback_data="back_main")]])

    if len(summary) > 4000:
        await message.edit_text(
            f"{emoji} Test yakunlandi!\n\n✅ To'g'ri: {correct_count}/{total}\n📊 Natija: {pct}% — {baho}",
            reply_markup=kb
        )
        detail = "Batafsil tahlil:\n\n" + "\n\n".join(result_lines)
        for chunk in [detail[i:i+3900] for i in range(0, len(detail), 3900)]:
            await message.reply_text(chunk)
    else:
        await message.edit_text(summary, reply_markup=kb)

# ─────────────────────────────────────────────
#  ADMIN: TEST QO'SHISH (ConversationHandler)
# ─────────────────────────────────────────────


def admin_add_controls(back_callback=None):
    buttons = []
    if back_callback:
        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data=back_callback)])
    buttons.append([InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_cancel_add_test")])
    return InlineKeyboardMarkup(buttons)
async def admin_add_test_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["new_test"] = {"title": "", "questions": [], "_adding_q_num": 1}
    ctx.user_data["current_new_q"] = {}
    await query.edit_message_text(
        "➕ Yangi test qo'shish\n\n"
        "1) Qo'lda tuzish uchun test nomini yozing.\n"
        "2) Word fayl yuboring (.docx), bot savollarni o'zi qo'shadi.\n\n"
        "Word formati:\n"
        "Test nomi: Matematika\n"
        "1. Savol matni\n"
        "A) Variant\nB) Variant\nC) Variant\nD) Variant\n"
        "Javob: A",
        reply_markup=admin_add_controls()
    )
    return ADMIN_QUESTION


async def admin_cancel_add_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.pop("new_test", None)
    ctx.user_data.pop("current_new_q", None)
    buttons = [
        [InlineKeyboardButton("Jonli statistika", callback_data="admin_live")],
        [InlineKeyboardButton("➕ Test qo'shish", callback_data="admin_add_test")],
        [InlineKeyboardButton("📋 Testlar ro'yxati", callback_data="admin_list_tests")],
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("🔄 Qayta yechishga ruxsat", callback_data="admin_retake_users")],
        [InlineKeyboardButton("📥 CSV yuklab olish", callback_data="admin_csv")],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="back_main")],
    ]
    await query.edit_message_text("⚙️ Admin panel\nNimani qilmoqchisiz?", reply_markup=InlineKeyboardMarkup(buttons))
    return ConversationHandler.END


async def admin_back_to_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["new_test"] = {"title": "", "questions": [], "_adding_q_num": 1}
    ctx.user_data["current_new_q"] = {}
    await query.edit_message_text(
        "➕ Yangi test qo'shish\n\nTest nomini yozing yoki .docx Word fayl yuboring.",
        reply_markup=admin_add_controls()
    )
    return ADMIN_QUESTION


async def admin_back_to_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    q_num = ctx.user_data.get("new_test", {}).get("_adding_q_num", 1)
    ctx.user_data["current_new_q"] = {}
    await query.edit_message_text(
        f"📝 {q_num}-savolni kiriting:",
        reply_markup=admin_add_controls("admin_back_to_title")
    )
    return ADMIN_OPTIONS


async def admin_import_docx_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        return ConversationHandler.END

    document = update.message.document
    filename = document.file_name or ""
    if not filename.lower().endswith(".docx"):
        await update.message.reply_text(
            "⚠️ Iltimos, .docx formatdagi Word fayl yuboring.",
            reply_markup=admin_add_controls()
        )
        return ADMIN_QUESTION

    await update.message.reply_text("⏳ Word fayl o'qilyapti...")
    try:
        tg_file = await document.get_file()
        buffer = io.BytesIO()
        await tg_file.download_to_memory(out=buffer)
        title, questions = parse_docx_test(buffer.getvalue())
        save_test_to_db(title, questions, user.id)
    except Exception as e:
        logger.exception("Word test importida xatolik")
        await update.message.reply_text(
            "❌ Word fayldan test qo'shib bo'lmadi.\n\n"
            f"Sabab: {e}\n\n"
            "Formatni tekshiring:\n"
            "Test nomi: Matematika\n"
            "1. Savol matni\n"
            "A) Variant\nB) Variant\nC) Variant\nD) Variant\n"
            "Javob: A",
            reply_markup=admin_add_controls()
        )
        return ADMIN_QUESTION

    ctx.user_data.pop("new_test", None)
    ctx.user_data.pop("current_new_q", None)
    await update.message.reply_text(
        f"✅ Word fayldan test qo'shildi!\n\n📄 Nom: {title}\n❓ Savollar: {len(questions)} ta",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_panel")]])
    )
    return ConversationHandler.END


async def admin_get_test_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("⚠️ Test nomini yozing.", reply_markup=admin_add_controls())
        return ADMIN_QUESTION
    ctx.user_data["new_test"] = {"title": title, "questions": [], "_adding_q_num": 1}
    ctx.user_data["current_new_q"] = {}
    await update.message.reply_text(
        "✅ Test nomi saqlandi.\n\n📝 1-savolni kiriting:",
        reply_markup=admin_add_controls("admin_back_to_title")
    )
    return ADMIN_OPTIONS


async def admin_get_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ Savol matnini yozing.", reply_markup=admin_add_controls("admin_back_to_title"))
        return ADMIN_OPTIONS
    ctx.user_data["current_new_q"] = {"question": text, "options": []}
    await update.message.reply_text(
        f"❓ Savol: {text}\n\nA variantini kiriting:",
        reply_markup=admin_add_controls("admin_back_to_question")
    )
    return ADMIN_ANSWER


async def admin_get_options(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("⚠️ Variant bo'sh bo'lmasin.", reply_markup=admin_add_controls("admin_back_to_question"))
        return ADMIN_ANSWER

    current = ctx.user_data.get("current_new_q")
    if not current:
        await update.message.reply_text("⚠️ Savol topilmadi. Qaytadan savol kiriting.", reply_markup=admin_add_controls("admin_back_to_title"))
        return ADMIN_OPTIONS

    options = current.get("options", [])
    options.append(text)
    current["options"] = options

    next_labels = ["B", "C", "D"]
    if len(options) < 4:
        await update.message.reply_text(
            f"{next_labels[len(options)-1]} variantini kiriting:",
            reply_markup=admin_add_controls("admin_back_to_question")
        )
        return ADMIN_ANSWER

    a, b, c, d = options
    await update.message.reply_text(
        f"📋 Savol: {current['question']}\n\n"
        f"A) {a}\nB) {b}\nC) {c}\nD) {d}\n\n"
        "To'g'ri javobni kiriting: A, B, C yoki D",
        reply_markup=admin_add_controls("admin_back_to_question")
    )
    return ADMIN_SAVE


async def admin_get_correct_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ans = update.message.text.strip().upper()
    if ans not in ("A", "B", "C", "D"):
        await update.message.reply_text(
            "⚠️ Faqat A, B, C yoki D kiriting!",
            reply_markup=admin_add_controls("admin_back_to_question")
        )
        return ADMIN_SAVE

    current = ctx.user_data.get("current_new_q")
    if not current or len(current.get("options", [])) != 4:
        await update.message.reply_text("⚠️ Savol to'liq emas. Qaytadan kiriting.", reply_markup=admin_add_controls("admin_back_to_title"))
        return ADMIN_OPTIONS

    current["correct"] = ans
    ctx.user_data["new_test"]["questions"].append(current)
    ctx.user_data["current_new_q"] = {}
    q_count = len(ctx.user_data["new_test"]["questions"])

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Yana savol qo'shish", callback_data="add_more_q")],
        [InlineKeyboardButton("💾 Testni saqlash", callback_data="save_test")],
        [InlineKeyboardButton("🔙 Savolni qayta kiritish", callback_data="admin_back_to_question")],
        [InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_cancel_add_test")],
    ])
    await update.message.reply_text(f"✅ {q_count}-savol saqlandi.\n\nNimani qilmoqchisiz?", reply_markup=buttons)
    return ADMIN_SAVE


async def admin_add_more_question(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if "new_test" not in ctx.user_data:
        await query.edit_message_text("Test ma'lumoti topilmadi. Qaytadan boshlang.", reply_markup=admin_add_controls())
        return ADMIN_QUESTION
    q_num = len(ctx.user_data["new_test"].get("questions", [])) + 1
    ctx.user_data["new_test"]["_adding_q_num"] = q_num
    ctx.user_data["current_new_q"] = {}
    await query.edit_message_text(
        f"📝 {q_num}-savolni kiriting:",
        reply_markup=admin_add_controls("admin_back_to_title")
    )
    return ADMIN_OPTIONS


async def admin_save_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    new_test = ctx.user_data.get("new_test", {})
    title = new_test.get("title", "Nomsiz test")
    questions = new_test.get("questions", [])

    if not questions:
        await query.edit_message_text("❌ Savollar yo'q. Test saqlanmadi.", reply_markup=admin_add_controls())
        return ADMIN_QUESTION

    save_test_to_db(title, questions, query.from_user.id)
    ctx.user_data.pop("new_test", None)
    ctx.user_data.pop("current_new_q", None)

    await query.edit_message_text(
        f"✅ Test muvaffaqiyatli saqlandi!\n\n📄 Nom: {title}\n❓ Savollar: {len(questions)} ta",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_panel")]])
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("new_test", None)
    ctx.user_data.pop("current_new_q", None)
    await update.message.reply_text(
        "❌ Bekor qilindi.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin panel", callback_data="admin_panel")]])
    )
    return ConversationHandler.END

# ─────────────────────────────────────────────
#  CSV EXPORT
# ─────────────────────────────────────────────
async def export_csv(query, ctx: ContextTypes.DEFAULT_TYPE):
    db = get_db()
    c = db.cursor()
    c.execute("SELECT user_id, username, full_name, phone, registered, tests_taken FROM users")
    rows = c.fetchall()
    db.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Username", "Ism Familiya", "Telefon", "Ro'yxatdan o'tgan", "Testlar"])
    for r in rows:
        writer.writerow(r)

    output.seek(0)
    bio = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    bio.name = "foydalanuvchilar.csv"

    await ctx.bot.send_document(
        chat_id=query.from_user.id,
        document=bio,
        filename="foydalanuvchilar.csv",
        caption=f"📊 Jami {len(rows)} ta foydalanuvchi"
    )
    await query.answer("✅ CSV yuborildi!", show_alert=True)


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.exception("Yangilanishni qayta ishlashda xatolik", exc_info=ctx.error)


# ─────────────────────────────────────────────
#  ASOSIY ISHGA TUSHIRISH
# ─────────────────────────────────────────────
def main():
    init_db()
    logger.info("Bot ishga tushmoqda...")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Admin test qo'shish conversation
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_test_start, pattern="^admin_add_test$")],
        states={
            ADMIN_QUESTION: [
                CallbackQueryHandler(admin_cancel_add_test, pattern="^admin_cancel_add_test$"),
                MessageHandler(filters.Document.ALL, admin_import_docx_test),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_test_title),
            ],
            ADMIN_OPTIONS: [
                CallbackQueryHandler(admin_cancel_add_test, pattern="^admin_cancel_add_test$"),
                CallbackQueryHandler(admin_back_to_title, pattern="^admin_back_to_title$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_question),
            ],
            ADMIN_ANSWER: [
                CallbackQueryHandler(admin_cancel_add_test, pattern="^admin_cancel_add_test$"),
                CallbackQueryHandler(admin_back_to_question, pattern="^admin_back_to_question$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_options),
            ],
            ADMIN_SAVE: [
                CallbackQueryHandler(admin_cancel_add_test, pattern="^admin_cancel_add_test$"),
                CallbackQueryHandler(admin_back_to_question, pattern="^admin_back_to_question$"),
                CallbackQueryHandler(admin_add_more_question, pattern="^add_more_q$"),
                CallbackQueryHandler(admin_save_test, pattern="^save_test$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_correct_answer),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(admin_cancel_add_test, pattern="^admin_cancel_add_test$")],
        allow_reentry=True,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("live", admin_live_command))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
    app.add_handler(admin_conv)
    app.add_handler(MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), registration_router))
    app.add_handler(CallbackQueryHandler(menu_callback))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        # Render / production: webhook rejimi
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/webhook",
            url_path="/webhook",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        # Local ishlab chiqish: polling rejimi
        logger.info("WEBHOOK_URL topilmadi — polling rejimida ishlamoqda")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
