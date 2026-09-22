import os
import sqlite3
import threading
import hashlib
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote_plus

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tashkent"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Eng_zarur_ilm_bot").replace("@", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
PORT = int(os.getenv("PORT", "10000"))
DB_FILE = os.getenv("DB_FILE", "hadis_bot.db")

BRAND = "ENG ZARUR ILM"
SUBTITLE = "Islomiy bilimlar bot"
BG = (13, 48, 38)
BG2 = (21, 69, 55)
GOLD = (214, 170, 74)
GOLD_LIGHT = (239, 211, 132)
CREAM = (249, 242, 221)
CREAM2 = (236, 224, 193)
WHITE = (255, 255, 255)
MUTED = (198, 204, 191)
DARK = (32, 48, 40)

HADITHS = [
    {
        "id": 1, "audience": "children", "topic": "Niyat",
        "arabic": "إِنَّمَا الأَعْمَالُ بِالنِّيَّاتِ، وَإِنَّمَا لِكُلِّ امْرِئٍ مَا نَوَى",
        "uzbek": "Amallar faqat niyatga bog‘liqdir. Har bir kishiga niyat qilgan narsasi bo‘ladi.",
        "lesson": "Yaxshi ishni boshlashdan oldin niyatimizni to‘g‘rilashni odat qilaylik.",
        "source": "Sahih al-Bukhari", "reference": "1", "grade": "Sahih",
        "source_url": "https://sunnah.com/bukhari:1",
    },
    {
        "id": 2, "audience": "children", "topic": "Yaxshi so‘z",
        "arabic": "فَلْيَقُلْ خَيْرًا أَوْ لِيَسْكُتْ",
        "uzbek": "Kim Allohga va oxirat kuniga iymon keltirgan bo‘lsa, yaxshi gapirsin yoki sukut qilsin.",
        "lesson": "Gapirishdan oldin so‘zimiz boshqalarga foyda beradimi, deb o‘ylash yaxshi odatdir.",
        "source": "Sahih Muslim", "reference": "47b", "grade": "Sahih",
        "source_url": "https://sunnah.com/muslim:47b",
    },
    {
        "id": 3, "audience": "children", "topic": "Mehmon va odob",
        "arabic": "فَلْيُكْرِمْ ضَيْفَهُ",
        "uzbek": "Kim Allohga va oxirat kuniga iymon keltirgan bo‘lsa, mehmonini hurmat qilsin.",
        "lesson": "Mehmonni hurmat qilish va odob bilan muomala qilish go‘zal xulqdir.",
        "source": "Sahih Muslim", "reference": "47b", "grade": "Sahih",
        "source_url": "https://sunnah.com/muslim:47b",
    },
    {
        "id": 4, "audience": "adults", "topic": "Ixlos",
        "arabic": "الدِّينُ النَّصِيحَةُ",
        "uzbek": "Din — samimiy nasihatdir.",
        "lesson": "Samimiyat, ixlos va yaxshilikni istash inson munosabatlarida muhim o‘rin tutadi.",
        "source": "Sahih Muslim", "reference": "55a", "grade": "Sahih",
        "source_url": "https://sunnah.com/muslim:55a",
    },
    {
        "id": 5, "audience": "adults", "topic": "Kuch va harakat",
        "arabic": "الْمُؤْمِنُ الْقَوِيُّ خَيْرٌ وَأَحَبُّ إِلَى اللَّهِ مِنَ الْمُؤْمِنِ الضَّعِيفِ",
        "uzbek": "Kuchli mo‘min Allohga zaif mo‘mindan ko‘ra yaxshiroq va suyukliroqdir. Har ikkisida ham yaxshilik bor. Senga foyda beradigan narsaga intil, Allohdan yordam so‘ra va ojizlanib qolma.",
        "lesson": "Foydali ishga intilish, Allohdan yordam so‘rash va harakatni tark etmaslik.",
        "source": "Sahih Muslim", "reference": "2664", "grade": "Sahih",
        "source_url": "https://sunnah.com/muslim:2664",
    },
    {
        "id": 6, "audience": "adults", "topic": "Yaxshi so‘z",
        "arabic": "فَلْيَقُلْ خَيْرًا أَوْ لِيَسْكُتْ",
        "uzbek": "Kim Allohga va oxirat kuniga iymon keltirgan bo‘lsa, yaxshi gapirsin yoki sukut qilsin.",
        "lesson": "So‘zning ham mas’uliyati bor. Foydali gapni aytish, zararli gapdan tiyilish go‘zal odobdir.",
        "source": "Sahih Muslim", "reference": "47b", "grade": "Sahih",
        "source_url": "https://sunnah.com/muslim:47b",
    },
]

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        username TEXT DEFAULT '',
        audience TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        last_seen TEXT,
        current_hadith INTEGER,
        current_day TEXT
    );
    CREATE TABLE IF NOT EXISTS learned (
        uid INTEGER NOT NULL,
        day TEXT NOT NULL,
        hadith_id INTEGER NOT NULL,
        learned_at TEXT,
        PRIMARY KEY(uid, day)
    );
    CREATE TABLE IF NOT EXISTS hadiths (
        id INTEGER PRIMARY KEY,
        audience TEXT NOT NULL,
        topic TEXT NOT NULL,
        arabic TEXT NOT NULL,
        uzbek TEXT NOT NULL,
        lesson TEXT NOT NULL,
        source TEXT NOT NULL,
        reference TEXT NOT NULL,
        grade TEXT NOT NULL,
        source_url TEXT NOT NULL
    );
    """)
    for h in HADITHS:
        conn.execute("""
        INSERT OR REPLACE INTO hadiths
        (id, audience, topic, arabic, uzbek, lesson, source, reference, grade, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, tuple(h[k] for k in (
            "id","audience","topic","arabic","uzbek","lesson",
            "source","reference","grade","source_url"
        )))
    conn.commit()
    conn.close()

