import os, io, json, sqlite3, threading, logging, asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parent
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
TIMEZONE = ZoneInfo(os.getenv('TIMEZONE', 'Asia/Tashkent'))
BOT_USERNAME = os.getenv('BOT_USERNAME', 'Eng_zarur_ilm_bot').lstrip('@')
DB_PATH = os.getenv('DB_PATH', str(BASE / 'bot.db'))
TEMPLATE = BASE / 'premium_template.png'
FONT_SERIF = BASE / 'DejaVuSerif.ttf'
FONT_SERIF_BOLD = BASE / 'DejaVuSerif-Bold.ttf'
FONT_ARABIC = BASE / 'NotoNaskhArabic-Bold.ttf'

GREEN=(8,57,46); GOLD=(224,183,84); TEXT=(18,55,48); SOFT=(239,242,222)

with open(BASE / 'hadiths.json', 'r', encoding='utf-8') as f:
    HADITHS = sorted(json.load(f), key=lambda x:int(x['id']))
HADITH_BY_ID = {int(x['id']):x for x in HADITHS}

CARD_CACHE = {}
CARD_LOCK = threading.Lock()

def F(path, size):
    return ImageFont.truetype(str(path), size)

def now(): return datetime.now(TIMEZONE)
def today(): return now().date().isoformat()

def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('''CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY, audience TEXT, current_hadith INTEGER DEFAULT 0,
        joined_date TEXT, learned_today INTEGER DEFAULT 0, last_seen TEXT
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS learned_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        hadith_id INTEGER NOT NULL, learned_date TEXT NOT NULL,
        UNIQUE(user_id, hadith_id, learned_date)
    )''')
    # Safe migration for databases created by older bot versions.
    cols={r[1] for r in con.execute('PRAGMA table_info(users)').fetchall()}
    if 'last_seen' not in cols:
        con.execute('ALTER TABLE users ADD COLUMN last_seen TEXT')
    con.commit(); return con

def ensure_user(uid):
    con=db(); row=con.execute('SELECT user_id FROM users WHERE user_id=?',(uid,)).fetchone()
    if row is None:
        con.execute('INSERT INTO users(user_id,joined_date,last_seen) VALUES(?,?,?)',(uid,today(),now().isoformat()))
    else:
        con.execute('UPDATE users SET last_seen=? WHERE user_id=?',(now().isoformat(),uid))
    con.commit(); con.close()

def get_user(uid):
    con=db(); row=con.execute('SELECT user_id,audience,current_hadith,joined_date,learned_today FROM users WHERE user_id=?',(uid,)).fetchone(); con.close(); return row

def set_audience(uid,aud):
    con=db(); con.execute('UPDATE users SET audience=?, current_hadith=0, joined_date=COALESCE(joined_date,?), learned_today=0 WHERE user_id=?',(aud,today(),uid)); con.commit(); con.close()

def set_current(uid,hid):
    con=db(); con.execute('UPDATE users SET current_hadith=?, learned_today=0, last_seen=? WHERE user_id=?',(hid,now().isoformat(),uid)); con.commit(); con.close()

def mark_learned(uid,hid):
    con=db(); d=today(); con.execute('INSERT OR IGNORE INTO learned_events(user_id,hadith_id,learned_date) VALUES(?,?,?)',(uid,hid,d)); con.execute('UPDATE users SET learned_today=1,last_seen=? WHERE user_id=?',(now().isoformat(),uid)); con.commit(); con.close()

def learned_count(uid):
    con=db(); n=con.execute('SELECT COUNT(DISTINCT hadith_id) FROM learned_events WHERE user_id=?',(uid,)).fetchone()[0]; con.close(); return n

def daily_count(uid):
    con=db(); n=con.execute('SELECT COUNT(*) FROM learned_events WHERE user_id=? AND learned_date=?',(uid,today())).fetchone()[0]; con.close(); return n

