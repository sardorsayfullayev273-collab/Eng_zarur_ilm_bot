
import os
import io
import json
import sqlite3
import threading
from datetime import datetime, time
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Eng_zarur_ilm_bot")
DB_PATH = os.getenv("DB_PATH", "bot.db")

TEMPLATE = "premium_template.png"

# Fonts available on Render Linux images.
FONT_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
FONT_SERIF_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
FONT_ARABIC = "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf"

GREEN = (8, 57, 46)
GOLD = (224, 183, 84)
TEXT = (18, 55, 48)
IVORY = (247, 239, 228)
SOFT = (239, 242, 222)


def F(path, size):
    return ImageFont.truetype(path, size)


with open("hadiths.json", "r", encoding="utf-8") as f:
    HADITHS = json.load(f)

HADITHS.sort(key=lambda x: int(x["id"]))
HADITH_BY_ID = {int(x["id"]): x for x in HADITHS}


def now():
    return datetime.now(TIMEZONE)


def today():
    return now().date().isoformat()


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            audience TEXT,
            current_hadith INTEGER DEFAULT 0,
            joined_date TEXT,
            learned_today INTEGER DEFAULT 0
        )
    """)
    con.commit()
    return con


def ensure_user(uid):
    con = get_db()
    row = con.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO users(user_id, current_hadith, joined_date) VALUES(?,?,?)",
            (uid, 0, today())
        )
    con.commit()
    con.close()


def set_audience(uid, audience):
    con = get_db()
    con.execute("""
        UPDATE users
        SET audience=?, joined_date=COALESCE(joined_date,?)
        WHERE user_id=?
    """, (audience, today(), uid))
    con.commit()
    con.close()


def get_user(uid):
    con = get_db()
    row = con.execute("""
        SELECT user_id,audience,current_hadith,joined_date,learned_today
        FROM users WHERE user_id=?
    """, (uid,)).fetchone()
    con.close()
    return row


def set_current(uid, hid):
    con = get_db()
    con.execute(
        "UPDATE users SET current_hadith=?, learned_today=0 WHERE user_id=?",
        (hid, uid)
    )
    con.commit()
    con.close()


def mark_learned(uid):
    con = get_db()
    con.execute("UPDATE users SET learned_today=1 WHERE user_id=?", (uid,))
    con.commit()
    con.close()


def wrap_lines(draw, text, font, max_width):
    words = str(text).split()
    lines = []
    current = ""

    for word in words:
        test = word if not current else current + " " + word
        if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def centered_text(draw, text, box, font, fill=TEXT, spacing=7, max_lines=4):
    x1, y1, x2, y2 = box
    lines = wrap_lines(draw, text, font, x2 - x1)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while draw.textbbox((0, 0), last + "…", font=font)[2] > x2 - x1 and last:
            last = last[:-1]
        lines[-1] = last + "…"

    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total = sum(heights) + spacing * (len(lines) - 1)
    y = y1 + max(0, ((y2 - y1) - total) // 2)

    for line, height in zip(lines, heights):
        bb = draw.textbbox((0, 0), line, font=font)
        x = x1 + ((x2 - x1) - (bb[2] - bb[0])) // 2
        draw.text((x, y), line, font=font, fill=fill)
        y += height + spacing


def make_premium_card(hadith):
    # The exact premium reference background is stored in premium_template.png.
    # Only the variable text is rendered by Pillow. This prevents AI text distortion.
    img = Image.open(TEMPLATE).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Badge
    draw.rounded_rectangle(
        (265, 210, 650, 285),
        radius=32,
        fill=GREEN,
        outline=GOLD,
        width=5
    )
    draw.text(
        (457, 248),
        "BUGUNGI HADIS",
        font=F(FONT_SERIF_BOLD, 37),
        fill=(250, 232, 169),
        anchor="mm"
    )

    # Arabic — exact value stored in hadiths.json.
    arabic = hadith["arabic"].strip()
    draw.text(
        (540, 430),
        arabic,
        font=F(FONT_ARABIC, 58),
        fill=TEXT,
        anchor="mm",
        direction="rtl"
    )

    # Decorative divider
    draw.line((380, 585, 750, 585), fill=GOLD, width=2)
    draw.ellipse((560, 580, 570, 590), fill=GOLD)

    # Uzbek text — exact value stored in hadiths.json.
    centered_text(
        draw,
        "“" + hadith["text"].strip() + "”",
        (170, 615, 930, 760),
        F(FONT_SERIF_BOLD, 40),
        spacing=5,
        max_lines=3
    )

    # Number + source.
    draw.rounded_rectangle(
        (385, 785, 720, 845),
        radius=30,
        fill=(250, 248, 239),
        outline=GOLD,
        width=2
    )
    centered_text(
        draw,
        f"Hadis №{hadith['id']}  •  {hadith['source_short']}",
        (400, 795, 705, 835),
        F(FONT_SERIF_BOLD, 20),
        spacing=2,
        max_lines=2
    )

    # Meaning / sharh from the source.
    draw.rounded_rectangle(
        (170, 885, 985, 1115),
        radius=34,
        fill=SOFT
    )
    draw.text(
        (240, 920),
        "HADIS MA’NOSI",
        font=F(FONT_SERIF_BOLD, 29),
        fill=TEXT
    )
    centered_text(
        draw,
        hadith["meaning"].strip(),
        (225, 965, 945, 1095),
        F(FONT_SERIF, 23),
        spacing=5,
        max_lines=5
    )

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


async def send_next_hadith(bot, uid):
    row = get_user(uid)
    if not row or not row[1]:
        return

    current = int(row[2] or 0)
    next_id = current + 1

    if next_id > len(HADITHS):
        # After the last verified item, do not invent or loop into random material.
        next_id = len(HADITHS)

    hadith = HADITH_BY_ID[next_id]
    set_current(uid, next_id)

    photo = make_premium_card(hadith)

    share_text = (
        f"📖 Hadis №{hadith['id']}\n"
        f"{hadith['text']}\n\n"
        f"Manba: {hadith['source_short']}"
    )
    share_url = (
        f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}"
        f"&text={quote(share_text)}"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✓ Bilib oldim",
            callback_data=f"learn:{hadith['id']}"
        )],
        [InlineKeyboardButton("↗ Ulashish", url=share_url)]
    ])

    await bot.send_photo(
        chat_id=uid,
        photo=photo,
        caption=f"📖 Hadis №{hadith['id']}",
        reply_markup=keyboard
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👦 Bolalar uchun", callback_data="aud:children")],
        [InlineKeyboardButton("👤 Kattalar uchun", callback_data="aud:adults")]
    ])

    await update.message.reply_text(
        "Assalomu alaykum!\n\nKim uchun hadislar kerakligini tanlang:",
        reply_markup=keyboard
    )


async def audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    audience_value = query.data.split(":")[1]

    ensure_user(uid)
    set_audience(uid, audience_value)

    await query.edit_message_text("Tanlov saqlandi. Bugungi hadis tayyorlanmoqda…")
    await send_next_hadith(context.bot, uid)


async def learned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Bilib oldim ✓")
    mark_learned(query.from_user.id)

    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "✓ Bilib oldim — saqlandi",
                callback_data="noop"
            )]
        ])
    )


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Bu hadis allaqachon saqlangan.")


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_next_hadith(context.bot, update.effective_user.id)


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    con = get_db()
    users = con.execute("""
        SELECT user_id, joined_date
        FROM users
        WHERE audience IS NOT NULL
    """).fetchall()
    con.close()

    today_value = today()

    for uid, joined_date in users:
        # First day: the hadith was sent immediately after audience selection.
        if joined_date == today_value:
            continue

        await send_next_hadith(context.bot, uid)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Eng_zarur_ilm_bot OK")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(
        target=server.serve_forever,
        daemon=True
    ).start()


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi.")

    start_health_server()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("next", next_command))

    app.add_handler(
        CallbackQueryHandler(audience, pattern=r"^aud:")
    )
    app.add_handler(
        CallbackQueryHandler(learned, pattern=r"^learn:")
    )
    app.add_handler(
        CallbackQueryHandler(noop, pattern=r"^noop$")
    )

    # Asia/Tashkent, every day at 06:00.
    app.job_queue.run_daily(
        daily_job,
        time=time(hour=6, minute=0, tzinfo=TIMEZONE),
        name="daily_hadith"
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
