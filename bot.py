import os, json, asyncio, hashlib, hmac, urllib.parse, secrets
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
import uvicorn

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
TZ = ZoneInfo(TIMEZONE)
BASE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(BASE, "hadiths.json"), encoding="utf-8") as f:
    HADITHS = sorted(json.load(f), key=lambda x: x.get("id", 0))
HMAP = {int(h["id"]): h for h in HADITHS}
MAX_HADITH = max(HMAP) if HMAP else 0

app = FastAPI(title="Eng zarur ilm — Telegram Mini App")
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


def verify_init_data(init_data: str):
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


def user_from_request(request: Request):
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
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                audience TEXT NOT NULL DEFAULT 'both',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
                streak INTEGER NOT NULL DEFAULT 0,
                points INTEGER NOT NULL DEFAULT 0,
                last_learned DATE,
                notifications BOOLEAN NOT NULL DEFAULT TRUE,
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
        await c.execute("CREATE INDEX IF NOT EXISTS learned_day_idx ON learned(learned_on)")
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
        # Safe migrations for already-created databases.
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT REFERENCES users(id) ON DELETE SET NULL")


async def ensure_user_data(tg_user, referred_by=None):
    async with pool.acquire() as c:
        row = await c.fetchrow("SELECT * FROM users WHERE id=$1", tg_user.id)
        if row:
            await c.execute(
                "UPDATE users SET username=$2, first_name=$3, last_name=$4, last_seen=now() WHERE id=$1",
                tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name,
            )
            return row
        ref = secrets.token_urlsafe(8).replace("-", "_").replace("=", "")
        await c.execute(
            """INSERT INTO users(id,username,first_name,last_name,ref_code,referred_by)
               VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING""",
            tg_user.id, tg_user.username, tg_user.first_name, tg_user.last_name, ref, referred_by,
        )
        if referred_by and referred_by != tg_user.id:
            await c.execute(
                "INSERT INTO referrals(referrer_id,invited_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                referred_by, tg_user.id,
            )
            await c.execute("UPDATE users SET points=points+5 WHERE id=$1", referred_by)
        return await c.fetchrow("SELECT * FROM users WHERE id=$1", tg_user.id)


async def next_hadith(uid):
    async with pool.acquire() as c:
        row = await c.fetchrow(
            "SELECT hadith_id FROM learned WHERE user_id=$1 ORDER BY hadith_id DESC LIMIT 1", uid
        )
    last = int(row["hadith_id"]) if row else 0
    if last >= MAX_HADITH:
        return HMAP.get(1)
    return HMAP.get(last + 1) or HMAP.get(1)


async def current_hadith(uid):
    return await next_hadith(uid)


async def mark_learned(uid, hid):
    if hid not in HMAP:
        return False
    today = now().date()
    async with pool.acquire() as c:
        exists = await c.fetchrow(
            "SELECT 1 FROM learned WHERE user_id=$1 AND hadith_id=$2", uid, hid
        )
        if exists:
            return False
        await c.execute(
            "INSERT INTO learned(user_id,hadith_id,learned_on) VALUES($1,$2,$3)",
            uid, hid, today,
        )
        row = await c.fetchrow("SELECT streak,last_learned,points FROM users WHERE id=$1", uid)
        streak = int(row["streak"] or 0)
        last = row["last_learned"]
        if last == today - timedelta(days=1):
            streak += 1
        elif last != today:
            streak = 1
        await c.execute(
            "UPDATE users SET streak=$2,last_learned=$3,points=points+10,last_seen=now() WHERE id=$1",
            uid, streak, today,
        )
        await c.execute("INSERT INTO app_events(user_id,event) VALUES($1,'learned')", uid)
        return True


def card_text(h):
    meaning = h.get("meaning", "").strip()
    out = [f"📖 BUGUNGI HADIS\n\n{h['text'].strip()}"]
    if meaning:
        out.append(f"💡 Hadis ma’nosi\n{meaning}")
    out.append(f"📚 Manba: {h.get('source','')}")
    return "\n\n".join(out)


async def send_hadith(chat_id, context, uid):
    h = await current_hadith(uid)
    if not h:
        return
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Bilib oldim", callback_data=f"learn:{h['id']}"),
            InlineKeyboardButton("📱 Mini App", web_app=WebAppInfo(WEBAPP_URL)) if WEBAPP_URL else InlineKeyboardButton("📤 Ulashish", switch_inline_query=card_text(h)[:900]),
        ]
    ])
    await context.bot.send_message(chat_id, card_text(h), reply_markup=kb)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ref_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref_id = int(context.args[0].split("_", 1)[1])
        except ValueError:
            ref_id = None
    await ensure_user_data(u, ref_id)
    if WEBAPP_URL:
        try:
            await context.bot.set_chat_menu_button(
                chat_id=u.id,
                menu_button=MenuButtonWebApp(text="📱 Mini App", web_app=WebAppInfo(WEBAPP_URL)),
            )
        except Exception:
            pass
    await update.message.reply_text(
        "Assalomu alaykum! 🌙\n\n<b>Eng zarur ilm</b> — hadis, ma’no va foydali odatlarni bir joyda kuzatib boring.",
        parse_mode="HTML",
        reply_markup=reply_keyboard(),
    )
    if WEBAPP_URL:
        await update.message.reply_text(
            "⬇️ Mini App orqali barcha imkoniyatlarni oching:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Mini Appni ochish", web_app=WebAppInfo(WEBAPP_URL))]]),
        )