def today():
    return datetime.now(TIMEZONE).date().isoformat()

def now_text():
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

def save_user(user):
    conn = db()
    conn.execute("""
    INSERT INTO users (id, name, username, created_at, last_seen)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        name=excluded.name,
        username=excluded.username,
        last_seen=excluded.last_seen
    """, (user.id, user.full_name or "Foydalanuvchi", user.username or "", now_text(), now_text()))
    conn.commit()
    conn.close()

def get_user(uid):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return row

def get_hadith_for_user(uid, audience=None):
    conn = db()
    if audience is None:
        row = conn.execute("SELECT audience FROM users WHERE id=?", (uid,)).fetchone()
        if not row or not row["audience"]:
            conn.close()
            return None
        audience = row["audience"]
    rows = conn.execute("SELECT * FROM hadiths WHERE audience=? ORDER BY id", (audience,)).fetchall()
    if not rows:
        conn.close()
        return None
    seed = f"{today()}:{audience}"
    digest = hashlib.sha256(seed.encode()).hexdigest()
    index = int(digest[:12], 16) % len(rows)
    h = rows[index]
    conn.execute("UPDATE users SET current_hadith=?, current_day=? WHERE id=?", (h["id"], today(), uid))
    conn.commit()
    conn.close()
    return h

def font_candidates():
    return [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

def get_font(size, bold=False):
    candidates = font_candidates()
    if bold:
        candidates = [
            p.replace(".ttf", "-Bold.ttf") if "DejaVuSans.ttf" in p else p
            for p in candidates
        ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
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

def draw_centered_multiline(draw, text, box, font, fill, spacing=10):
    x1, y1, x2, y2 = box
    lines = wrap_text(draw, text, font, x2 - x1)
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total = sum(heights) + spacing * max(0, len(lines) - 1)
    y = y1 + max(0, ((y2 - y1) - total) / 2)
    for line, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = x1 + ((x2 - x1) - w) / 2
        draw.text((x, y), line, font=font, fill=fill)
        y += h + spacing

def arabic_text(text):
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text

def make_premium_card(hadith):
    width, height = 1080, 1350
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / height
        c = tuple(int(BG[i] + (BG2[i] - BG[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=c)
    for radius in (300, 240, 180):
        draw.ellipse((width-radius, -radius//2, width+radius, radius//2), outline=GOLD, width=3)
    draw.rounded_rectangle((35,35,width-35,height-35), radius=45, outline=GOLD, width=4)
    draw.rounded_rectangle((55,55,width-55,height-55), radius=38, outline=GOLD_LIGHT, width=1)

    logo_font, small_font = get_font(38, True), get_font(23)
    draw.text((80,82), BRAND, font=logo_font, fill=GOLD_LIGHT)
    draw.text((82,132), SUBTITLE.upper(), font=small_font, fill=MUTED)

    badge_text, badge_font = "BUGUNGI HADIS", get_font(24, True)
    badge_box = (80,195,390,250)
    draw.rounded_rectangle(badge_box, radius=26, fill=GOLD)
    bbox = draw.textbbox((0,0), badge_text, font=badge_font)
    draw.text(((badge_box[0]+badge_box[2]-(bbox[2]-bbox[0]))/2,209), badge_text, font=badge_font, fill=DARK)

    panel = (70,280,width-70,1060)
    draw.rounded_rectangle(panel, radius=42, fill=CREAM)
    draw.rounded_rectangle((panel[0]+12,panel[1]+12,panel[2]-12,panel[3]-12), radius=34, outline=GOLD, width=2)

    topic_font = get_font(25, True)
    draw.text((110,315), hadith["topic"].upper(), font=topic_font, fill=(105,83,36))

    arabic_font = get_font(43, True)
    draw_centered_multiline(draw, arabic_text(hadith["arabic"]), (115,365,width-115,500), arabic_font, DARK, 8)

    draw.line((125,525,width-125,525), fill=GOLD, width=2)

    uz_font = get_font(34, True)
    draw_centered_multiline(draw, f"“{hadith['uzbek']}”", (125,555,width-125,770), uz_font, DARK, 10)

    lesson_box = (115,800,width-115,950)
    draw.rounded_rectangle(lesson_box, radius=25, fill=CREAM2, outline=GOLD, width=2)
    draw.text((145,825), "BUGUNGI SABOQ", font=get_font(23, True), fill=(105,83,36))
    draw_centered_multiline(draw, hadith["lesson"], (145,865,width-145,935), get_font(27), DARK, 6)

    draw.text((115,975), "MANBA", font=get_font(23, True), fill=(105,83,36))
    draw.text((115,1015), f"{hadith['source']}  •  Hadis {hadith['reference']}  •  {hadith['grade']}",
              font=get_font(22), fill=DARK)

    footer = "Yaxshi bilim — yaxshi amal sari qadam."
    footer_font = get_font(27, True)
    bbox = draw.textbbox((0,0), footer, font=footer_font)
    draw.text(((width-(bbox[2]-bbox[0]))/2,1125), footer, font=footer_font, fill=GOLD_LIGHT)
    draw.line((350,1190,730,1190), fill=GOLD, width=2)
    draw.ellipse((530,1178,550,1198), fill=GOLD)

    path = f"/tmp/hadith_{hadith['id']}_{today()}.png"
    img.save(path, "PNG", optimize=True)
    return path

def main_menu():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📖 Bugungi hadis"), KeyboardButton("📊 Statistikam")],
        [KeyboardButton("🏆 Reyting"), KeyboardButton("🎯 Sozlamalar")],
        [KeyboardButton("📤 Do‘stlarga ulashish"), KeyboardButton("ℹ️ Yordam")],
    ], resize_keyboard=True, is_persistent=True)

def audience_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👦 Bolalar uchun", callback_data="aud:children")],
        [InlineKeyboardButton("👨 Kattalar uchun", callback_data="aud:adults")],
    ])

def hadith_buttons(hadith):
    share_text = f"📖 Bugungi hadis\n\n{hadith['uzbek']}\n\nManba: {hadith['source']} {hadith['reference']}\n\n@{BOT_USERNAME}"
    share_url = "https://t.me/share/url?" + f"url=https://t.me/{BOT_USERNAME}&text={quote_plus(share_text)}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Bilib oldim", callback_data=f"learn:{hadith['id']}"),
         InlineKeyboardButton("📤 Ulashish", url=share_url)],
        [InlineKeyboardButton("📚 Yana ma'lumot", callback_data=f"info:{hadith['id']}")]
    ])

