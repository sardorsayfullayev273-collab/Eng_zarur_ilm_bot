
import os
import io
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Eng_zarur_ilm_bot")
DB = os.getenv("DB_PATH", "bot.db")

W, H = 1536, 1536
FONT_DIR = "/usr/share/fonts/truetype/dejavu"

def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size)

def arabic_font(size):
    # If you add an Arabic-capable TTF to the project, set ARABIC_FONT_PATH.
    path = os.getenv("ARABIC_FONT_PATH", "")
    if path and os.path.exists(path):
        return ImageFont.truetype(path, size)
    # DejaVu has Arabic glyphs on most Linux images.
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)

def now_local():
    return datetime.now(TIMEZONE)

def today():
    return now_local().date().isoformat()

def db():
    con = sqlite3.connect(DB)
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

def get_user(uid):
    con = db()
    row = con.execute("SELECT user_id,audience,current_hadith,joined_date,learned_today FROM users WHERE user_id=?", (uid,)).fetchone()
    con.close()
    return row

def save_user(uid, audience=None):
    con = db()
    row = con.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO users(user_id,audience,current_hadith,joined_date,learned_today) VALUES(?,?,?,?,0)",
            (uid, audience, 0, today())
        )
    elif audience:
        con.execute(
            "UPDATE users SET audience=?, joined_date=COALESCE(joined_date,?) WHERE user_id=?",
            (audience, today(), uid)
        )
    con.commit()
    con.close()

def set_current(uid, hid):
    con = db()
    con.execute("UPDATE users SET current_hadith=?, learned_today=0 WHERE user_id=?", (hid, uid))
    con.commit()
    con.close()

def mark_learned(uid):
    con = db()
    con.execute("UPDATE users SET learned_today=1 WHERE user_id=?", (uid,))
    con.commit()
    con.close()

# IMPORTANT:
# Do not put translated/generated hadiths here.
# hadiths.json is the source-of-truth dataset.
with open("hadiths.json", "r", encoding="utf-8") as f:
    HADITHS = json.load(f)

HADITH_BY_ID = {int(x["id"]): x for x in HADITHS}

def wrap(draw, text, fnt, max_width):
    words = str(text).split()
    lines, cur = [], ""
    for word in words:
        test = word if not cur else cur + " " + word
        if draw.textbbox((0,0), test, font=fnt)[2] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

