import os, sqlite3, threading, urllib.parse, calendar
from datetime import datetime, date, timedelta, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

load_dotenv()
BASE = Path(__file__).resolve().parent
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
TZ = ZoneInfo(os.getenv('TIMEZONE', 'Asia/Tashkent'))
DB = os.getenv('DB_PATH', str(BASE / 'eng_zarur_ilm.db'))
PORT = int(os.getenv('PORT', '10000'))
BOT_USERNAME = os.getenv('BOT_USERNAME', 'Eng_zarur_ilm_bot').lstrip('@')
SOURCE_URL = 'https://ahlussunnawaljamoa.wordpress.com/kitoblar/hadis-kitoblari/'

HADITHS = __import__('json').loads((BASE / 'hadiths.json').read_text(encoding='utf-8'))
HADITHS = sorted(HADITHS, key=lambda x: x['id'])

# -------------------- DATABASE --------------------
def conn():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = conn()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        username TEXT,
        audience TEXT,
        active INTEGER DEFAULT 1,
        joined_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS learned(
        uid INTEGER NOT NULL,
        day TEXT NOT NULL,
        hadith_id INTEGER NOT NULL,
        PRIMARY KEY(uid, day)
    );
    CREATE TABLE IF NOT EXISTS invite_events(
        inviter INTEGER NOT NULL,
        invited INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(inviter, invited)
    );
    CREATE INDEX IF NOT EXISTS idx_learned_uid_day ON learned(uid, day);
    CREATE INDEX IF NOT EXISTS idx_users_active ON users(active, audience);
    ''')
    c.commit(); c.close()

def now(): return datetime.now(TZ)
def today(): return now().date()
def day_s(d=None): return (d or today()).isoformat()

def register(user):
    c = conn()
    c.execute('''INSERT INTO users(id,name,username,active,joined_at) VALUES(?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username, active=1''',
              (user.id, user.full_name or 'Foydalanuvchi', user.username, 1, now().isoformat()))
    c.commit(); c.close()

def get_user(uid):
    c=conn(); r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close(); return r

def set_audience(uid, audience):
    c=conn(); c.execute('UPDATE users SET audience=?,active=1 WHERE id=?',(audience,uid)); c.commit(); c.close()

def learned_today(uid):
    c=conn(); r=c.execute('SELECT 1 FROM learned WHERE uid=? AND day=?',(uid,day_s())).fetchone(); c.close(); return bool(r)

def learned_count(uid):
    c=conn(); n=c.execute('SELECT COUNT(*) FROM learned WHERE uid=?',(uid,)).fetchone()[0]; c.close(); return n

def streak(uid):
    c=conn(); rows=c.execute('SELECT day FROM learned WHERE uid=? ORDER BY day DESC',(uid,)).fetchall(); c.close()
    days={date.fromisoformat(r['day']) for r in rows}
    d=today() if today() in days else today()-timedelta(days=1)
    n=0
    while d in days:
        n+=1; d-=timedelta(days=1)
    return n

def longest_streak(uid):
    c=conn(); rows=c.execute('SELECT DISTINCT day FROM learned WHERE uid=? ORDER BY day',(uid,)).fetchall(); c.close()
    days=sorted(date.fromisoformat(r['day']) for r in rows)
    best=cur=0; prev=None
    for d in days:
        if prev and d==prev+timedelta(days=1): cur+=1
        else: cur=1
        best=max(best,cur); prev=d
    return best

def ranking(uid=None):
    c=conn()
    rows=c.execute('''SELECT u.id,u.name,u.username,COUNT(l.day) n
                      FROM users u LEFT JOIN learned l ON l.uid=u.id
                      WHERE u.active=1 GROUP BY u.id ORDER BY n DESC,u.name LIMIT 100''').fetchall()
    c.close()
    pos=None
    for i,r in enumerate(rows,1):
        if uid and r['id']==uid: pos=i; break
    return rows,pos

# -------------------- UI --------------------
def main_menu():
    return ReplyKeyboardMarkup([
        ['📖 Bugungi hadis','📊 Statistikam'],
        ['🏆 Reyting','🔥 Streakim'],
        ['📅 Kalendar','👤 Profil'],
        ['📤 Do‘stlarga ulashish','🎯 Sozlamalar'],
        ['ℹ️ Yordam']
    ], resize_keyboard=True, is_persistent=True)

def audience_menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton('👦 Bolalar uchun',callback_data='aud:children'),
                                  InlineKeyboardButton('👨 Kattalar uchun',callback_data='aud:adults')]])

def hadith_buttons(hid):
    share='https://t.me/share/url?url=&text='+urllib.parse.quote('📖 Bugungi hadis\n\n'+next(x['text'] for x in HADITHS if x['id']==hid))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Bilib oldim',callback_data=f'learn:{hid}'), InlineKeyboardButton('📤 Ulashish',url=share)],
        [InlineKeyboardButton('📖 Yana ko‘rish',callback_data='today')]
    ])

# -------------------- IMAGE RENDERING --------------------
def font(size,bold=False):
    paths=['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
           '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf']
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def wrap(draw,text,f,maxw):
    lines=[]; cur=''
    for word in text.split():
        z=word if not cur else cur+' '+word
        if draw.textbbox((0,0),z,font=f)[2] <= maxw: cur=z
        else:
            if cur: lines.append(cur)
            cur=word
    if cur: lines.append(cur)
    return lines

def render_hadith_card(h):
    template=BASE/'premium_template.png'
    if template.exists():
        im=Image.open(template).convert('RGB')
    else:
        im=Image.new('RGB',(1536,1536),(35,20,10))
    d=ImageDraw.Draw(im)
    # Dynamic content area: the reference template reserves a cream central panel.
    # Text is programmatically rendered so the source text is never AI-generated.
    d.rounded_rectangle((70,300,930,1130),radius=30,fill=(246,239,218),outline=(196,151,70),width=3)
    d.rounded_rectangle((150,215,850,295),radius=30,fill=(205,158,67))
    d.text((365,235),'BUGUNGI HADIS',font=font(32,True),fill=(52,38,21))
    y=340
    d.text((110,y),f'HADIS № {h["id"] if h["id"] else "KIRISH"}',font=font(30,True),fill=(105,77,39)); y+=70
    for line in wrap(d,h['text'],font(42,True),760):
        d.text((110,y),line,font=font(42,True),fill=(39,34,28)); y+=58
    y+=30
    d.text((110,y),'MA’NOSI / SABOQ',font=font(28,True),fill=(105,77,39)); y+=52
    meaning=h.get('meaning') or h['text']
    for line in wrap(d,meaning,font(29),760)[:7]:
        d.text((110,y),line,font=font(29),fill=(58,52,45)); y+=42
    y=min(y+20,1040)
    d.text((110,y),'MANBA',font=font(24,True),fill=(105,77,39)); y+=40
    for line in wrap(d,h['source_full'],font(22),760)[:3]:
        d.text((110,y),line,font=font(22),fill=(75,67,57)); y+=31
    out=BASE/f'.card_{h["id"]}.png'; im.save(out,quality=95); return out

def render_dashboard(uid):
    u=get_user(uid); count=learned_count(uid); st=streak(uid); best=longest_streak(uid); rows,pos=ranking(uid)
    im=Image.new('RGB',(1080,1550),(27,18,12)); d=ImageDraw.Draw(im)
    gold=(229,174,76); cream=(246,238,214); muted=(169,145,110); panel=(40,27,18)
    d.text((65,55),'ENG ZARUR ILM',font=font(62,True),fill=gold)
    d.text((68,130),'SHAXSIY KABINET',font=font(28,True),fill=cream)
    d.text((68,180),(u['name'] if u else 'Foydalanuvchi'),font=font(34,True),fill=cream)
    cards=[('O‘rganilgan hadis',str(count)),('Joriy seriya',f'{st} kun'),('Eng uzun seriya',f'{best} kun'),('Reyting',f'{pos or "—"}-o‘rin')]
    y=250
    for title,val in cards:
        d.rounded_rectangle((55,y,1025,y+150),radius=24,fill=panel,outline=(72,52,31),width=2)
        d.text((85,y+28),val,font=font(48,True),fill=gold)
        d.text((85,y+95),title,font=font(25),fill=muted); y+=175
    d.text((65,980),'BUGUN',font=font(26,True),fill=gold)
    status='Bajarildi' if learned_today(uid) else 'Hali bajarilmagan'
    d.text((65,1030),f'Bugungi hadis: {status}',font=font(32,True),fill=cream)
    d.text((65,1140),'MANBA TARTIBI',font=font(26,True),fill=gold)
    d.text((65,1190),'1) 101-hadis.pdf',font=font(28),fill=cream)
    d.text((65,1235),'2) Keyingi bosqich — tasdiqlangan hadis kitoblari',font=font(24),fill=muted)
    out=BASE/f'.dashboard_{uid}.png'; im.save(out,quality=95); return out

def render_calendar(uid,year=None,month=None):
    dte=today(); year=year or dte.year; month=month or dte.month
    im=Image.new('RGB',(1080,1450),(27,18,12)); d=ImageDraw.Draw(im)
    gold=(229,174,76); cream=(246,238,214); muted=(169,145,110); panel=(40,27,18); done=(221,169,73); empty=(53,37,24)
    d.text((65,50),calendar.month_name[month]+' '+str(year),font=font(48,True),fill=cream)
    d.text((65,120),'📅 O‘RGANISH KALENDARI',font=font(28,True),fill=gold)
    names=['Du','Se','Ch','Pa','Ju','Sh','Ya']; x0=65; y0=210; cw=135; ch=105
    for i,n in enumerate(names): d.text((x0+i*cw+45,y0),n,font=font(24,True),fill=muted)
    c=conn(); rows=c.execute("SELECT day FROM learned WHERE uid=? AND day LIKE ?",(uid,f'{year:04d}-{month:02d}-%')).fetchall(); c.close(); done_days={int(r['day'][-2:]) for r in rows}
    weeks=calendar.monthcalendar(year,month)
    for wi,wk in enumerate(weeks):
        for di,day in enumerate(wk):
            if not day: continue
            x=x0+di*cw; y=y0+55+wi*ch
            fill=done if day in done_days else empty
            d.rounded_rectangle((x,y,x+118,y+78),radius=16,fill=fill)
            d.text((x+48,y+20),str(day),font=font(26,True),fill=(35,27,18) if day in done_days else muted)
    y=1120
    d.rounded_rectangle((65,y,1015,y+170),radius=24,fill=panel)
    n=len(done_days)
    d.text((90,y+30),f'Bu oyda o‘rganilgan kunlar: {n}',font=font(31,True),fill=cream)
    d.text((90,y+90),'🟨 Bajarilgan     ⬛ Hali bajarilmagan',font=font(25),fill=muted)
    out=BASE/f'.calendar_{uid}.png'; im.save(out,quality=95); return out

# -------------------- HADITH FLOW --------------------
def hadith_for_user(uid):
    u=get_user(uid)
    if not u or not u['audience']: return None
    # Strict sequence: first 101-hadis source, never random.
    # Same hadith is shown for the whole day; learned state decides completion.
    idx=(today().toordinal()-1) % len(HADITHS)
    return HADITHS[idx]

async def send_hadith(chat_id,uid,ctx,first=False):
    h=hadith_for_user(uid)
    if not h:
        await ctx.bot.send_message(chat_id,'Avval auditoriyani tanlang:',reply_markup=audience_menu()); return
    p=render_hadith_card(h)
    cap=(f'📖 <b>BUGUNGI HADIS</b>\n\n{h["text"]}\n\n'
         f'📚 <b>Manba:</b> {h["source_full"]}\n'
         f'ℹ️ PDFda alohida hadis darajasi ko‘rsatilmagan.')
    with open(p,'rb') as f:
        await ctx.bot.send_photo(chat_id,f,caption=cap,reply_markup=hadith_buttons(h['id']),parse_mode='HTML')

# -------------------- HANDLERS --------------------
async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; register(user)
    if ctx.args:
        try:
            inviter=int(ctx.args[0])
            if inviter!=user.id:
                c=conn(); c.execute('INSERT OR IGNORE INTO invite_events(inviter,invited,created_at) VALUES(?,?,?)',(inviter,user.id,now().isoformat())); c.commit(); c.close()
        except ValueError: pass
    u=get_user(user.id)
    if u and u['audience']:
        await update.message.reply_text('🌙 <b>ENG ZARUR ILM</b>\n\nBugungi hadisni davom ettiramiz. Quyidagi menyudan bo‘limni tanlang.',parse_mode='HTML',reply_markup=main_menu())
        await send_hadith(update.effective_chat.id,user.id,ctx,first=False)
    else:
        await update.message.reply_text('🌙 <b>ENG ZARUR ILM</b>\n\nHar kuni bitta hadisni o‘rganib, uni odatga aylantiring.\n\n<b>Kim uchun hadislar kerak?</b>',parse_mode='HTML',reply_markup=audience_menu())
        await update.message.reply_text('Menyudan bo‘limlarni keyin ham ishlatishingiz mumkin.',reply_markup=main_menu())

async def callback(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); register(q.from_user)
    if q.data.startswith('aud:'):
        aud=q.data.split(':',1)[1]; set_audience(q.from_user.id,aud)
        label='Bolalar' if aud=='children' else 'Kattalar'
        await q.edit_message_text(f'✅ <b>{label}</b> uchun oqim tanlandi.\n\n📖 Birinchi hadis hozir yuboriladi. Keyingi kunlarda ketma-ket davom etadi.',parse_mode='HTML')
        await send_hadith(q.message.chat_id,q.from_user.id,ctx,first=True)
        return
    if q.data.startswith('learn:'):
        hid=int(q.data.split(':',1)[1])
        c=conn(); c.execute('INSERT OR IGNORE INTO learned(uid,day,hadith_id) VALUES(?,?,?)',(q.from_user.id,day_s(),hid)); c.commit(); c.close()
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f'✅ <b>Bilib oldim</b> saqlandi!\n\n🔥 Joriy seriya: <b>{streak(q.from_user.id)} kun</b>',parse_mode='HTML',reply_markup=main_menu())
        return
    if q.data=='today': await send_hadith(q.message.chat_id,q.from_user.id,ctx); return

async def text_handler(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    register(update.effective_user); t=(update.message.text or '').strip(); uid=update.effective_user.id
    if t=='📖 Bugungi hadis': await send_hadith(update.effective_chat.id,uid,ctx)
    elif t=='📊 Statistikam':
        p=render_dashboard(uid); await update.message.reply_photo(open(p,'rb'),caption='📊 <b>Shaxsiy statistika</b>',parse_mode='HTML',reply_markup=main_menu())
    elif t=='🏆 Reyting': await show_rating(update.effective_chat.id,uid,ctx)
    elif t=='🔥 Streakim': await update.message.reply_text(f'🔥 <b>Joriy seriya:</b> {streak(uid)} kun\n🏆 <b>Eng uzun seriya:</b> {longest_streak(uid)} kun',parse_mode='HTML')
    elif t=='📅 Kalendar':
        p=render_calendar(uid); await update.message.reply_photo(open(p,'rb'),caption='📅 <b>Oylik faollik kalendari</b>',parse_mode='HTML')
    elif t=='👤 Profil': await show_profile(update.effective_chat.id,uid,ctx)
    elif t=='📤 Do‘stlarga ulashish':
        link=f'https://t.me/{BOT_USERNAME}?start={uid}'
        await update.message.reply_text(f'📤 <b>Do‘stlaringizni taklif qiling</b>\n\nSizning havolangiz:\n{link}\n\n👥 Siz orqali qo‘shilganlar: {invite_count(uid)}',parse_mode='HTML')
    elif t=='🎯 Sozlamalar': await update.message.reply_text('🎯 <b>Sozlamalar</b>\n\nAuditoriyani almashtirish:',parse_mode='HTML',reply_markup=audience_menu())
    elif t=='ℹ️ Yordam': await help_text(update.effective_chat.id,ctx)
    elif ctx.user_data.get('broadcast') and uid in ADMIN_IDS: await do_broadcast(update,ctx)

async def show_rating(chat,uid,ctx):
    rows,pos=ranking(uid); lines=['🏆 <b>ENG FAOL O‘RGANUVCHILAR</b>','']
    for i,r in enumerate(rows[:10],1):
        mark='🥇' if i==1 else '🥈' if i==2 else '🥉' if i==3 else f'{i}.'
        lines.append(f'{mark} {r["name"]} — {r["n"]} kun')
    if pos: lines += ['',f'📍 <b>Sizning o‘rningiz:</b> {pos}']
    await ctx.bot.send_message(chat, '\n'.join(lines),parse_mode='HTML',reply_markup=main_menu())

async def show_profile(chat,uid,ctx):
    u=get_user(uid); n=invite_count(uid); await ctx.bot.send_message(chat,
        f'👤 <b>{u["name"]}</b>\n\n📚 O‘rganilgan: <b>{learned_count(uid)}</b> hadis\n🔥 Seriya: <b>{streak(uid)}</b> kun\n🏆 Eng uzun: <b>{longest_streak(uid)}</b> kun\n👥 Takliflar: <b>{n}</b>',parse_mode='HTML',reply_markup=main_menu())

def invite_count(uid):
    c=conn(); n=c.execute('SELECT COUNT(*) FROM invite_events WHERE inviter=?',(uid,)).fetchone()[0]; c.close(); return n

async def help_text(chat,ctx):
    await ctx.bot.send_message(chat,'ℹ️ <b>Qanday ishlaydi?</b>\n\n1. Auditoriyani tanlaysiz.\n2. Hadis keladi.\n3. O‘qib, <b>Bilib oldim</b>ni bosasiz.\n4. Streak va statistikangiz yuritiladi.\n5. Reytingda o‘rningiz ko‘rinadi.\n\nHadislar avval 101-hadis.pdf tartibida ketadi.',parse_mode='HTML',reply_markup=main_menu())

# -------------------- ADMIN --------------------
async def admin(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return await update.message.reply_text('⛔ Faqat admin uchun.')
    c=conn(); users=c.execute('SELECT COUNT(*) FROM users WHERE active=1').fetchone()[0]; learned=c.execute('SELECT COUNT(*) FROM learned').fetchone()[0]; c.close()
    await update.message.reply_text(f'👑 <b>ADMIN</b>\n\n👥 Faol foydalanuvchilar: {users}\n📖 Bajarilganlar: {learned}\n📚 Hozirgi tekshirilgan baza: {len(HADITHS)} yozuv\n\n/broadcast — e’lon yuborish\n/test_hadis — test hadis',parse_mode='HTML')

async def test_hadis(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    if not get_user(update.effective_user.id)['audience']: set_audience(update.effective_user.id,'adults')
    await send_hadith(update.effective_chat.id,update.effective_user.id,ctx)

async def broadcast(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    ctx.user_data['broadcast']=True; await update.message.reply_text('📢 E’lon matni yoki rasmini yuboring.')

async def do_broadcast(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ctx.user_data['broadcast']=False; c=conn(); users=c.execute('SELECT id FROM users WHERE active=1').fetchall(); c.close(); sent=fail=0
    for r in users:
        try:
            await ctx.bot.copy_message(r['id'],update.effective_chat.id,update.effective_message.message_id); sent+=1
        except Exception: fail+=1
    await update.message.reply_text(f'📢 Yuborildi: {sent}\nYetkazilmadi: {fail}')

# -------------------- SCHEDULE --------------------
async def daily_job(ctx):
    # First-day protection: a user who joined today already received the first hadith.
    c=conn(); users=c.execute("SELECT id,audience,joined_at FROM users WHERE active=1 AND audience IS NOT NULL").fetchall(); c.close()
    for r in users:
        try:
            joined=datetime.fromisoformat(r['joined_at']).astimezone(TZ).date()
            if joined==today(): continue
            await send_hadith(r['id'],r['id'],ctx)
        except Exception:
            continue

async def reminder_job(ctx):
    c=conn(); users=c.execute('SELECT id FROM users WHERE active=1 AND audience IS NOT NULL').fetchall(); c.close()
    for r in users:
        if learned_today(r['id']): continue
        try: await ctx.bot.send_message(r['id'],'🌱 Bugungi hadisni o‘qib, <b>Bilib oldim</b> tugmasini bosishni unutmang.\n\n🔥 Seriyangizni saqlab qoling!',parse_mode='HTML')
        except Exception: pass

# -------------------- HEALTH / MAIN --------------------
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/plain; charset=utf-8'); self.end_headers(); self.wfile.write(b'Eng_zarur_ilm_bot OK')
    def log_message(self,*args): pass

def start_health():
    threading.Thread(target=lambda:HTTPServer(('0.0.0.0',PORT),Health).serve_forever(),daemon=True).start()

def main():
    if not TOKEN: raise RuntimeError('TELEGRAM_BOT_TOKEN topilmadi')
    init_db(); start_health()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',start))
    app.add_handler(CommandHandler('admin',admin))
    app.add_handler(CommandHandler('broadcast',broadcast))
    app.add_handler(CommandHandler('test_hadis',test_hadis))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    app.job_queue.run_daily(daily_job,time=time(6,0,tzinfo=TZ),name='daily_hadith')
    app.job_queue.run_daily(reminder_job,time=time(12,0,tzinfo=TZ),name='reminder_1')
    app.job_queue.run_daily(reminder_job,time=time(20,0,tzinfo=TZ),name='reminder_2')
    print('ENG_ZARUR_ILM_BOT v3 started',TZ,PORT)
    app.run_polling(drop_pending_updates=True)

if __name__=='__main__': main()