async def start(update, context):
    user = update.effective_user
    save_user(user)
    row = get_user(user.id)
    if row and row["audience"]:
        await update.message.reply_text(
            f"Assalomu alaykum, {user.first_name}! 🌙\n\n"
            "Bugungi foydali ilmingizni olishga tayyormisiz?\n\n"
            "📖 Har kuni siz uchun hadis\n🌱 O‘rganilgan hadislar statistikasi\n"
            "🏆 Faollar reytingi\n🎯 Shaxsiy natijalar",
            reply_markup=main_menu())
        return
    await update.message.reply_text(
        "Assalomu alaykum! 🌙\n\n✨ ENG ZARUR ILM botiga xush kelibsiz.\n\n"
        "Bu yerda har kuni qisqa va foydali hadislar orqali yangi bilim olish imkoniga ega bo‘lasiz.\n\n"
        "Avval hadislar kim uchun kerakligini tanlang:",
        reply_markup=audience_menu())

async def callbacks(update, context):
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if query.data.startswith("aud:"):
        audience = query.data.split(":",1)[1]
        conn = db()
        conn.execute("UPDATE users SET audience=? WHERE id=?", (audience,user.id))
        conn.commit(); conn.close()
        title = "👦 Bolalar uchun" if audience == "children" else "👨 Kattalar uchun"
        await query.edit_message_text(f"✅ Tanlov saqlandi: {title}\n\nEndi sizga mos hadislar yuboriladi.")
        await context.bot.send_message(user.id,
            "🌿 Tayyor!\n\nHar kuni sizga foydali hadis va uning qisqa sabog‘i yetib boradi.\n\nBilimingizni muntazam davom ettiring. 🤍",
            reply_markup=main_menu())
        return

    if query.data.startswith("learn:"):
        hadith_id = int(query.data.split(":",1)[1])
        conn = db()
        exists = conn.execute("SELECT 1 FROM learned WHERE uid=? AND day=?", (user.id,today())).fetchone()
        if exists:
            conn.close()
            await query.answer("Bugungi hadis allaqachon belgilangan ✅", show_alert=True)
            return
        conn.execute("INSERT INTO learned(uid,day,hadith_id,learned_at) VALUES(?,?,?,?)",
                     (user.id,today(),hadith_id,now_text()))
        conn.commit(); conn.close()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(
            "🌱 Juda yaxshi!\n\nBugungi hadis o‘rganildi.\n\n"
            f"🔥 Ketma-ketlik: {calculate_streak(user.id)} kun\n\n"
            "Ertangi foydali hadisni ham kutib qoling.",
            reply_markup=main_menu())
        return

    if query.data.startswith("info:"):
        hid = int(query.data.split(":",1)[1])
        conn = db(); h = conn.execute("SELECT * FROM hadiths WHERE id=?", (hid,)).fetchone(); conn.close()
        if h:
            await query.message.reply_text(
                "📚 HADIS MA'LUMOTI\n\n"
                f"📌 Mavzu: {h['topic']}\n📖 Manba: {h['source']}\n"
                f"🔢 Raqam: {h['reference']}\n✅ Daraja: {h['grade']}\n\n🔎 Manba:\n{h['source_url']}")