def draw_centered_wrapped(draw, text, box, fnt, fill, spacing=10, max_lines=None):
    x1,y1,x2,y2 = box
    lines = wrap(draw, text, fnt, x2-x1)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while draw.textbbox((0,0), last+"…", font=fnt)[2] > x2-x1 and last:
            last = last[:-1]
        lines[-1] = last+"…"
    heights = [draw.textbbox((0,0), s, font=fnt)[3] for s in lines]
    total = sum(heights) + spacing*(len(lines)-1)
    y = y1 + max(0, ((y2-y1)-total)//2)
    for line,h in zip(lines, heights):
        bb = draw.textbbox((0,0), line, font=fnt)
        x = x1 + ((x2-x1)-(bb[2]-bb[0]))//2
        draw.text((x,y), line, font=fnt, fill=fill)
        y += h + spacing

def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def make_card(h):
    # The text is rendered by Pillow, not by an image generator.
    # This prevents Arabic/Uzbek text, number and source from being silently changed.
    img = Image.new("RGB", (W,H), "#0b3b2e")
    d = ImageDraw.Draw(img)

    # Background: mosque/sunset inspired by the supplied reference.
    for y in range(H):
        t = y/H
        r = int(24 + 36*t)
        g = int(82 - 28*t)
        b = int(66 - 42*t)
        d.line((0,y,W,y), fill=(r,g,b))

    # soft sunset
    for r in range(430, 10, -8):
        alpha = r/430
        cx, cy = 1210, 330
        rr = int(242 + 80*(1-alpha))
        gg = int(204 + 40*(1-alpha))
        bb = int(115 + 25*(1-alpha))
        d.ellipse((cx-r,cy-r,cx+r,cy+r), fill=(rr,gg,bb))

    # mosque silhouette
    ground = 1030
    d.rectangle((0,ground,W,H), fill="#173f32")
    dome_x, dome_y = 1120, 760
    d.ellipse((dome_x-190,dome_y-120,dome_x+190,dome_y+260), fill="#245443")
    d.rectangle((dome_x-190,dome_y+40,dome_x+190,ground), fill="#245443")
    for x in (930,1320):
        d.rectangle((x-28,470,x+28,ground), fill="#245443")
        d.polygon([(x-65,470),(x,385),(x+65,470)], fill="#245443")
        d.ellipse((x-8,360,x+8,390), fill="#d8a83b")

    # top brand panel
    rounded(d, (35,25,1500,205), 35, "#073d2e", "#e3b33f", 5)
    d.text((90,65), "ENG ZARUR ILM", font=font(64, True), fill="#f3d27b")
    d.text((95,135), "ISLOMIY BILIM BOTI", font=font(30), fill="#f3d27b")
    d.text((1190,65), "Ilm — qalbning", font=font(30), fill="#f6e4b1")
    d.text((1230,105), "nuridir", font=font(30), fill="#f6e4b1")

    # main cream card
    rounded(d, (55,235,1480,1275), 42, "#f8f1dc", "#e3b33f", 8)

    # title badge
    rounded(d, (455,205,1080,330), 35, "#073d2e", "#e3b33f", 7)
    d.text((545,238), "📖  BUGUNGI HADIS", font=font(42, True), fill="#f3d27b")

    # number
    rounded(d, (95,300,260,465), 35, "#073d2e", "#e3b33f", 5)
    d.text((138,325), str(h["id"]), font=font(70, True), fill="#f3d27b")
    d.text((112,405), "HADIS", font=font(26, True), fill="#f3d27b")

    # Arabic: exact value from dataset.
    ar = h.get("arabic", "").strip()
    if ar:
        ar_f = arabic_font(50 if len(ar) < 90 else 40)
        draw_centered_wrapped(d, ar, (290,350,1425,525), ar_f, "#123d32", spacing=12, max_lines=3)

    d.line((330,545,1205,545), fill="#d6a534", width=4)

    # Uzbek hadith text: do not rewrite here.
    uz = h["text"].strip()
    uz_f = font(37, True if len(uz) < 120 else False)
    draw_centered_wrapped(d, "“" + uz + "”", (170,570,1365,765), uz_f, "#173f32", spacing=9, max_lines=4)

    # Source
    source = h["source"].strip()
    rounded(d, (390,790,1145,865), 30, "#fffaf0", "#d6a534", 3)
    draw_centered_wrapped(d, source, (420,802,1115,852), font(25, True), "#173f32", spacing=2, max_lines=2)

    # Meaning panel
    rounded(d, (115,900,1420,1135), 30, "#edf0dc", None)
    d.text((170,925), "💡  HADIS MA’NOSI", font=font(31, True), fill="#173f32")
    meaning = h.get("meaning", "").strip()
    draw_centered_wrapped(d, meaning, (170,975,1360,1115), font(26), "#173f32", spacing=7, max_lines=5)

    # buttons printed on card
    rounded(d, (110,1160,720,1250), 40, "#073d2e", "#e3b33f", 5)
    rounded(d, (815,1160,1425,1250), 40, "#073d2e", "#e3b33f", 5)
    d.text((225,1185), "✓   Bilib oldim", font=font(31, True), fill="#fff5d7")
    d.text((930,1185), "↗   Ulashish", font=font(31, True), fill="#fff5d7")

    d.line((250,1320,1285,1320), fill="#d6a534", width=3)
    footer = "Yaxshi bilim — yaxshi amal sari qadam."
    bb = d.textbbox((0,0), footer, font=font(27))
    d.text(((W-(bb[2]-bb[0]))//2,1340), footer, font=font(27), fill="#f3d27b")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    out.seek(0)
    return out

async def send_hadith(bot, uid):
    row = get_user(uid)
    if not row or not row[1]:
        return
    # Sequential order. After the last entry, stay on the last verified entry.
    current = int(row[2] or 0)
    hid = current + 1
    if hid > len(HADITHS):
        hid = len(HADITHS)
    h = HADITH_BY_ID[hid]
    set_current(uid, hid)

    photo = make_card(h)
    share = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}&text={quote('Bugungi hadis: ' + h['text'])}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✓ Bilib oldim", callback_data=f"learn:{hid}")],
        [InlineKeyboardButton("↗ Ulashish", url=share)]
    ])
    await bot.send_photo(uid, photo=photo, caption=f"📖 Hadis №{hid}", reply_markup=kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    save_user(uid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👦 Bolalar uchun", callback_data="aud:children")],
        [InlineKeyboardButton("👤 Kattalar uchun", callback_data="aud:adults")]
    ])
    await update.message.reply_text(
        "Assalomu alaykum!\n\nKim uchun hadislar kerakligini tanlang:",
        reply_markup=kb
    )

async def audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    aud = q.data.split(":")[1]
    save_user(uid, aud)
    await q.edit_message_text("Tanlov saqlandi. Bugungi hadis tayyorlanmoqda…")
    await send_hadith(context.bot, uid)

async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Saqlab qo‘yildi ✓")
    mark_learned(q.from_user.id)
    await q.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Bilib oldim — saqlandi", callback_data="noop")]
        ])
    )

async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Bu hadis allaqachon saqlangan.")

async def next_hadith(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_hadith(context.bot, update.effective_user.id)

async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    con = db()
    rows = con.execute("SELECT user_id,joined_date,current_hadith FROM users WHERE audience IS NOT NULL").fetchall()
    con.close()
    today_s = today()
    for uid, joined, current in rows:
        # First day: already sent immediately after audience selection.
        if joined == today_s:
            continue
        # One hadith per day, strictly sequential.
        await send_hadith(context.bot, uid)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Eng_zarur_ilm_bot OK")
    def log_message(self, format, *args):
        pass

def health_server():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()

def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN topilmadi.")

    threading.Thread(target=health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("next", next_hadith))
    app.add_handler(CallbackQueryHandler(audience, pattern=r"^aud:"))
    app.add_handler(CallbackQueryHandler(learn, pattern=r"^learn:"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))

    # 06:00 Asia/Tashkent; first-day users are skipped by joined_date check.
    app.job_queue.run_daily(daily_job, time=datetime.strptime("06:00", "%H:%M").time(), name="daily_hadith")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
