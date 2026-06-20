# Registon | TEST Bot — Render'ga deploy qilish

## 📁 Fayl tarkibi
```
registon_test_bot.py   ← Bot kodi (webhook tayyor)
requirements.txt       ← Python paketlar ro'yxati
render.yaml            ← Render sozlamalari
```

---

## 🚀 Qadamba-qadam ko'rsatma

### 1. GitHub repozitoriy yarating
1. https://github.com → "New repository" tugmasi
2. Nom bering: `registon-test-bot`
3. "Create repository" bosing
4. Uchala faylni upload qiling:
   - `registon_test_bot.py`
   - `requirements.txt`
   - `render.yaml`

### 2. Render'da servis yarating
1. https://render.com → ro'yxatdan o'ting (bepul)
2. "New +" → "Web Service" tanlang
3. GitHub'ni ulang va `registon-test-bot` reponi tanlang
4. Quyidagi sozlamalar avtomatik to'ladi (`render.yaml` dan):
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python registon_test_bot.py`

### 3. Environment Variables (muhim!)
Render dashboard → "Environment" bo'limida quyidagilarni qo'shing:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | `@BotFather`dan olingan token |
| `WEBHOOK_URL` | `https://registon-test-bot.onrender.com` (deploy tugaganidan keyin URL ko'rinadi) |
| `ADMIN_IDS` | `8307855834` (bir nechta bo'lsa vergul bilan: `111,222,333`) |

> ⚠️ `WEBHOOK_URL` ni deploy tugaganidan **keyin** to'ldirasiz. Render sizga URL beradi.

### 4. Deploy va tekshirish
1. "Create Web Service" → deploy boshlanadi
2. Logs bo'limida `Bot ishga tushmoqda...` ko'rsangiz — muvaffaqiyatli!
3. Agar `WEBHOOK_URL` qo'shilmagan bo'lsa, bot polling rejimida ishlaydi (local test uchun)

---

## ⚙️ Muhim eslatmalar

- **SQLite bazasi** Render'dagi `/opt/render/project/src/` papkasida saqlanadi
- Render **free tier** da servis 15 daqiqa faolsiz bo'lsa **uxlab qoladi** — bu muammo emas, birinchi so'rovda uyg'onadi
- Bot token'ni hech qachon kodga yozib qo'ymang — faqat Environment Variables orqali bering!

---

## 🔧 Lokal test (kompyuterda)
```bash
pip install -r requirements.txt
# .env faylga yozing yoki to'g'ridan terminal orqali bering:
BOT_TOKEN="tokeningiz" python registon_test_bot.py
# WEBHOOK_URL bo'lmasa polling rejimida ishlaydi
```