async def send_hadith_to_user(bot, uid, forced_audience=None):
    row = get_user(uid)
    if not row: return False
    audience = forced_audience or row["audience"]
    if not audience: return False
    hadith = get_hadith_for_user(uid, audience)
    if not hadith: return False
    path = make_premium_card(hadith)
    try:
        with open(path, "rb") as photo:
            await bot.send_photo(
                uid, photo=photo,
                caption=f"📖 <b>BUGUNGI HADIS</b>\n\n<i>{hadith['source']} • {hadith['reference']} • {hadith['grade']}</i>",
                parse_mode="HTML", reply_markup=hadith_buttons(hadith))
        return True
    except Exception as e:
        print("Hadis yuborishda xato:", e)
        return False

async def today_hadith(update, context):
    user = update.effective_user
    save_user(user)
    row = get_user(user.id)
    if not row or not row["audience"]:
        await update.message.reply_text("Avval sizga mos hadis turini tanlang:", reply_markup=audience_menu())
        return
    await update.message.reply_text("📖 Bugungi hadis tayyorlanmoqda...")
    await send_hadith_to_user(context.bot, user.id)

def calculate_streak(uid):
    conn = db()
    rows = conn.execute("SELECT day FROM learned WHERE uid=? ORDER BY day DESC", (uid,)).fetchall()
    conn.close()
    if not rows: return 0
    days = {datetime.fromisoformat(r["day"]).date() for r in rows}
    current = datetime.now(TIMEZONE).date()
    if current not in days: current -= timedelta(days=1)
    streak = 0
    while current in days:
        streak += 1
        current -= timedelta(days=1)
    return streak