def streak(uid):
    con=db(); rows=con.execute('SELECT DISTINCT learned_date FROM learned_events WHERE user_id=? ORDER BY learned_date DESC',(uid,)).fetchall(); con.close()
    dates={r[0] for r in rows}; cur=now().date(); s=0
    while cur.isoformat() in dates:
        s+=1; cur-=timedelta(days=1)
    return s

def wrap_lines(draw,text,font,max_width):
    lines=[]; cur=''
    for word in str(text).split():
        test=word if not cur else cur+' '+word
        if draw.textbbox((0,0),test,font=font)[2] <= max_width: cur=test
        else:
            if cur: lines.append(cur)
            cur=word
    if cur: lines.append(cur)
    return lines

def centered_text(draw,text,box,font,fill=TEXT,spacing=7,max_lines=4):
    x1,y1,x2,y2=box; lines=wrap_lines(draw,text,font,x2-x1)
    if len(lines)>max_lines:
        lines=lines[:max_lines]; last=lines[-1]
        while last and draw.textbbox((0,0),last+'…',font=font)[2]>x2-x1: last=last[:-1]
        lines[-1]=last+'…'
    hs=[draw.textbbox((0,0),l,font=font)[3] for l in lines]; total=sum(hs)+spacing*(len(lines)-1)
    y=y1+max(0,((y2-y1)-total)//2)
    for l,h in zip(lines,hs):
        bb=draw.textbbox((0,0),l,font=font); x=x1+((x2-x1)-(bb[2]-bb[0]))//2; draw.text((x,y),l,font=font,fill=fill); y+=h+spacing

def make_card(h):
    hid=int(h['id'])
    with CARD_LOCK:
        if hid in CARD_CACHE:
            return io.BytesIO(CARD_CACHE[hid])
    img=Image.open(TEMPLATE).convert('RGB'); draw=ImageDraw.Draw(img)
    draw.rounded_rectangle((265,210,650,285),radius=32,fill=GREEN,outline=GOLD,width=5)
    draw.text((457,248),'BUGUNGI HADIS',font=F(FONT_SERIF_BOLD,37),fill=(250,232,169),anchor='mm')
    draw.text((540,430),h['arabic'].strip(),font=F(FONT_ARABIC,58),fill=TEXT,anchor='mm',direction='rtl')
    draw.line((380,585,750,585),fill=GOLD,width=2); draw.ellipse((560,580,570,590),fill=GOLD)
    centered_text(draw,'“'+h['text'].strip()+'”',(170,615,930,760),F(FONT_SERIF_BOLD,40),spacing=5,max_lines=3)
    draw.rounded_rectangle((385,785,720,845),radius=30,fill=(250,248,239),outline=GOLD,width=2)
    centered_text(draw,f"Hadis №{h['id']} • {h['source_short']}",(400,795,705,835),F(FONT_SERIF_BOLD,20),spacing=2,max_lines=2)
    draw.rounded_rectangle((170,885,985,1115),radius=34,fill=SOFT)
    draw.text((240,920),'HADIS MA’NOSI',font=F(FONT_SERIF_BOLD,29),fill=TEXT)
    centered_text(draw,h['meaning'].strip(),(225,965,945,1095),F(FONT_SERIF,23),spacing=5,max_lines=5)
    out=io.BytesIO(); img.save(out,format='PNG',optimize=True); data=out.getvalue()
    with CARD_LOCK: CARD_CACHE[hid]=data
    return io.BytesIO(data)

def main_menu():
    return ReplyKeyboardMarkup([
        ['📖 Bugungi hadis','📊 Statistikam'],
        ['🏆 Reyting','🎯 Sozlamalar'],
        ['📤 Do‘stlarga ulashish','ℹ️ Yordam']
    ],resize_keyboard=True,is_persistent=True)

def share_url(): return f'https://t.me/share/url?url=https://t.me/{BOT_USERNAME}&text={quote("Eng Zarur Ilm — foydali hadislarni o‘rganish boti")}'

def card_markup(h,learned=False):
    label='✓ Bilib oldim — saqlandi' if learned else '✓ Bilib oldim'
    return InlineKeyboardMarkup([[InlineKeyboardButton(label,callback_data=f"learn:{h['id']}")],[InlineKeyboardButton('↗ Ulashish',url=share_url())]])

async def send_hadith(bot,uid,hid=None):
    row=get_user(uid)
    if not row or not row[1]:
        await bot.send_message(uid,'Avval /start orqali kim uchun hadis kerakligini tanlang.',reply_markup=main_menu()); return False
    current=int(row[2] or 0)
    if hid is None: hid=current or 1
    if hid not in HADITH_BY_ID:
        await bot.send_message(uid,'Hozircha tasdiqlangan hadislar bazasida bundan keyingi hadis mavjud emas. Yangi hadislar faqat manba tekshirilgach qo‘shiladi.',reply_markup=main_menu()); return False
    set_current(uid,hid); h=HADITH_BY_ID[hid]
    photo = await asyncio.to_thread(make_card,h)
    await bot.send_photo(uid,photo=photo,caption=f'📖 Hadis №{h["id"]}',reply_markup=card_markup(h, bool(get_user(uid)[4])))
    return True

async def send_next(bot,uid):
    row=get_user(uid)
    if not row or not row[1]: return await bot.send_message(uid,'Avval /start orqali tanlov qiling.',reply_markup=main_menu())
    nxt=int(row[2] or 0)+1
    if nxt>len(HADITHS):
        return await bot.send_message(uid,'📚 Hozircha barcha tasdiqlangan hadislar yuborib bo‘lindi. Keyingi hadislar manba tekshirilib qo‘shiladi.',reply_markup=main_menu())
    return await send_hadith(bot,uid,nxt)

async def start(update,context):
    uid=update.effective_user.id; ensure_user(uid)
    await update.message.reply_text('Assalomu alaykum!\n\nKim uchun hadislar kerakligini tanlang:',reply_markup=main_menu())
    await update.message.reply_text('👇 Tanlang:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👦 Bolalar uchun',callback_data='aud:children')],[InlineKeyboardButton('👤 Kattalar uchun',callback_data='aud:adults')]]))

