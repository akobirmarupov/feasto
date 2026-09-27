# Feasto — Backend (API)

Restoran va to'yxonalarni onlayn qidirish, filtrlash va bron qilish platformasining serveri.
Django 5.2 + DRF + PostgreSQL + Redis + Celery. Faqat API: veb va mobil ilova alohida loyihada.

Frontend jamoasi uchun batafsil hujjat: **[docs/API.md](docs/API.md)**. Interaktiv sxema: `/swagger/`, `/redoc/`.
Serverga chiqarish (systemd, zaxira): **[deploy/DEPLOY.md](deploy/DEPLOY.md)**.

---

## Tuzilma

```
<app>/
├── routes/
│   ├── serializers.py         ← ilovaning barcha serializerlari
│   └── <model>_api.py         ← shu model uchun APIView'lar (bitta model — bitta fayl)
├── urls.py                    ← yo'llar fayl bo'yicha guruhlangan
├── models.py · services.py · signals.py · tasks.py · filters.py · admin.py · tests.py
```

| Ilova | Modellar | API fayllari |
|---|---|---|
| `account` | `User` | `google_api`, `auth_api`, `admin_user_api` |
| `common` | `PlatformSettings`, `Feedback` | `health_api`, `platform_settings_api`, `feedback_api` |
| `businesses` | `BusinessApplication`, `Business`, `BusinessPhoto`, `Room`, `Hall`, `VenuePricing`, `Favorite` | `business_api`, `business_application_api`, `business_photo_api`, `room_api`, `hall_api`, `venue_pricing_api`, `favorite_api` |
| `catalog` | `RestaurantMenuItem`, `VenueMenuItem` | `restaurant_menu_item_api`, `venue_menu_item_api` |
| `reservations` | `Availability`, `Reservation` | `availability_api`, `reservation_api` |
| `reviews` | `Review`, `ReviewPhoto` | `review_api`, `review_photo_api` |
| `subscriptions` | `SubscriptionPlan`, `Subscription`, `SubscriptionRequest`, `PaymentLog` | `subscription_plan_api`, `subscription_api`, `subscription_request_api`, `payment_log_api` |
| `content` | `Banner`, `News` | `banner_api`, `news_api` |
| `notifications` | `Notification`, `Device` | `notification_api`, `device_api` |

Biznes mantiq `services.py` da: u API'dan ham, admin paneldan ham, Celery'dan ham chaqiriladi.

---

## Rollar

Rasman ikkita rol: `user` va `business`. Super-admin — alohida rol emas, Django `is_staff`.
Restoran va to'yxona egasi bitta rolda, `business.type` (`restaurant` | `venue`) bilan ajraladi.

Login javobi frontendga panelni tanlash uchun yetarli ma'lumot beradi:

```json
{"access": "...", "refresh": "...",
 "user": {"role": "business", "is_staff": false,
          "business": {"id": "...", "name": "Shoxona", "type": "restaurant",
                       "is_approved": true, "application_status": "approved",
                       "subscription_status": "trial"}}}
```

`is_staff` → admin paneli · `business.is_approved && type == "restaurant"` → Xonalar paneli ·
`type == "venue"` → Zallar paneli · `business` bor lekin `is_approved=false` → "ariza tekshiruvda" ekrani · aks holda oddiy foydalanuvchi.

**Ro'yxatdan o'tish faqat Google orqali.** Parol bilan ro'yxat, SMS tasdiq yo'q va rejalashtirilmagan.
`POST /api/auth/login/` (username/parol) faqat admin panel orqali yaratilgan hisoblar uchun.

---

## Ishga tushirish

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # qiymatlarni to'ldiring
python manage.py migrate
python manage.py seed_platform     # sozlamalar + tarif rejalari
python manage.py createsuperuser
python manage.py seed_demo         # ixtiyoriy: demo bizneslar, jadval, bronlar (faqat DEBUG)
python manage.py runserver
```

Celery (alohida terminallarda, **beat faqat bitta nusxada**):

```bash
celery -A config worker -l info
celery -A config beat -l info
```

| Vazifa | Qachon | Nima qiladi |
|---|---|---|
| `complete_past_reservations_task` | har 15 daqiqa | vaqti tugagan bronni yakunlaydi, mijozdan sharh so'raydi |
| `check_expired_subscriptions_task` | har kuni 03:00 | muddati o'tgan obunani yopadi, biznesni yashiradi |
| `notify_expiring_subscriptions_task` | har kuni 09:00 | 5/3/2 kun qolganda egasiga eslatma |
| `heartbeat_task` | har daqiqa | `/api/health/` dagi `celery` holati |
| `send_admin_telegram_task`, `send_push_task` | hodisa bo'yicha | Telegram va push yuborish |

Boshqa buyruqlar: `reset_data --yes` (sinov ma'lumotini tozalash), `repair_data`, `check --deploy`.

Testlar: `python manage.py test` (700+ test, ~3 daqiqa).

---

## Integratsiyalar (hammasi `.env` orqali, bo'sh bo'lsa o'chiq)

| Nima | O'zgaruvchi | Izoh |
|---|---|---|
| Google kirish | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `FRONTEND_LOGIN_URL` | Google Console → redirect URI: `https://<api>/api/auth/google/callback/` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` | yangi ariza, bron, obuna so'rovi, taklif → admin guruhiga |
| Push (FCM v1) | `FCM_SERVICE_ACCOUNT_FILE`, `FCM_PROJECT_ID` | ilova `POST /api/notifications/devices/` bilan token beradi |
| Sentry | `SENTRY_DSN` | Django + Celery xatolari |
| S3/MinIO | `USE_S3=True`, `AWS_*` | media fayllar uchun; aks holda `media/` papka |

---

## Xavfsizlik va unumdorlik

Argon2 parol · JWT rotatsiya + qora ro'yxat · ikki qatlamli throttling · IDOR himoyasi (biznes ID tokendan) ·
`is_staff` API orqali berilmaydi · fayl format/hajm tekshiruvi + avtomatik siqish (1600 px) ·
HSTS/secure cookie (`DEBUG=False`) · CORS allowlist · `X-Request-ID` + `logs/security.log`.

Kompozit indekslar · geo qidiruv SQL'da (bounding box + Haversine) · ommaviy ro'yxat 60 s, detal 120 s kesh
(versiyali kalit) · `rating_avg`/`reviews_count`/`rank` denormalizatsiya · `select_for_update` bilan poyga himoyasi ·
Redis yiqilsa sayt ishlayveradi.