async def statistics(update, context):
    uid = update.effective_user.id
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM learned WHERE uid=?", (uid,)).fetchone()[0]
    last = conn.execute("SELECT day FROM learned WHERE uid=? ORDER BY day DESC LIMIT 1", (uid,)).fetchone()
    conn.close()
    await update.message.reply_text(
        "📊 SHAXSIY STATISTIKA\n\n"
        f"📖 O‘rganilgan hadislar: {total}\n🔥 Ketma-ket kunlar: {calculate_streak(uid)}\n"
        f"📅 Oxirgi natija: {last['day'] if last else 'Hali yo‘q'}\n\n"
        "🌱 Har bir o‘rganilgan hadis — bilim yo‘lidagi yana bir qadam.")

async def rating(update, context):
    conn = db()
    rows = conn.execute("""
        SELECT users.name, COUNT(learned.uid) AS total
        FROM users LEFT JOIN learned ON learned.uid=users.id
        GROUP BY users.id HAVING total>0
        ORDER BY total DESC, users.name ASC LIMIT 10
    """).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("🏆 Reyting hali shakllanmagan.\n\nBirinchi hadisni o‘rganib, reytingga kiring!")
        return
    medals = ["🥇","🥈","🥉"]
    text = "🏆 <b>ENG FAOL O‘RGANUVCHILAR</b>\n\n"
    for i,r in enumerate(rows):
        text += f"{medals[i] if i<3 else str(i+1)+'.'} <b>{r['name']}</b> — {r['total']} hadis\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def settings(update, context):
    row = get_user(update.effective_user.id)
    if not row or not row["audience"]:
        await update.message.reply_text("Hadis turini tanlang:", reply_markup=audience_menu())
        return
    audience = "👦 Bolalar uchun" if row["audience"]=="children" else "👨 Kattalar uchun"
    await update.message.reply_text(f"🎯 SOZLAMALAR\n\nTanlangan yo‘nalish: {audience}\n\nYo‘nalishni almashtirish:",
                                    reply_markup=audience_menu())

async def share(update, context):
    share_url = "https://t.me/share/url?" + f"url=https://t.me/{BOT_USERNAME}&text={quote_plus('📖 Men ENG ZARUR ILM botidan har kuni foydali hadis olaman. Siz ham qo‘shiling!')}"
    await update.message.reply_text(
        "📤 Do‘stlaringizga ulashing:\n\nHar kuni bitta foydali hadis — bilim va yaxshi odatlar uchun kichik, ammo foydali qadam.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Telegramda ulashish", url=share_url)]]))

async def help_command(update, context):
    await update.message.reply_text(
        "ℹ️ <b>ENG ZARUR ILM</b>\n\n"
        "Bu bot har kuni foydali hadislar orqali bilimingizni oshirishga yordam beradi.\n\n"
        "📖 Bugungi hadis — bugungi hadisni olish\n📊 Statistikam — shaxsiy natijalar\n"
        "🏆 Reyting — eng faol o‘rganuvchilar\n🎯 Sozlamalar — hadis yo‘nalishini almashtirish\n"
        "📤 Ulashish — do‘stlaringizga yuborish\n\n🌱 Oz-ozdan, ammo muntazam o‘rganing.",
        parse_mode="HTML", reply_markup=main_menu())

def is_admin(uid): return uid in ADMIN_IDS