async def audience(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; ensure_user(uid); set_audience(uid,q.data.split(':')[1])
    await q.edit_message_text('✓ Tanlov saqlandi. Bugungi hadis tayyorlanmoqda…')
    await send_hadith(context.bot,uid,1)

async def learned(update,context):
    q=update.callback_query; uid=q.from_user.id; hid=int(q.data.split(':')[1]); await q.answer('Bilib oldim ✓'); await asyncio.to_thread(mark_learned,uid,hid)
    h=HADITH_BY_ID.get(hid); 
    if h: await q.edit_message_reply_markup(reply_markup=card_markup(h,True))

async def noop(update,context): await update.callback_query.answer('Bu amal bajarilgan.')

async def today_cmd(update,context):
    uid=update.effective_user.id; ensure_user(uid); row=get_user(uid)
    if not row[1]: return await update.message.reply_text('Avval /start orqali tanlov qiling.',reply_markup=main_menu())
    await send_hadith(context.bot,uid,int(row[2] or 1))

async def next_cmd(update,context): await send_next(context.bot,update.effective_user.id)

async def stats(update,context):
    uid=update.effective_user.id; await asyncio.to_thread(ensure_user,uid)
    vals=await asyncio.gather(asyncio.to_thread(learned_count,uid),asyncio.to_thread(streak,uid),asyncio.to_thread(daily_count,uid))
    await update.message.reply_text(f'📊 Sizning statistikangiz\n\n📚 O‘rganilgan hadislar: {vals[0]}\n🔥 Ketma-ket kunlar: {vals[1]}\n✅ Bugun o‘rganilgan: {vals[2]}',reply_markup=main_menu())

async def rating(update,context):
    con=db(); rows=con.execute('SELECT u.user_id,COUNT(DISTINCT e.hadith_id) c FROM users u LEFT JOIN learned_events e ON u.user_id=e.user_id GROUP BY u.user_id ORDER BY c DESC,u.user_id LIMIT 10').fetchall(); con.close()
    text='🏆 Reyting — eng ko‘p o‘rganilgan hadislar\n\n'
    if not rows: text+='Hozircha ma’lumot yo‘q.'
    else:
        for i,(uid,c) in enumerate(rows,1): text+=f'{i}. 🟢 {c} ta hadis\n'
    await update.message.reply_text(text,reply_markup=main_menu())

async def settings(update,context):
    await update.message.reply_text('🎯 Sozlamalar\n\nAuditoriyani almashtirish:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👦 Bolalar uchun',callback_data='aud:children')],[InlineKeyboardButton('👤 Kattalar uchun',callback_data='aud:adults')]]))

