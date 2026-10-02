# Telegram Bot — Ab18

بوت تيليغرام متكامل لإدارة المحتوى التعليمي، الاختبارات، والمحادثة بالذكاء الاصطناعي.

## كيفية التشغيل

```
python main.py
```

زر Run يشغّل workflow **Project** الذي يشغّل خدمتين:

- **Start application**: `RUN_EMBEDDED_WEBSITE=0 python main.py` — بوت Telegram فقط.
- **Website**: `python run_website.py` — موقع Flask على `0.0.0.0:5000`.

تشغيل `python main.py` مباشرةً يشغّل الموقع المدمج أيضاً. عند تشغيل الموقع بشكل مستقل، استخدم `RUN_EMBEDDED_WEBSITE=0` لتجنّب تعارض المنفذ.

## المتطلبات (Secrets)

| المتغير | الوصف |
|---|---|
| `TELEGRAM_BOT_TOKEN` | توكن البوت من @BotFather |
| `MONGODB_URI` | رابط قاعدة بيانات MongoDB |
| `GEMINI_API_KEY` | مفتاح Google Gemini AI |
| `SUPER_ADMIN_ID` | معرّف المشرف الرئيسي (مضبوط في env vars) |

## Stack

- **Python 3.11**
- **python-telegram-bot 20.7** — Long Polling mode
- **MongoDB (pymongo)** — قاعدة البيانات الرئيسية
- **Google Gemini** — ميزات الذكاء الاصطناعي
- **Flask + Gunicorn** — الموقع الإلكتروني المستقل

## هيكل المشروع

```
main.py              — نقطة الدخول الرئيسية
bot/
  shared.py          — الإعدادات العامة والمتغيرات
  loader.py          — تحميل الوحدات ديناميكياً
  data_access.py     — كل عمليات MongoDB
  content_delivery.py — إرسال المحتوى للمستخدمين
  quiz_challenge.py   — نظام الاختبارات
  study_sessions.py   — جلسات الدراسة (Pomodoro)
  callback_handlers/  — معالجات أزرار الـ inline keyboard
  features/           — ميزات إضافية (AI chat, حاسبة, عداد...)
```

## ملاحظات

- إذا ظهر خطأ **Conflict** فهذا يعني وجود نسخة أخرى من البوت تعمل في مكان آخر — أوقفها أولاً.
- البوت يستخدم أحرف Unicode غير مرئية لتشفير معرّفات الأزرار داخل نص الرسائل.

## User preferences

- المستخدم يريد إجراء تعديلات بسيطة على البوت بعد إعداده.

## حالة الإعداد على Replit

- تم تثبيت حزم `requirements.txt` في بيئة Python 3.11.
- `SESSION_SECRET` متوفر؛ `TELEGRAM_BOT_TOKEN` و`MONGODB_URI` و`GEMINI_API_KEY` غير متوفرة عند الإعداد الحالي.
- البوت لا يستطيع العمل قبل إضافة توكن Telegram ورابط MongoDB في Secrets. مفتاح Gemini مطلوب لميزات الذكاء الاصطناعي.
- الموقع يعرض رسالة إعداد واضحة برمز HTTP 503 عند غياب `MONGODB_URI` بدلاً من عرض محتوى وهمي أو الاتصال بقاعدة بيانات بديلة.
- بعد إضافة الأسرار، أعد تشغيل الخدمتين. أوقف أي نسخة أخرى من البوت قبل تشغيله لتجنّب تعارض Long Polling.
- لم يتم التحقق من الاتصال الفعلي بـ Telegram أو MongoDB لأن المستخدم لم يقدّم الأسرار.
