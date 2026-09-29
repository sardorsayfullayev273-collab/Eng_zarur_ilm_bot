# ENG ZARUR ILM — PREMIUM V2

Bu paket Telegram bot + Telegram Mini App + Render PostgreSQL uchun tayyorlangan.

## Dizayn
Ilova berilgan namuna kabi glassmorphism, kosmik/starfield fon, yashil neon va binafsha aksentlardan foydalanadi. Matnlar o‘zbekcha.

## Asosiy funksiyalar
- Bolalar + kattalar auditoriyasi
- Bugungi hadis va ketma-ket o‘qish
- Bilib oldim / streak / ochko
- Kalendar
- Reyting
- Profil va bildirishnomalar
- Hadis bo‘yicha qidiruv
- Referral havola
- Bot ichida admin va broadcast
- Mini App ichida admin statistikasi va broadcast
- PostgreSQL
- Telegram Mini App initData tekshiruvi
- Render health endpoint

## Muhim manba tartibi
1. `101-hadis.pdf` dagi mavjud raqamlangan hadislar tartibi
2. keyin navbatdagi rasmiy/ishonchli manbalar qo‘shiladi

`hadiths.json` faylidagi matnni o‘zboshimchalik bilan o‘zgartirmang.

## GitHub
Repository root ichiga quyidagilarni joylang:
- `bot.py`
- `hadiths.json`
- `requirements.txt`
- `schema.sql`
- `render.yaml`
- `SOURCE_SEQUENCE.txt`
- `web/index.html`

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`python bot.py`

Environment Variables:
- `TELEGRAM_BOT_TOKEN`
- `ADMIN_IDS`
- `DATABASE_URL`
- `TIMEZONE=Asia/Tashkent`
- `WEBAPP_URL=https://YOUR-RENDER-DOMAIN/`

PostgreSQL yaratilgandan keyin uning Internal Database URL qiymatini `DATABASE_URL` ga qo‘ying.

Mini App URL sifatida Render web servisining HTTPS manzilini qo‘ying.

## Telegram
BotFather orqali Mini App uchun URL qo‘yish yoki bot menyusida Mini App tugmasini yoqish mumkin. Kod `/start` vaqtida foydalanuvchining chat menyusiga Mini App tugmasini o‘rnatishga ham urinadi.

## Admin
`ADMIN_IDS` ga Telegram numeric ID yozing. Bir nechta ID vergul bilan ajratiladi.

Bot:
`/admin`
`/broadcast matn`

Mini App:
`Profil → Admin`

## Local test
`python bot.py` faqat `TELEGRAM_BOT_TOKEN` va `DATABASE_URL` mavjud bo‘lganda ishga tushadi.


## V2 yangilanishlari
- Start vaqtida Bolalar/Kattalar rejimini tanlash.
- Birinchi hadis tanlovdan keyin darhol yuboriladi.
- 06:00 yuborilishida shu kunning hadisiga ikki marta yuborishni oldini olish uchun `last_daily_sent` nazorati.
- 100-hadis tugagach, hadis 1 ga qaytib aylanmaydi. Keyingi manba qo‘shilganda ketma-ketlik davom ettiriladi.
- Mini Appda 7 kunlik faollik statistikasi.
- Ulashish uchun Telegram/Web Share fallback.

## Muhim
Hozirgi `hadiths.json` faylida `101-hadis.pdf`ning mavjud raqamlangan **1–100** hadislari bor. Keyingi Sahihi Buxoriy va boshqa manbalar matnlari manba fayllari bilan tekshirilmasdan o‘zboshimchalik bilan qo‘shilmaydi. `SOURCE_SEQUENCE.txt`dagi tartib saqlanadi.
