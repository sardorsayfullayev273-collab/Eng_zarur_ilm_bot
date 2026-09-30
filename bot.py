import os, json, asyncio, hashlib, hmac, urllib.parse, secrets, calendar, io
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
import uvicorn
from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    WebAppInfo, MenuButtonWebApp
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent").strip() or "Asia/Tashkent"
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
CERT_MIN_HADITHS = int(os.getenv("CERT_MIN_HADITHS", "30"))
TZ = ZoneInfo(TIMEZONE)
BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, "hadiths.json"), encoding="utf-8") as f:
    HADITHS = sorted(json.load(f), key=lambda x: int(x.get("id", 0)))
HMAP = {int(h["id"]): h for h in HADITHS}
MAX_HADITH = max(HMAP) if HMAP else 0

app = FastAPI(title="Eng zarur ilm")
pool = None
bot_app = None

MENU = [
    ["📖 Bugungi hadis", "📊 Statistikam"],
    ["🏆 Reyting", "🔥 Streakim"],
    ["🎯 Sozlamalar", "📤 Do‘stlarga ulashish"],
    ["ℹ️ Yordam"],
]

def now():
    return datetime.now(TZ)

def reply_keyboard():
    return ReplyKeyboardMarkup(MENU, resize_keyboard=True, is_persistent=True)