async def callback_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ""
    if data.startswith("learn:"):
        hid = int(data.split(":", 1)[1])
        ok = await mark_learned(uid, hid)
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await q.message.reply_text("✅ Bilib oldim — saqlandi!" if ok else "ℹ️ Bu hadis avval saqlangan.")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            "🎯 Sozlamalar\n\nSozlamalar va bildirishnomalarni Mini App ichidan boshqarishingiz mumkin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 Sozlamalarni ochish", web_app=WebAppInfo(WEBAPP_URL))]]) if WEBAPP_URL else None,
        )
    elif txt == "📤 Do‘stlarga ulashish":
        me = await context.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{u.id}"
        await update.message.reply_text(f"📤 Do‘stingizga yuboring:\n{link}\n\nDo‘stingiz qo‘shilganda sizga bonus ochko beriladi.")
    elif txt == "ℹ️ Yordam":
        await update.message.reply_text(
            "ℹ️ Yordam\n\n📖 Bugungi hadis — navbatdagi hadisni ko‘rsatadi.\n✅ Bilib oldim — progressni saqlaydi.\n📊 Statistika — shaxsiy natijalarni ko‘rsatadi.\n🏆 Reyting — ochkolar bo‘yicha reyting.\n📱 Mini App — to‘liq boshqaruv paneli."
        )


async def stats_message(update):
    uid = update.effective_user.id
    async with pool.acquire() as c:
        total = await c.fetchval("SELECT count(*) FROM learned WHERE user_id=$1", uid)
        r = await c.fetchrow("SELECT streak,points FROM users WHERE id=$1", uid)
    await update.message.reply_text(
        f"📊 <b>Statistikam</b>\n\n📖 O‘qilgan hadislar: {total}\n🔥 Streak: {r['streak']} kun\n⭐ Ochko: {r['points']}",
        parse_mode="HTML",
    )


async def rating_message(update):
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT first_name,username,points,streak FROM users ORDER BY points DESC,streak DESC,created_at ASC LIMIT 10"
        )
    lines = ["🏆 <b>REYTING</b>", ""]
    for i, r in enumerate(rows, 1):
        name = r["first_name"] or r["username"] or "Foydalanuvchi"
        lines.append(f"{i}. {name} — ⭐ {r['points']} | 🔥 {r['streak']}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Ruxsat yo‘q.")
        return
    async with pool.acquire() as c:
        users = await c.fetchval("SELECT count(*) FROM users")
        learned = await c.fetchval("SELECT count(*) FROM learned")
        active = await c.fetchval("SELECT count(*) FROM users WHERE last_seen > now()-interval '7 days'")
    await update.message.reply_text(
        f"🛠 <b>ADMIN PANEL</b>\n\n👥 Foydalanuvchilar: {users}\n📖 O‘qilganlar: {learned}\n🟢 7 kunlik faol: {active}\n\nE’lon: /broadcast matn",
        parse_mode="HTML",
    )


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await asyncio.sleep(0.03)
    await update.message.reply_text(f"📢 Yuborildi: {ok}\n❌ Yetkazilmadi: {bad}")


@app.get("/")
async def index():
    return FileResponse(os.path.join(BASE, "web", "index.html"))


@app.get("/health")
async def health():
    return {"ok": True, "service": "eng_zarur_ilm_bot"}


@app.get("/api/me")
async def api_me(request: Request):
    u = user_from_request(request)
    await ensure_user_data(type("TG", (), u)())
    async with pool.acquire() as c:
        r = await c.fetchrow(
            "SELECT id,username,first_name,last_name,audience,streak,points,last_learned,notifications,ref_code FROM users WHERE id=$1",
            int(u["id"]),
        )
        total = await c.fetchval("SELECT count(*) FROM learned WHERE user_id=$1", int(u["id"]))
        this_month = await c.fetchval(
            "SELECT count(*) FROM learned WHERE user_id=$1 AND date_trunc('month',learned_on)=date_trunc('month',$2::date)",
            int(u["id"]), now().date(),
        )
    return {"user": dict(r), "total": int(total), "this_month": int(this_month)}