async def share(update,context):
    await update.message.reply_text('📤 Do‘stlaringizga yuboring:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📤 Ulashish',url=share_url())]]))

async def help_cmd(update,context):
    await update.message.reply_text('ℹ️ Yordam\n\n📖 Bugungi hadis — hozirgi hadisni ko‘rsatadi.\n📊 Statistikam — shaxsiy natijalar.\n🏆 Reyting — o‘rganish faolligi.\n🎯 Sozlamalar — auditoriyani almashtirish.\n📤 Do‘stlarga ulashish — botni ulashish.\n\nBirinchi hadis tanlovdan keyin darhol yuboriladi; keyingi kunlarda kunlik yuborish avtomatik ishlaydi.',reply_markup=main_menu())

async def text_menu(update,context):
    t=(update.message.text or '').strip()
    if t=='📖 Bugungi hadis': return await today_cmd(update,context)
    if t=='📊 Statistikam': return await stats(update,context)
    if t=='🏆 Reyting': return await rating(update,context)
    if t=='🎯 Sozlamalar': return await settings(update,context)
    if t=='📤 Do‘stlarga ulashish': return await share(update,context)
    if t=='ℹ️ Yordam': return await help_cmd(update,context)

async def daily_job(context):
    con=db(); rows=con.execute('SELECT user_id,joined_date,audience,current_hadith FROM users WHERE audience IS NOT NULL').fetchall(); con.close(); d=today()
    for uid,joined,aud,current in rows:
        if joined==d: continue
        try:
            nxt=int(current or 0)+1
            if nxt<=len(HADITHS): await send_hadith(context.bot,uid,nxt)
        except Exception:
            log.exception('Daily send failed for %s',uid)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/plain; charset=utf-8'); self.end_headers(); self.wfile.write(b'Eng_zarur_ilm_bot OK')
    def log_message(self,*args): pass

def start_health_server():
    port=int(os.getenv('PORT','10000')); HTTPServer(('0.0.0.0',port),HealthHandler).serve_forever()

def error_handler(update,context): log.exception('Telegram handler error',exc_info=context.error)

def main():
    if not TOKEN: raise RuntimeError('TELEGRAM_BOT_TOKEN topilmadi.')
    threading.Thread(target=start_health_server,daemon=True).start()
    app=Application.builder().token(TOKEN).connect_timeout(10).read_timeout(30).write_timeout(30).pool_timeout(10).concurrent_updates(True).build()
    app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('help',help_cmd)); app.add_handler(CommandHandler('today',today_cmd)); app.add_handler(CommandHandler('next',next_cmd)); app.add_handler(CommandHandler('stats',stats)); app.add_handler(CommandHandler('rating',rating)); app.add_handler(CommandHandler('settings',settings))
    app.add_handler(CallbackQueryHandler(audience,pattern=r'^aud:')); app.add_handler(CallbackQueryHandler(learned,pattern=r'^learn:')); app.add_handler(CallbackQueryHandler(noop,pattern=r'^noop$'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_menu))
    app.add_error_handler(error_handler)
    app.job_queue.run_daily(daily_job,time=time(6,0,tzinfo=TIMEZONE),name='daily_hadith')
    app.run_polling(drop_pending_updates=True,allowed_updates=Update.ALL_TYPES)

if __name__=='__main__': main()
