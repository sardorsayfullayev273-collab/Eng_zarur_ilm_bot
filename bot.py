import os, sqlite3, threading, urllib.parse
from datetime import datetime, date, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

load_dotenv()
TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
ADMIN_IDS={int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()}
TZ=ZoneInfo(os.getenv('TIMEZONE','Asia/Tashkent')); DB=os.getenv('DB_PATH','hadis_bot.db'); PORT=int(os.getenv('PORT','10000'))

# Starter hadith library. Uzbek text is a concise meaning/paraphrase; references are included for verification.
H=[
('children','Amallar niyatga bog‘liq. Har bir inson o‘zi niyat qilgan narsasiga yarasha natija oladi.','Yaxshi ishni yaxshi niyat bilan boshlang.','Sahih al-Bukhari','1','Sahih'),
('children','Kim boshqalarga rahm-shafqat qilmasa, unga ham rahm-shafqat qilinmaydi.','Ota-ona, ustoz, do‘stlar va kichiklarga mehribon bo‘ling.','Sahih al-Bukhari','6013','Sahih'),
('children','Kuchli mo‘min Allohga suyukliroqdir; foydali narsaga intiling, Allohdan yordam so‘rang va ojizlanmang.','Ilm olishda harakat qiling va foydali ishlarni tanlang.','Sahih Muslim','2664','Sahih'),
('adults','Amallar niyatga bog‘liq. Har bir inson o‘zi niyat qilgan narsasiga yarasha natija oladi.','Har bir amal oldidan niyatni to‘g‘rilash muhim.','Sahih al-Bukhari','1','Sahih'),
('adults','Din — nasihat va samimiylikdir: Allohga, Uning Kitobiga, Rasuliga va musulmonlarga nisbatan xolis bo‘lish.','Samimiylik va yaxshilikni hayotingizning asosiy mezonlaridan qiling.','Sahih Muslim','55a','Sahih'),
('adults','Kuchli mo‘min Allohga suyukliroqdir; foydali narsaga intiling, Allohdan yordam so‘rang va ojizlanmang.','Foydali ishga intiling, Allohdan yordam so‘rang va qiyinchilikda taslim bo‘lmang.','Sahih Muslim','2664','Sahih')]

def db():
 c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c