async def admin(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Bu bo‘lim faqat admin uchun.")
        return
    conn=db()
    users=conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active=conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
    learned=conn.execute("SELECT COUNT(*) FROM learned").fetchone()[0]
    conn.close()
    await update.message.reply_text(
        "👑 <b>ADMIN PANEL</b>\n\n"
        f"👥 Jami foydalanuvchilar: {users}\n🟢 Faol foydalanuvchilar: {active}\n📖 O‘rganilgan hadislar: {learned}\n\n"
        "Buyruqlar:\n/test_hadis — hadisni hozir sinash\n/broadcast — barcha foydalanuvchilarga xabar\n/admin — statistika",
        parse_mode="HTML")

async def test_hadith(update, context):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("🧪 Premium hadis karta testi...")
    for label, audience in [("BOLALAR UCHUN TEST","children"),("KATTALAR UCHUN TEST","adults")]:
        h = next((x for x in HADITHS if x["audience"]==audience), None)
        if h:
            path=make_premium_card(h)
            with open(path,"rb") as f:
                await update.message.reply_photo(f, caption=f"🧪 <b>{label}</b>\n\n{h['source']} {h['reference']}", parse_mode="HTML")

async def broadcast(update, context):
    if not is_admin(update.effective_user.id): return
    context.user_data["broadcast_mode"]=True
    await update.message.reply_text("📢 E'lon matnini yuboring.\n\nBekor qilish: /cancel")

async def cancel(update, context):
    context.user_data["broadcast_mode"]=False
    await update.message.reply_text("❌ Bekor qilindi.")

async def broadcast_message(update, context):
    if not context.user_data.get("broadcast_mode"): return False
    if not is_admin(update.effective_user.id): return True
    context.user_data["broadcast_mode"]=False
    conn=db(); users=conn.execute("SELECT id FROM users WHERE active=1").fetchall(); conn.close()
    sent=failed=0
    await update.message.reply_text(f"📢 E'lon {len(users)} ta foydalanuvchiga yuborilmoqda...")
    for row in users:
        try:
            await update.message.copy(chat_id=row["id"])
            sent += 1
        except Exception as e:
            failed += 1
            print("Broadcast xatosi:", row["id"], e)
    await update.message.reply_text(f"✅ E'lon yakunlandi.\n\nYuborildi: {sent}\nYetkazilmadi: {failed}")
    return True

async def text_router(update, context):
    if context.user_data.get("broadcast_mode"):
        if await broadcast_message(update, context): return
    text=(update.message.text or "").strip()
    if text=="📖 Bugungi hadis": await today_hadith(update,context)
    elif text=="📊 Statistikam": await statistics(update,context)
    elif text=="🏆 Reyting": await rating(update,context)
    elif text=="🎯 Sozlamalar": await settings(update,context)
    elif text=="📤 Do‘stlarga ulashish": await share(update,context)
    elif text=="ℹ️ Yordam": await help_command(update,context)

async def daily_hadith_job(context):
    conn=db(); users=conn.execute("SELECT id FROM users WHERE active=1 AND audience IS NOT NULL").fetchall(); conn.close()
    print(f"[DAILY] {len(users)} foydalanuvchiga hadis yuboriladi.")
    for row in users:
        try: await send_hadith_to_user(context.bot,row["id"])
        except Exception as e: print("Daily xato:",row["id"],e)

async def reminder_job(context):
    conn=db(); users=conn.execute("SELECT id FROM users WHERE active=1 AND audience IS NOT NULL").fetchall(); conn.close()
    for row in users:
        uid=row["id"]
        conn=db(); learned=conn.execute("SELECT 1 FROM learned WHERE uid=? AND day=?", (uid,today())).fetchone(); conn.close()
        if learned: continue
        try:
            await context.bot.send_message(uid,
                "🌱 Bugungi foydali hadis sizni kutmoqda.\n\nBir necha daqiqa ajratib, bugungi hadisni o‘qib chiqing va “Bilib oldim” tugmasini bosing. 🤍",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📖 Bugungi hadis", callback_data="today:open")]]))
        except Exception as e: print("Reminder xato:",uid,e)

async def today_callback(update, context):
    query=update.callback_query; await query.answer()
    await send_hadith_to_user(context.bot,query.from_user.id)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type","text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"ENG ZARUR ILM BOT OK")
    def log_message(self, format, *args): return

def start_health_server():
    server=HTTPServer(("0.0.0.0",PORT),HealthHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    print(f"Health server started on port {PORT}")

def main():
    if not TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN Environment Variable kerak.")
    init_db(); start_health_server()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("help",help_command))
    app.add_handler(CommandHandler("rating",rating))
    app.add_handler(CommandHandler("stats",statistics))
    app.add_handler(CommandHandler("admin",admin))
    app.add_handler(CommandHandler("test_hadis",test_hadith))
    app.add_handler(CommandHandler("broadcast",broadcast))
    app.add_handler(CommandHandler("cancel",cancel))
    app.add_handler(CallbackQueryHandler(today_callback,pattern=r"^today:open$"))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_router))
    app.job_queue.run_daily(daily_hadith_job,time=time(6,0,tzinfo=TIMEZONE),name="daily_hadith")
    app.job_queue.run_daily(reminder_job,time=time(12,0,tzinfo=TIMEZONE),name="reminder_1")
    app.job_queue.run_daily(reminder_job,time=time(20,0,tzinfo=TIMEZONE),name="reminder_2")
    print("ENG ZARUR ILM BOT ISHLAMOQDA")
    app.run_polling(allowed_updates=Update.ALL_TYPES,drop_pending_updates=True)

if __name__=="__main__":
    main()
