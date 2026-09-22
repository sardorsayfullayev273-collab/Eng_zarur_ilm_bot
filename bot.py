import os,sqlite3
from datetime import datetime,time
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,ContextTypes
load_dotenv(); TOKEN=os.getenv("TELEGRAM_BOT_TOKEN"); ADM={int(x) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}
TZ=ZoneInfo(os.getenv("TIMEZONE","Asia/Tashkent")); DB="hadis_bot.db"
def con(): c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
 c=con(); c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,audience TEXT,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS hadiths(id INTEGER PRIMARY KEY AUTOINCREMENT,audience TEXT,text TEXT,explanation TEXT,source TEXT,no TEXT,grade TEXT);
CREATE TABLE IF NOT EXISTS learned(uid INTEGER,day TEXT,hadith INTEGER,PRIMARY KEY(uid,day));"""); c.commit();c.close()
def day(): return datetime.now(TZ).date().isoformat()
def start_kb(): return InlineKeyboardMarkup([[InlineKeyboardButton("👦 Bolalar uchun",callback_data="aud:children")],[InlineKeyboardButton("👨 Kattalar uchun",callback_data="aud:adults")]])
async def start(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
 x=u.effective_user;c=con();c.execute("INSERT OR IGNORE INTO users(id,name) VALUES(?,?)",(x.id,x.full_name));c.commit();c.close()
 await u.message.reply_text("Assalomu alaykum! 🌙\nKim uchun hadislar kerak?",reply_markup=start_kb())
async def cb(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
 q=u.callback_query;await q.answer(); x=q.from_user
 if q.data.startswith("aud:"):
  a=q.data[4:];c=con();c.execute("UPDATE users SET audience=? WHERE id=?",(a,x.id));c.commit();c.close()
  await q.edit_message_text("✅ Tanlov saqlandi.\nHar kuni soat 06:00 da hadis yuboriladi.")
 elif q.data.startswith("learn:"):
  hid=int(q.data[6:]);c=con();c.execute("INSERT OR IGNORE INTO learned VALUES(?,?,?)",(x.id,day(),hid));c.commit();c.close()
  await q.edit_message_reply_markup(reply_markup=None);await q.message.reply_text("🌱 Ma shaa Alloh! Bugungi hadis o‘rganildi.")
async def rating(u,ctx):
 c=con(); rows=c.execute("SELECT name,COUNT(*) n FROM learned JOIN users ON users.id=learned.uid GROUP BY uid ORDER BY n DESC LIMIT 10").fetchall();c.close()
 await u.message.reply_text("🏆 REYTING\n\n"+"\n".join(f"{i+1}. {r['name']} — {r['n']} hadis" for i,r in enumerate(rows)) or "Hozircha reyting bo‘sh.")
async def admin(u,ctx):
 if u.effective_user.id not in ADM:return await u.message.reply_text("Faqat admin uchun.")
 c=con();n=c.execute("SELECT COUNT(*) n FROM users WHERE active=1").fetchone()["n"];l=c.execute("SELECT COUNT(*) n FROM learned").fetchone()["n"];c.close()
 await u.message.reply_text(f"👑 ADMIN\n\n👥 Obunachilar: {n}\n📖 O‘rganilgan hadislar: {l}")
async def daily(ctx):
 c=con(); users=c.execute("SELECT id,audience FROM users WHERE active=1 AND audience IS NOT NULL").fetchall()
 for u in users:
  h=c.execute("SELECT * FROM hadiths WHERE audience=? ORDER BY id LIMIT 1",(u["audience"],)).fetchone()
  if not h: continue
  txt=f"📖 BUGUNGI HADIS\n\n{h['text']}\n\n💡 {h['explanation']}\n\nManba: {h['source']} №{h['no']} | {h['grade']}"
  kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Bilib oldim",callback_data=f"learn:{h['id']}"),InlineKeyboardButton("📤 Ulashish",switch_inline_query=h["text"])]])
  try: await ctx.bot.send_message(u["id"],txt,reply_markup=kb)
  except: pass
 c.close()
def main():
 if not TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN kerak")
 init();a=Application.builder().token(TOKEN).build();a.add_handler(CommandHandler("start",start));a.add_handler(CommandHandler("rating",rating));a.add_handler(CommandHandler("admin",admin));a.add_handler(CallbackQueryHandler(cb))
 a.job_queue.run_daily(daily,time=time(6,0,tzinfo=TZ));a.run_polling()
if __name__=="__main__":main()