@app.get("/api/today")
async def api_today(request: Request):
    u = user_from_request(request)
    return await current_hadith(int(u["id"]))


@app.post("/api/learn/{hid}")
async def api_learn(hid: int, request: Request):
    u = user_from_request(request)
    return {"saved": await mark_learned(int(u["id"]), hid)}


@app.get("/api/rating")
async def api_rating(request: Request):
    u = user_from_request(request)
    async with pool.acquire() as c:
        rows = await c.fetch(
            """SELECT first_name,username,points,streak FROM users
               ORDER BY points DESC,streak DESC,created_at ASC LIMIT 50"""
        )
    return [dict(r) for r in rows]


@app.get("/api/calendar")
async def api_calendar(request: Request):
    u = user_from_request(request)
    ym = request.query_params.get("month") or now().strftime("%Y-%m")
    async with pool.acquire() as c:
        rows = await c.fetch(
            "SELECT learned_on,hadith_id FROM learned WHERE user_id=$1 AND to_char(learned_on,'YYYY-MM')=$2 ORDER BY learned_on",
            int(u["id"]), ym,
        )
    return [dict(r) for r in rows]


@app.get("/api/search")
async def api_search(request: Request):
    user_from_request(request)
    q = (request.query_params.get("q") or "").strip().lower()
    if len(q) < 2:
        return []
    results = []
    for h in HADITHS:
        hay = " ".join([h.get("text", ""), h.get("meaning", ""), h.get("source", "")]).lower()
        if q in hay:
            results.append(h)
        if len(results) >= 12:
            break
    return results


@app.get("/api/admin")
async def api_admin(request: Request):
    u = user_from_request(request)
    if int(u["id"]) not in ADMIN_IDS:
        raise HTTPException(403, "Ruxsat yo‘q")
    async with pool.acquire() as c:
        users = await c.fetchval("SELECT count(*) FROM users")
        learned = await c.fetchval("SELECT count(*) FROM learned")
        active7 = await c.fetchval("SELECT count(*) FROM users WHERE last_seen>now()-interval '7 days'")
        today = await c.fetchval("SELECT count(*) FROM learned WHERE learned_on=$1", now().date())
    return {"users": int(users), "learned": int(learned), "active7": int(active7), "today": int(today)}


@app.post("/api/admin/broadcast")
async def api_admin_broadcast(request: Request):
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
    return {"sent": ok, "failed": bad}


@app.post("/api/settings")
async def api_settings(request: Request):
    u = user_from_request(request)
    body = await request.json()
    notifications = body.get("notifications")
    audience = body.get("audience")
    async with pool.acquire() as c:
        if isinstance(notifications, bool):
            await c.execute("UPDATE users SET notifications=$2 WHERE id=$1", int(u["id"]), notifications)
        if audience in {"both", "children", "adults"}:
            await c.execute("UPDATE users SET audience=$2 WHERE id=$1", int(u["id"]), audience)
    return {"ok": True}


async def daily_job(ctx: ContextTypes.DEFAULT_TYPE):
    async with pool.acquire() as c:
        rows = await c.fetch("SELECT id FROM users WHERE notifications=true")
    for row in rows:
        try:
            await send_hadith(row["id"], ctx, row["id"])
        except Exception:
            pass
        await asyncio.sleep(0.03)


async def run_bot():
    global bot_app
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
    await bot_app.updater.start_polling(drop_pending_updates=True)


async def main():
    # IMPORTANT: initialize the database and Telegram bot BEFORE starting
    # the web server. This prevents Render from showing the service as
    # healthy while the Telegram bot has already crashed in the background.
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN yo‘q. Render > Environment ga token kiriting.")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL yo‘q. Render PostgreSQL URL sini Environment ga ulang.")

    print("[START] Database ulanishi tekshirilmoqda...", flush=True)
    await db_init()
    print("[OK] PostgreSQL ulandi.", flush=True)

    global bot_app
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin", admin_command))
    bot_app.add_handler(CommandHandler("broadcast", broadcast))
    bot_app.add_handler(CallbackQueryHandler(callback_button))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    bot_app.job_queue.run_daily(daily_job, time=time(6, 0, tzinfo=TZ))

    print("[START] Telegram bot ishga tushmoqda...", flush=True)
    await bot_app.initialize()
    await bot_app.start()
    me = await bot_app.bot.get_me()
    print(f"[OK] Telegram bot: @{me.username}", flush=True)
    await bot_app.updater.start_polling(drop_pending_updates=True)
    print("[OK] Polling faol.", flush=True)

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=int(os.getenv("PORT", "10000")),
            log_level="info",
        )
    )
    try:
        await server.serve()
    finally:
        print("[STOP] Server yopilmoqda...", flush=True)
        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        finally:
            if pool:
                await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