def init_db():
 c=db(); c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT NOT NULL,audience TEXT,active INTEGER DEFAULT 1,joined_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS hadiths(id INTEGER PRIMARY KEY AUTOINCREMENT,audience TEXT NOT NULL,text TEXT NOT NULL,lesson TEXT NOT NULL,source TEXT NOT NULL,no TEXT NOT NULL,grade TEXT NOT NULL); CREATE TABLE IF NOT EXISTS learned(uid INTEGER NOT NULL,day TEXT NOT NULL,hadith INTEGER NOT NULL,PRIMARY KEY(uid,day));''')
 if c.execute('SELECT COUNT(*) n FROM hadiths').fetchone()['n']==0:
  c.executemany('INSERT INTO hadiths(audience,text,lesson,source,no,grade) VALUES(?,?,?,?,?,?)',H); c.commit()
 c.close()

def today(): return datetime.now(TZ).date()
def today_s(): return today().isoformat()
def reg(u):
 c=db(); c.execute('''INSERT INTO users(id,name,active,joined_at) VALUES(?,?,1,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,active=1''',(u.id,u.full_name or 'Foydalanuvchi',datetime.now(TZ).isoformat())); c.commit(); c.close()
def menu(): return InlineKeyboardMarkup([[InlineKeyboardButton('👦 Bolalar uchun',callback_data='aud:children'),InlineKeyboardButton('👨 Kattalar uchun',callback_data='aud:adults')],[InlineKeyboardButton('🏆 Reyting',callback_data='rating'),InlineKeyboardButton('📊 Statistikam',callback_data='stats')]])
def font(n,b=False):
 p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
 return ImageFont.truetype(p,n) if os.path.exists(p) else ImageFont.load_default()
def wrap(d,t,f,w):
 out=[]; cur=''
 for x in t.split():
  z=x if not cur else cur+' '+x
  if d.textbbox((0,0),z,font=f)[2]<=w: cur=z
  else: out.append(cur); cur=x
 if cur: out.append(cur)
 return out
def card(h):
 im=Image.new('RGB',(1080,1080),(20,65,50)); d=ImageDraw.Draw(im); tf=font(58,1); bf=font(43); lf=font(37); sf=font(28)
 d.text((60,50),'ENG ZARUR ILM',font=tf,fill=(245,220,150)); d.text((60,135),'BUGUNGI HADIS',font=font(34,1),fill='white')
 y=220
 for x in wrap(d,h['text'],bf,950): d.text((60,y),x,font=bf,fill='white'); y+=62
 y+=35; d.text((60,y),'💡 Hayotiy saboq',font=font(34,1),fill=(245,220,150)); y+=55
 for x in wrap(d,h['lesson'],lf,950): d.text((60,y),x,font=lf,fill=(240,240,240)); y+=50
 d.text((60,970),f"Manba: {h['source']} №{h['no']} • {h['grade']}",font=sf,fill=(225,225,225))
 p=f'/tmp/hadith_{h["id"]}.png'; im.save(p); return p
def choose(a):
 c=db(); r=c.execute('SELECT * FROM hadiths WHERE audience=? ORDER BY id',(a,)).fetchall(); c.close(); return r[(today().toordinal()-1)%len(r)] if r else None
def learned_today(uid):
 c=db(); x=c.execute('SELECT 1 FROM learned WHERE uid=? AND day=?',(uid,today_s())).fetchone(); c.close(); return bool(x)
def streak(uid):
 c=db(); rs=c.execute('SELECT day FROM learned WHERE uid=? ORDER BY day DESC',(uid,)).fetchall(); c.close(); days={date.fromisoformat(x['day']) for x in rs}
 d=today() if today() in days else today()-timedelta(days=1); n=0
 while d in days: n+=1; d-=timedelta(days=1)
 return n
def kb(h):
 text=urllib.parse.quote('📖 Bugungi hadis:\n\n'+h['text'])
 return InlineKeyboardMarkup([[InlineKeyboardButton('✅ Bilib oldim',callback_data=f"learn:{h['id']}"),InlineKeyboardButton('📤 Ulashish',url='https://t.me/share/url?text='+text)]])

async def start(u,ctx): reg(u.effective_user); await u.message.reply_text('Assalomu alaykum! 🌙\n\n«Eng zarur ilm» botiga xush kelibsiz.\nHar kuni 06:00 da hadis olish uchun tanlang:',reply_markup=menu())
async def rating_cmd(u,ctx): reg(u.effective_user); await rating(u.effective_chat.id,ctx)
async def rating(chat,ctx):
 c=db(); rs=c.execute('SELECT u.name,COUNT(l.day) n FROM users u LEFT JOIN learned l ON l.uid=u.id WHERE u.active=1 GROUP BY u.id ORDER BY n DESC,u.name LIMIT 10').fetchall(); c.close()
 await ctx.bot.send_message(chat,'🏆 FAOL O‘RGANUVCHILAR REYTINGI\n\n'+('\n'.join(f'{i}. {r["name"]} — {r["n"]} kun' for i,r in enumerate(rs,1)) if rs else 'Hozircha reyting bo‘sh.'))
async def stats_cmd(u,ctx): reg(u.effective_user); await stats(u.effective_chat.id,u.effective_user.id,ctx)
async def stats(chat,uid,ctx):
 c=db(); n=c.execute('SELECT COUNT(*) n FROM learned WHERE uid=?',(uid,)).fetchone()['n']; c.close(); await ctx.bot.send_message(chat,f'📊 SHAXSIY STATISTIKA\n\n📖 O‘rganilgan hadislar: {n}\n🔥 Davomiylik: {streak(uid)} kun\n✅ Bugun: {"Bajarildi" if learned_today(uid) else "Hali bajarilmadi"}')
async def cb(u,ctx):
 q=u.callback_query; await q.answer(); reg(q.from_user)
 if q.data.startswith('aud:'):
  a=q.data.split(':')[1]; c=db(); c.execute('UPDATE users SET audience=?,active=1 WHERE id=?',(a,q.from_user.id)); c.commit(); c.close(); await q.edit_message_text(('👦 Bolalar' if a=='children' else '👨 Kattalar')+' uchun hadislar tanlandi.\n\nHar kuni 06:00 da hadis, 12:00 va 20:00 da eslatma keladi.\n\n🏆 /rating\n📊 /stats')
 elif q.data.startswith('learn:'):
  hid=int(q.data.split(':')[1]); c=db(); c.execute('INSERT OR IGNORE INTO learned(uid,day,hadith) VALUES(?,?,?)',(q.from_user.id,today_s(),hid)); c.commit(); c.close(); await q.edit_message_reply_markup(reply_markup=None); await q.message.reply_text(f'🌱 Ma shaa Alloh! Bugungi hadis o‘rganildi.\n🔥 Davomiylik: {streak(q.from_user.id)} kun')
 elif q.data=='rating': await rating(q.message.chat_id,ctx)
 elif q.data=='stats': await stats(q.message.chat_id,q.from_user.id,ctx)

async def daily(ctx):
 c=db(); us=c.execute('SELECT id,audience FROM users WHERE active=1 AND audience IS NOT NULL').fetchall(); c.close()
 for u in us:
  h=choose(u['audience'])
  if not h: continue
  p=card(h); cap=f"📖 BUGUNGI HADIS\n\n{h['text']}\n\n💡 {h['lesson']}\n\nManba: {h['source']} №{h['no']} | {h['grade']}"
  try:
   with open(p,'rb') as f: await ctx.bot.send_photo(u['id'],f,caption=cap,reply_markup=kb(h))
  except Exception:
   c2=db(); c2.execute('UPDATE users SET active=0 WHERE id=?',(u['id'],)); c2.commit(); c2.close()
async def remind(ctx):
 c=db(); us=c.execute('SELECT id FROM users WHERE active=1 AND audience IS NOT NULL').fetchall(); c.close()
 for u in us:
  if learned_today(u['id']): continue
  try: await ctx.bot.send_message(u['id'],'🌱 Bugungi hadisni hali «Bilib oldim» deb belgilamadingiz.\n\nHadisni qayta o‘qib, bugungi ilmni mustahkamlang. 🤲')
  except Exception:
   c2=db(); c2.execute('UPDATE users SET active=0 WHERE id=?',(u['id'],)); c2.commit(); c2.close()
async def admin(u,ctx):
 if u.effective_user.id not in ADMIN_IDS: return await u.message.reply_text('⛔ Bu bo‘lim faqat admin uchun.')
 c=db(); n=c.execute('SELECT COUNT(*) n FROM users WHERE active=1').fetchone()['n']; l=c.execute('SELECT COUNT(*) n FROM learned').fetchone()['n']; c.close(); await u.message.reply_text(f'👑 ADMIN PANEL\n\n👥 Faol foydalanuvchilar: {n}\n📖 Bajarilgan hadislar: {l}\n\n📢 /broadcast — e’lon yuborish')
async def broadcast(u,ctx):
 if u.effective_user.id not in ADMIN_IDS: return await u.message.reply_text('⛔ Bu bo‘lim faqat admin uchun.')
 ctx.user_data['broadcast']=True; await u.message.reply_text('📢 E’lon matni yoki rasmini yuboring. Rasm yuborsangiz caption ham jo‘natiladi.')
async def admin_msg(u,ctx):
 if u.effective_user.id not in ADMIN_IDS or not ctx.user_data.get('broadcast'): return
 ctx.user_data['broadcast']=False; c=db(); us=c.execute('SELECT id FROM users WHERE active=1').fetchall(); c.close(); s=f=0
 for x in us:
  try: await ctx.bot.copy_message(x['id'],u.effective_chat.id,u.effective_message.message_id); s+=1
  except Exception: f+=1
 await u.message.reply_text(f'📢 E’lon yakunlandi.\n\nYuborildi: {s}\nYetkazilmadi: {f}')

class Health(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.send_header('Content-Type','text/plain; charset=utf-8'); self.end_headers(); self.wfile.write(b'Eng_zarur_ilm_bot OK')
 def log_message(self,*args): pass
def health_server(): threading.Thread(target=lambda: HTTPServer(('0.0.0.0',PORT),Health).serve_forever(),daemon=True).start()

def main():
 if not TOKEN: raise RuntimeError('TELEGRAM_BOT_TOKEN topilmadi')
 init_db(); health_server(); app=Application.builder().token(TOKEN).build()
 app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('rating',rating_cmd)); app.add_handler(CommandHandler('stats',stats_cmd)); app.add_handler(CommandHandler('admin',admin)); app.add_handler(CommandHandler('broadcast',broadcast)); app.add_handler(CallbackQueryHandler(cb)); app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND,admin_msg))
 app.job_queue.run_daily(daily,time=time(6,0,tzinfo=TZ),name='daily_hadith'); app.job_queue.run_daily(remind,time=time(12,0,tzinfo=TZ),name='remind_noon'); app.job_queue.run_daily(remind,time=time(20,0,tzinfo=TZ),name='remind_evening')
 print('Eng_zarur_ilm_bot ishga tushdi'); print('Timezone:',TZ,'Port:',PORT); app.run_polling(drop_pending_updates=True)
if __name__=='__main__': main()