def font(size, bold=False):
    names = [
        os.path.join(BASE, "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for n in names:
        if os.path.exists(n):
            return ImageFont.truetype(n, size)
    return ImageFont.load_default()

def wrap_text(draw, text, fnt, max_width):
    words = str(text or "").split()
    lines, line = [], ""
    for word in words:
        test = word if not line else line + " " + word
        if draw.textbbox((0, 0), test, font=fnt)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines

def make_hadith_card(h):
    template = os.path.join(BASE, "premium_template.png")

    if os.path.exists(template):
        img = Image.open(template).convert("RGB").resize(
            (1080, 1350), Image.LANCZOS
        )
    else:
        img = Image.new("RGB", (1080, 1350), (10, 36, 31))

    d = ImageDraw.Draw(img)

    GOLD = (214, 169, 67)
    DARK_GREEN = (8, 58, 48)
    TEXT = (49, 54, 48)
    MUTED = (78, 78, 70)

    # Sarlavha
    d.text(
        (540, 218),
        "BUGUNGI HADIS",
        font=font(34, True),
        fill=DARK_GREEN,
        anchor="ma"
    )

    # Hadis matni
    text = str(h.get("text", "")).strip()

    size = 36

    while size >= 24:
        fnt = font(size)
        lines = wrap_text(d, text, fnt, 830)

        if len(lines) <= 11:
            break

        size -= 2

    y = 300
    line_gap = max(12, int(size * 0.35))

    for line in lines[:11]:
        d.text(
            (540, y),
            line,
            font=fnt,
            fill=TEXT,
            anchor="ma"
        )

        y += size + line_gap

    if len(lines) > 11:
        d.text(
            (540, y),
            "…",
            font=font(size, True),
            fill=TEXT,
            anchor="ma"
        )

    # Hadis ma'nosi
    meaning = str(h.get("meaning", "")).strip()

    if meaning:

        box_top = min(y + 20, 820)

        d.rounded_rectangle(
            (125, box_top, 955, min(box_top + 155, 930)),
            radius=20,
            fill=(245, 239, 225),
            outline=(221, 190, 112),
            width=2
        )

        d.text(
            (540, box_top + 20),
            "HADIS MA’NOSI",
            font=font(23, True),
            fill=DARK_GREEN,
            anchor="ma"
        )

        meaning_font = font(21)

        meaning_lines = wrap_text(
            d,
            meaning,
            meaning_font,
            720
        )

        my = box_top + 58

        for line in meaning_lines[:4]:

            d.text(
                (540, my),
                line,
                font=meaning_font,
                fill=MUTED,
                anchor="ma"
            )

            my += 30

    # Manba
    source = str(h.get("source", "")).strip()

    d.text(
        (540, 955),
        "📚 " + source[:100],
        font=font(18, True),
        fill=MUTED,
        anchor="ma"
    )

    # Yangi footer
    d.text(
        (540, 1000),
        "Hadis ilmini o‘rganishda niyatingiz to‘g‘ri bo‘lsin.",
        font=font(20, True),
        fill=DARK_GREEN,
        anchor="ma"
    )

    d.text(
        (540, 1030),
        "Tahoratli holda o‘qisangiz, nur ustiga nurdir.",
        font=font(19),
        fill=GOLD,
        anchor="ma"
    )

    # Hadis raqami
    d.text(
        (1015, 955),
        f"#{h.get('id')}",
        font=font(19, True),
        fill=GOLD,
        anchor="ra"
    )

    out = io.BytesIO()

    img.save(
        out,
        "PNG",
        optimize=True
    )

    out.seek(0)

    return out
def make_certificate(user_name, year, month, count):
    W, H = 1600, 1100
    img = Image.new("RGB", (W, H), (8, 20, 24))
    d = ImageDraw.Draw(img)
    # Gold/mint border and Islamic geometric-style decoration.
    d.rectangle((35, 35, W-35, H-35), outline=(221, 187, 101), width=5)
    d.rectangle((55, 55, W-55, H-55), outline=(87, 210, 171), width=2)
    for x in range(100, W-100, 100):
        d.ellipse((x-3, 105, x+3, 111), fill=(110, 220, 181))
        d.ellipse((x-3, H-111, x+3, H-105), fill=(221, 187, 101))
    d.text((W//2, 125), "ENG ZARUR ILM", font=font(54, True), fill=(125, 241, 201), anchor="ma")
    d.text((W//2, 205), "ISLOMIY BILIM BOTI", font=font(27, True), fill=(226, 193, 110), anchor="ma")
    d.text((W//2, 310), "SERTIFIKAT", font=font(76, True), fill=(242, 247, 242), anchor="ma")
    d.text((W//2, 410), "Ushbu sertifikat", font=font(28), fill=(190, 204, 202), anchor="ma")
    safe_name = user_name[:40]
    d.text((W//2, 485), safe_name, font=font(48, True), fill=(242, 247, 242), anchor="ma")
    d.text((W//2, 585),
           f"{month} {year}-yil davomida {count} ta hadisni o‘rganib,",
           font=font(27), fill=(205, 217, 213), anchor="ma")
    d.text((W//2, 635),
           "“Bilib oldim” orqali bilim odatini muntazam davom ettirgani uchun",
           font=font(25), fill=(205, 217, 213), anchor="ma")
    d.text((W//2, 735), "taqdirlanadi.", font=font(31, True), fill=(125, 241, 201), anchor="ma")
    d.text((W//2, 865), f"Mezon: oyiga kamida {CERT_MIN_HADITHS} ta hadis",
           font=font(24, True), fill=(226, 193, 110), anchor="ma")
    d.text((W//2, 930), "Yaxshi odatlar — baxt kaliti", font=font(22), fill=(165, 180, 178), anchor="ma")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    out.seek(0)
    return out

def verify_init_data(init_data):
    if not init_data:
        raise HTTPException(401, "Telegram Mini App ichidan oching.")
    q = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received = q.pop("hash", None)
    if not received:
        raise HTTPException(401, "Telegram initData hash yo‘q.")
    data_check = "\n".join(f"{k}={q[k]}" for k in sorted(q))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received):
        raise HTTPException(401, "Telegram initData noto‘g‘ri.")
    try:
        return json.loads(q.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(401, "Telegram user ma’lumoti noto‘g‘ri.")

def user_from_request(request):
    return verify_init_data(request.headers.get("X-Telegram-Init-Data", ""))

async def db_init():
    global pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL Render PostgreSQL ga ulanmagan.")
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=8)
    async with pool.acquire() as c:
        await c.execute("""
            CREATE TABLE IF NOT EXISTS users(
                id BIGINT PRIMARY KEY,
                username TEXT, first_name TEXT, last_name TEXT,
                audience TEXT NOT NULL DEFAULT 'both',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                streak INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                last_learned DATE,
                notifications BOOLEAN NOT NULL DEFAULT TRUE,
                last_daily_sent DATE,
                ref_code TEXT UNIQUE,
                referred_by BIGINT REFERENCES users(id) ON DELETE SET NULL
            )
        """)
        await c.execute("""
            CREATE TABLE IF NOT EXISTS learned(
                user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                hadith_id INTEGER NOT NULL,
                learned_on DATE NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(user_id, hadith_id)
            )
        """)
        await c.execute("""
            CREATE TABLE IF NOT EXISTS daily_deliveries(
                user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                day DATE NOT NULL,
                hadith_id INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(user_id, day)
            )
        """)
        await c.execute("""
            CREATE TABLE IF NOT EXISTS referrals(
                referrer_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                invited_id BIGINT UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await c.execute("""
            CREATE TABLE IF NOT EXISTS app_events(
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT,
                event TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await c.execute("""
            CREATE TABLE IF NOT EXISTS certificates(
                user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                month_key TEXT NOT NULL,
                count INTEGER NOT NULL,
                issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY(user_id, month_key)
            )
        """)
        await c.execute("CREATE INDEX IF NOT EXISTS learned_day_idx ON learned(learned_on)")
        await c.execute("CREATE INDEX IF NOT EXISTS events_user_idx ON app_events(user_id, created_at)")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT REFERENCES users(id) ON DELETE SET NULL")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_sent DATE")

async def ensure_user_data(tg_user, referred_by=None):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM users WHERE id=$1", tg_user.id)
        if row:
            await c.execute(
                "UPDATE users SET username=$2, first_name=$3, last_name=$4, last_seen=now() WHERE id=$1",
                tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name)
            return row
        ref = secrets.token_urlsafe(8).replace("-", "_").replace("=", "")
        await c.execute(
            """INSERT INTO users(id,username,first_name,last_name,ref_code,referred_by)
               VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING""",
            tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name, ref, referred_by)
        if referred_by and referred_by != tg_user.id:
            await c.execute(
                "INSERT INTO referrals(referrer_id,invited_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                referred_by, tg_user.id)
            await c.execute("UPDATE users SET points=points+5 WHERE id=$1", referred_by)
        return await c.fetchrow("SELECT * FROM users WHERE id=$1", tg_user.id)

async def next_hadith_for_user(uid):
    today = now().date()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT hadith_id FROM daily_deliveries WHERE user_id=$1 AND day=$2", uid, today)
        if row:
            return HMAP.get(int(row["hadith_id"]))
        last = await c.fetchval(
            "SELECT max(hadith_id) FROM daily_deliveries WHERE user_id=$1", uid)
    last_id = int(last or 0)
    if last_id >= MAX_HADITH:
        return None
    return HMAP.get(last_id + 1)

async def current_hadith(uid):
    return await next_hadith_for_user(uid)

async def reserve_today_hadith(uid):
    today = now().date()
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT hadith_id FROM daily_deliveries WHERE user_id=$1 AND day=$2", uid, today)
        if row:
            return HMAP.get(int(row["hadith_id"]))
        last = await c.fetchval(
            "SELECT max(hadith_id) FROM daily_deliveries WHERE user_id=$1", uid)
        hid = int(last or 0) + 1
        if hid > MAX_HADITH:
            return None
        await c.execute(
            """INSERT INTO daily_deliveries(user_id,day,hadith_id)
               VALUES($1,$2,$3) ON CONFLICT (user_id,day) DO NOTHING""",
            uid, today, hid)
        return HMAP.get(hid)

async def mark_learned(uid, hid):
    if hid not in HMAP:
        return False, None
    today = now().date()
    async with pool.acquire() as c:
        exists = await c.fetchrow(
            "SELECT 1 FROM learned WHERE user_id=$1 AND hadith_id=$2", uid, hid)
        if exists:
            return False, None
        await c.execute(
            "INSERT INTO learned(user_id,hadith_id,learned_on) VALUES($1,$2,$3)",
            uid, hid, today)
        row = await c.fetchrow(
            "SELECT streak,last_learned,points FROM users WHERE id=$1", uid)
        streak = int(row["streak"] or 0)
        last = row["last_learned"]
        if last == today - timedelta(days=1):
            streak += 1
        elif last != today:
            streak = 1
        await c.execute(
            "UPDATE users SET streak=$2,last_learned=$3,points=points+10,last_seen=now() WHERE id=$1",
            uid, streak, today)
        await c.execute("INSERT INTO app_events(user_id,event) VALUES($1,'learned')", uid)
        month_key = today.strftime("%Y-%m")
        count = await c.fetchval(
            "SELECT count(*) FROM learned WHERE user_id=$1 AND date_trunc('month',learned_on)=date_trunc('month',$2::date)",
            uid, today)
        cert = None
        if int(count) >= CERT_MIN_HADITHS:
            ins = await c.fetchrow(
                """INSERT INTO certificates(user_id,month_key,count)
                   VALUES($1,$2,$3) ON CONFLICT DO NOTHING RETURNING user_id""",
                uid, month_key, int(count))
            if ins:
                cert = {"month_key": month_key, "count": int(count)}
                await c.execute(
                    "INSERT INTO app_events(user_id,event) VALUES($1,'certificate_issued')", uid)
        return True, cert

def card_text(h):
    out = [f"📖 BUGUNGI HADIS\n\n{h['text'].strip()}"]
    if h.get("meaning"):
        out.append(f"💡 Hadis ma’nosi\n{h['meaning'].strip()}")
    out.append(f"📚 Manba: {h.get('source','')}")
    return "\n\n".join(out)

async def send_hadith(chat_id, context, uid, force_new=False):
    h = await reserve_today_hadith(uid)
    if not h:
        await context.bot.send_message(
            chat_id,
            f"🎉 Siz mavjud {MAX_HADITH} ta hadisning kundalik ketma-ketligini tugatdingiz. "
            "Yangi manba qo‘shilgach, ketma-ketlik davom etadi.")
        return
    buttons = [InlineKeyboardButton("✅ Bilib oldim", callback_data=f"learn:{h['id']}"),
               InlineKeyboardButton("📤 Ulashish", callback_data=f"share:{h['id']}")]
    if WEBAPP_URL:
        buttons.append(InlineKeyboardButton("📱 Mini App", web_app=WebAppInfo(WEBAPP_URL)))
    await context.bot.send_photo(
        chat_id, photo=make_hadith_card(h),
        caption="🌙 Har kuni bir hadis — har kuni bir qadam.",
        reply_markup=InlineKeyboardMarkup([buttons]))

async def issue_certificate(chat_id, context, uid, cert):
    if not cert:
        return
    async with pool.acquire() as c:
        r = await c.fetchrow("SELECT first_name,last_name,username FROM users WHERE id=$1", uid)
    name = " ".join([x for x in [r["first_name"], r["last_name"]] if x]) if r else "Foydalanuvchi"
    if not name:
        name = r["username"] if r and r["username"] else "Foydalanuvchi"
    y, m = map(int, cert["month_key"].split("-"))
    month_name = calendar.month_name[m]
    # Uzbek month names.
    uz_months = ["", "yanvar", "fevral", "mart", "aprel", "may", "iyun",
                 "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
    month_name = uz_months[m]
    await context.bot.send_photo(
        chat_id, photo=make_certificate(name, y, month_name, cert["count"]),
        caption=f"🏅 Tabriklaymiz, {name}!\n\nSiz {y}-yil {month_name} oyida {cert['count']} ta hadisni o‘rgandingiz. "
                f"Oyiga {CERT_MIN_HADITHS} ta hadis mezoni bajarildi.")

async def start(update, context):
    u = update.effective_user
    ref_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref_id = int(context.args[0].split("_", 1)[1])
        except ValueError:
            pass
    await ensure_user_data(u, ref_id)
    if WEBAPP_URL:
        try:
            await context.bot.set_chat_menu_button(
                chat_id=u.id,
                menu_button=MenuButtonWebApp(text="📱 Mini App", web_app=WebAppInfo(WEBAPP_URL)))
        except Exception:
            pass
    await update.message.reply_text(
        "Assalomu alaykum! 🌙\n\n<b>ENG ZARUR ILM</b>\n"
        ""Har kuni hadis, ma’no va foydali odat.\n\n"
"Hadis ilmini o‘rganishda niyatingiz to‘g‘ri bo‘lsin. "
"Tahoratli holda o‘qisangiz, nur ustiga nurdir.\n\n"
"O‘zingiz uchun rejimni tanlang:""
        "O‘zingiz uchun rejimni tanlang:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👧 Bolalar uchun", callback_data="aud:children"),
            InlineKeyboardButton("👨 Kattalar uchun", callback_data="aud:adults")]]))
    await update.message.reply_text(
        "Menyudan kerakli bo‘limni tanlang.", reply_markup=reply_keyboard())
    if WEBAPP_URL:
        await update.message.reply_text(
            "✨ Premium Mini App:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📱 Ochish", web_app=WebAppInfo(WEBAPP_URL))]]))

async def callback_button(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ""
    if data.startswith("aud:"):
        audience = data.split(":", 1)[1]
        if audience not in {"children", "adults"}:
            return
        async with pool.acquire() as c:
            await c.execute("UPDATE users SET audience=$2,last_seen=now() WHERE id=$1", uid, audience)
        await q.edit_message_text("✅ Rejim tanlandi.\n\n📖 Bugungi hadis tayyor.")
        await send_hadith(uid, context, uid)
        return
    if data.startswith("learn:"):
        hid = int(data.split(":", 1)[1])
        ok, cert = await mark_learned(uid, hid)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text("✅ Bilib oldim — saqlandi! 🔥" if ok else "ℹ️ Bu hadis avval saqlangan.")
        if cert:
            await issue_certificate(uid, context, uid, cert)
        return
    if data.startswith("share:"):
        hid = int(data.split(":", 1)[1])
        h = HMAP.get(hid)
        if not h:
            return
        async with pool.acquire() as c:
            await c.execute("INSERT INTO app_events(user_id,event) VALUES($1,'share_click')", uid)
        me = await context.bot.get_me()
        bot_link = f"https://t.me/{me.username}?start=ref_{uid}"
        text = urllib.parse.quote(card_text(h)[:1200])
        url = urllib.parse.quote(bot_link, safe="")
        await q.message.reply_text(
            f"📤 Ulashish uchun tayyor:\nhttps://t.me/share/url?url={url}&text={text}")

async def text_handler(update, context):
    u = update.effective_user
    txt = (update.message.text or "").strip()
    await ensure_user_data(u)
    if txt == "📖 Bugungi hadis":
        await send_hadith(update.effective_chat.id, context, u.id)
    elif txt == "📊 Statistikam":
        await stats_message(update)
    elif txt == "🏆 Reyting":
        await rating_message(update)
    elif txt == "🔥 Streakim":
        async with pool.acquire() as c:
            r = await c.fetchrow("SELECT streak,points FROM users WHERE id=$1", u.id)
        await update.message.reply_text(f"🔥 <b>Streak:</b> {r['streak']} kun\n⭐ <b>Ochko:</b> {r['points']}", parse_mode="HTML")
    elif txt == "🎯 Sozlamalar":
        await update.message.reply_text(
            "🎯 Sozlamalar",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📱 Sozlamalarni ochish", web_app=WebAppInfo(WEBAPP_URL))
            ]]) if WEBAPP_URL else None)
    elif txt == "📤 Do‘stlarga ulashish":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{u.id}"
        async with pool.acquire() as c:
            await c.execute("INSERT INTO app_events(user_id,event) VALUES($1,'share_click')", u.id)
        await update.message.reply_text(f"📤 Do‘stingizga yuboring:\n{link}")
    elif txt == "ℹ️ Yordam":
        await update.message.reply_text(
            f"ℹ️ Yordam\n\n📖 Har kuni soat 06:00 da premium hadis kartasi yuboriladi.\n"
            f"✅ Bilib oldim — progressni saqlaydi.\n"
            f"🏅 Oyiga kamida {CERT_MIN_HADITHS} ta hadis — sertifikat.\n"
            "📊 Statistika va reyting — PostgreSQLda saqlanadi.\n"
            "📱 Mini App — to‘liq boshqaruv paneli.")

async def stats_message(update):
    uid = update.effective_user.id
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT count(*) FROM learned WHERE user_id=$1", uid)
        month = await c.fetchval(
            "SELECT count(*) FROM learned WHERE user_id=$1 AND date_trunc('month',learned_on)=date_trunc('month',$2::date)",
            uid, now().date())
        r = await c.fetchrow("SELECT streak,points FROM users WHERE id=$1", uid)
    await update.message.reply_text(
        f"📊 <b>Statistikam</b>\n\n📖 Jami: {total}\n📅 Shu oy: {month}/{CERT_MIN_HADITHS}\n"
        f"🔥 Streak: {r['streak']} kun\n⭐ Ochko: {r['points']}", parse_mode="HTML")

async def rating_message(update):
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT first_name,username,points,streak FROM users ORDER BY points DESC,streak DESC,created_at ASC LIMIT 10")
    lines = ["🏆 <b>REYTING</b>", ""]
    for i, r in enumerate(rows, 1):
        name = r["first_name"] or r["username"] or "Foydalanuvchi"
        lines.append(f"{i}. {name} — ⭐ {r['points']} | 🔥 {r['streak']}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def admin_command(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Ruxsat yo‘q.")
        return
    async with pool.acquire() as c:
        users = await c.fetchval("SELECT count(*) FROM users")
        active = await c.fetchval("SELECT count(*) FROM users WHERE last_seen>now()-interval '7 days'")
        learned = await c.fetchval("SELECT count(*) FROM learned")
        shares = await c.fetchval("SELECT count(*) FROM app_events WHERE event='share_click'")
        certs = await c.fetchval("SELECT count(*) FROM certificates")
        today = await c.fetchval("SELECT count(*) FROM learned WHERE learned_on=$1", now().date())
    await update.message.reply_text(
        f"🛠 <b>ADMIN PANEL</b>\n\n👥 Foydalanuvchilar: {users}\n"
        f"🟢 7 kun faol: {active}\n📖 O‘rganilgan hadislar: {learned}\n"
        f"📤 Ulashishlar: {shares}\n🏅 Sertifikatlar: {certs}\n☀️ Bugun: {today}\n\n"
        "📢 E’lon: /broadcast matn", parse_mode="HTML")

async def broadcast(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = update.message.text.partition(" ")[2].strip()
    if not msg:
        await update.message.reply_text("/broadcast E’lon matni")
        return
    async with pool.acquire() as c:
        ids = await c.fetch("SELECT id FROM users")
    ok = bad = 0
    for row in ids:
        try:
            await context.bot.send_message(row["id"], msg)
            ok += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.035)
    async with pool.acquire() as c:
        await c.execute(
            "INSERT INTO app_events(user_id,event) VALUES($1,'broadcast_sent')",
            update.effective_user.id)
    await update.message.reply_text(f"📢 Yuborildi: {ok}\n❌ Yetkazilmadi: {bad}")

@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE, "web", "index.html"))

@app.get("/health")
async def health():
    return {"ok": True, "service": "eng_zarur_ilm_bot", "hadiths": MAX_HADITH}

@app.get("/api/me")
async def api_me(request):
    u = user_from_request(request)
    uid = int(u["id"])
    class TG:
        id=uid
        username=u.get("username")
        first_name=u.get("first_name")
        last_name=u.get("last_name")
    await ensure_user_data(TG())
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT id,username,first_name,last_name,audience,streak,points,last_learned,notifications,ref_code,last_daily_sent FROM users WHERE id=$1", uid)
        total = await c.fetchval("SELECT count(*) FROM learned WHERE user_id=$1", uid)
        this_month = await c.fetchval(
            "SELECT count(*) FROM learned WHERE user_id=$1 AND date_trunc('month',learned_on)=date_trunc('month',$2::date)", uid, now().date())
    return {"user": dict(r), "total": int(total), "this_month": int(this_month), "certificate_target": CERT_MIN_HADITHS}

@app.get("/api/today")
async def api_today(request):
    u = user_from_request(request)
    h = await reserve_today_hadith(int(u["id"]))
    return h or {}

@app.post("/api/learn/{hid}")
async def api_learn(hid, request):
    u = user_from_request(request)
    ok, cert = await mark_learned(int(u["id"]), int(hid))
    if cert:
        asyncio.create_task(issue_certificate(int(u["id"]), type("Ctx", (), {"bot": bot_app.bot})(), int(u["id"]), cert))
    return {"saved": ok, "certificate": bool(cert)}

@app.post("/api/share/{hid}")
async def api_share(hid, request):
    u = user_from_request(request)
    if int(hid) not in HMAP:
        raise HTTPException(404, "Hadis topilmadi")
    async with pool.acquire() as c:
        await c.execute("INSERT INTO app_events(user_id,event) VALUES($1,'share_click')", int(u["id"]))
    return {"ok": True}

@app.get("/api/stats")
async def api_stats(request):
    u = user_from_request(request)
    uid = int(u["id"])
    today = now().date()
    week_start = today - timedelta(days=6)
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT learned_on,count(*) AS n FROM learned WHERE user_id=$1 AND learned_on BETWEEN $2 AND $3 GROUP BY learned_on ORDER BY learned_on",
            uid, week_start, today)
        total = await c.fetchval("SELECT count(*) FROM learned WHERE user_id=$1", uid)
        r = await c.fetchrow("SELECT streak,points FROM users WHERE id=$1", uid)
        month = await c.fetchval(
            "SELECT count(*) FROM learned WHERE user_id=$1 AND date_trunc('month',learned_on)=date_trunc('month',$2::date)", uid, today)
    counts = {str(x["learned_on"]): int(x["n"]) for x in rows}
    days = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        days.append({"date": str(d), "count": counts.get(str(d), 0)})
    return {"total": int(total), "month": int(month), "target": CERT_MIN_HADITHS,
            "streak": int(r["streak"] or 0), "points": int(r["points"] or 0), "days": days}

@app.get("/api/rating")
async def api_rating(request):
    user_from_request(request)
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT first_name,username,points,streak FROM users ORDER BY points DESC,streak DESC,created_at ASC LIMIT 50")
    return [dict(r) for r in rows]

@app.get("/api/calendar")
async def api_calendar(request):
    u = user_from_request(request)
    ym = request.query_params.get("month") or now().strftime("%Y-%m")
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT learned_on,hadith_id FROM learned WHERE user_id=$1 AND to_char(learned_on,'YYYY-MM')=$2 ORDER BY learned_on",
            int(u["id"]), ym)
    return [dict(r) for r in rows]

@app.get("/api/search")
async def api_search(request):
    user_from_request(request)
    q = (request.query_params.get("q") or "").strip().lower()
    if len(q) < 2:
        return []
    results = []
    for h in HADITHS:
        hay = " ".join([h.get("text", ""), h.get("meaning", ""), h.get("source", "")]).lower()
        if q in hay:
            results.append(h)
        if len(results) >= 20:
            break
    return results

@app.get("/api/admin")
async def api_admin(request):
    u = user_from_request(request)
    if int(u["id"]) not in ADMIN_IDS:
        raise HTTPException(403, "Ruxsat yo‘q")
    async with pool.acquire() as c:
        users = await c.fetchval("SELECT count(*) FROM users")
        active7 = await c.fetchval("SELECT count(*) FROM users WHERE last_seen>now()-interval '7 days'")
        active30 = await c.fetchval("SELECT count(*) FROM users WHERE last_seen>now()-interval '30 days'")
        learned = await c.fetchval("SELECT count(*) FROM learned")
        shares = await c.fetchval("SELECT count(*) FROM app_events WHERE event='share_click'")
        broadcasts = await c.fetchval("SELECT count(*) FROM app_events WHERE event='broadcast_sent'")
        certs = await c.fetchval("SELECT count(*) FROM certificates")
        today = await c.fetchval("SELECT count(*) FROM learned WHERE learned_on=$1", now().date())
        month = await c.fetchval(
            "SELECT count(*) FROM learned WHERE date_trunc('month',learned_on)=date_trunc('month',$1::date)", now().date())
    return {"users": int(users), "active7": int(active7), "active30": int(active30),
            "learned": int(learned), "shares": int(shares), "broadcasts": int(broadcasts),
            "certificates": int(certs), "today": int(today), "month": int(month),
            "certificate_target": CERT_MIN_HADITHS}

@app.post("/api/admin/broadcast")
async def api_admin_broadcast(request):
    u = user_from_request(request)
    if int(u["id"]) not in ADMIN_IDS:
        raise HTTPException(403, "Ruxsat yo‘q")
    body = await request.json()
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "E’lon matni bo‘sh.")
    async with pool.acquire() as c:
        ids = await c.fetch("SELECT id FROM users")
    ok = bad = 0
    for row in ids:
        try:
            await bot_app.bot.send_message(row["id"], message)
            ok += 1
        except Exception:
            bad += 1
    async with pool.acquire() as c:
        await c.execute("INSERT INTO app_events(user_id,event) VALUES($1,'broadcast_sent')", int(u["id"]))
    return {"sent": ok, "failed": bad}

@app.get("/api/admin/certificates")
async def api_admin_certificates(request):
    u = user_from_request(request)
    if int(u["id"]) not in ADMIN_IDS:
        raise HTTPException(403, "Ruxsat yo‘q")
    async with pool.acquire() as c:
        rows = await c.fetch("""
            SELECT c.month_key,c.count,c.issued_at,u.first_name,u.last_name,u.username
            FROM certificates c JOIN users u ON u.id=c.user_id
            ORDER BY c.issued_at DESC LIMIT 100
        """)
    return [dict(r) for r in rows]

@app.get("/api/certificate")
async def api_certificate(request):
    u = user_from_request(request)
    uid = int(u["id"])
    async with pool.acquire() as c:
        r = await c.fetchrow("""
            SELECT c.month_key,c.count,u.first_name,u.last_name,u.username
            FROM certificates c JOIN users u ON u.id=c.user_id
            WHERE c.user_id=$1 ORDER BY c.issued_at DESC LIMIT 1
        """, uid)
    if not r:
        raise HTTPException(404, "Sertifikat hali berilmagan.")
    y,m = map(int, r["month_key"].split("-"))
    name = " ".join([x for x in [r["first_name"],r["last_name"]] if x]) or r["username"] or "Foydalanuvchi"
    uz_months = ["", "yanvar","fevral","mart","aprel","may","iyun","iyul","avgust","sentabr","oktabr","noyabr","dekabr"]
    return StreamingResponse(make_certificate(name,y,uz_months[m],int(r["count"])),
                             media_type="image/png",
                             headers={"Content-Disposition":"inline; filename=sertifikat.png"})

async def daily_job(ctx):
    today = now().date()
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT id FROM users WHERE notifications=true AND (last_daily_sent IS NULL OR last_daily_sent<>$1)",
            today)
    for row in rows:
        try:
            await send_hadith(row["id"], ctx, row["id"])
            async with pool.acquire() as c:
                await c.execute("UPDATE users SET last_daily_sent=$2,last_seen=now() WHERE id=$1",
                                row["id"], today)
        except Exception as e:
            print("[DAILY ERROR]", row["id"], repr(e), flush=True)
        await asyncio.sleep(0.04)

async def main():
    global bot_app
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN yo‘q.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL yo‘q.")

    await db_init()
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin", admin_command))
    bot_app.add_handler(CommandHandler("broadcast", broadcast))
    bot_app.add_handler(CallbackQueryHandler(callback_button))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    bot_app.job_queue.run_daily(daily_job, time=time(6, 0, tzinfo=TZ))

    await bot_app.initialize()
    await bot_app.start()
    me = await bot_app.bot.get_me()
    print(f"[OK] Telegram bot: @{me.username}", flush=True)
    await bot_app.updater.start_polling(drop_pending_updates=True)

    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=int(os.getenv("PORT", "10000")), log_level="info"))
    try:
        await server.serve()
    finally:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        if pool:
            await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
