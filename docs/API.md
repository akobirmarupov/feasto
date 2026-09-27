# Feasto API — frontend uchun qo'llanma

Bazaviy manzil: `https://<api-domen>/api/`. Interaktiv sxema: `/swagger/` (barcha maydonlar, misollar).
Bu hujjat sxemada yo'q narsalarni tushuntiradi: oqimlar, xato kodlari, formatlar, ruxsatlar.

## 1. Umumiy qoidalar

**Autentifikatsiya.** `Authorization: Bearer <access>`. Access 60 daqiqa, refresh 30 kun.
`POST /auth/refresh/ {"refresh"}` yangi juftlik qaytaradi, eski refresh bekor bo'ladi (rotatsiya).
401 kelsa — refresh qiling; refresh ham 401 bersa — qayta kirish.

**Javob formati.** Muvaffaqiyat — oddiy JSON. Ro'yxatlar sahifalangan:

```json
{"count": 57, "total_pages": 3, "current_page": 1, "next": "...?page=2", "previous": null, "results": [...]}
```
`?page=`, `?page_size=` (ko'pi bilan 100; sharhlar 10/sahifa, ko'pi bilan 50).

**Xato formati** — hamma joyda bir xil:

```json
{"success": false,
 "error": {"code": "bad_request", "message": "Odam o'qiydigan matn (o'zbekcha)",
           "details": {"maydon": ["xato"]}},
 "request_id": "a1b2c3"}
```
`message` — foydalanuvchiga ko'rsatsa bo'ladigan matn. `details` faqat validatsiya xatolarida (maydon → xabarlar).

| HTTP | `code` | Qachon |
|---|---|---|
| 400 | `bad_request` | validatsiya yoki mantiq xatosi |
| 400 | `phone_required` | aloqa raqamisiz ariza/bron/obuna so'rovi — profilga raqam kiritish oynasini oching |
| 400 | `application_pending` | ko'rib chiqilayotgan ariza allaqachon bor |
| 400 | `business_limit` | bitta hisobda bitta biznes |
| 400 | `trial_used` | bepul sinov ishlatilgan — pullik tarif tanlash ekraniga |
| 400 | `cancel_window_closed` | bekor qilish muddati o'tgan |
| 401 | `unauthenticated` | token yo'q / eskirgan |
| 403 | `permission_denied` | ruxsat yo'q (`message` sababini aytadi) |
| 404 | `not_found` | |
| 409 | `conflict` | bron vaqti band, jadval ochilmagan, ish vaqtidan tashqari |
| 429 | `too_many_requests` | throttle; `Retry-After` sarlavhasi bor |

**Cheklovlar (throttle).** Login/Google 10/min · bron 10/soat · biznes arizasi 3/kun · sharh 20/kun · taklif 10/kun ·
umumiy: kirgan 120/min, mehmon 40/min.

**Formatlar.** Sana `YYYY-MM-DD`, vaqt `HH:MM` (so'rovda) / `HH:MM:SS` (javobda), vaqt tamg'alari ISO 8601 `+05:00` bilan.
Pul — satr (`"250000.00"`), so'm. ID'lar UUID (foydalanuvchi ID — butun son). Rasm URL'lari to'liq (`https://...`).

**Tillar.** `?lang=uz|ru|en` yoki `Accept-Language` — faqat kontent (banner, yangilik) uchun. Xato matnlari o'zbekcha.

**Media.** Yuklash `multipart/form-data`; jpg/jpeg/png/webp, 5 MB gacha; server 1600 px gacha siqadi.

## 2. Kirish

**Google — yagona ro'yxatdan o'tish yo'li.** Hisob bo'lmasa yaratiladi, bo'lsa kiriladi.

- Mobil / SPA: Google SDK'dan `id_token` oling → `POST /auth/google/ {"credential": "<id_token>"}` →
  `{"access", "refresh", "user", "created"}`. `created=true` — yangi hisob (profil to'ldirish ekraniga).
- Redirect (veb): brauzerni `GET /auth/google/start/?next=/qayerga` ga yuboring. Google'dan qaytgach server
  `FRONTEND_LOGIN_URL#access=...&refresh=...&created=0|1&next=/qayerga` ga yo'naltiradi — fragmentni o'qib, tozalang.
  Xato: `FRONTEND_LOGIN_URL?google_error=cancelled|state|nocode|google|blocked`.
- `POST /auth/login/ {"username","password"}` — faqat admin yaratgan hisoblar.
- `POST /auth/logout/ {"refresh"}` → 205. `DELETE /auth/me/` → hisob o'chiriladi (anonimlashtiriladi), 204.

**Profil.** `GET/PATCH /auth/me/` (`full_name`, `phone_number` (+998XXXXXXXXX yoki `""` — o'chirish), `bio`, `birth_date`,
`preferred_language`; multipart bo'lsa `avatar`), `POST/DELETE /auth/me/avatar/`.
Javobda `trust` — ishonchlilik bali (`bits` 1–100, `level`, `level_display`, `tone`): har o'zi bekor qilgan bron −5.

## 3. Asosiy oqimlar

**Katalog (mehmon).** `GET /businesses/?type=restaurant|venue&search=&district=&cuisine=&min_rating=&guests=&date=YYYY-MM-DD&lat=&lng=&radius_km=`.
Filtrlar birga ishlaydi; `search` bir necha so'zli. Geo berilsa `distance_km` keladi va masofa bo'yicha tartiblanadi.
Mehmonga manzil/koordinata/telefon **berilmaydi** (`location_locked`, `contacts_locked` = true) — kirgan foydalanuvchiga beriladi.
`GET /businesses/{id}/` — to'liq profil: `gallery`, `rooms` (restoran) yoki `halls` + `dish_pricing` + `pricing_mode` (to'yxona), `menu`.
Qo'shimcha: `/businesses/{id}/rooms|halls|menu|venue-menu|photos|pricing|reviews|availability/`,
`/menu/restaurant/`, `/menu/venue/`, `/showcase/photos/`, `/banners/`, `/news/`, `/settings/`, `/subscription-plans/`.

**Sevimlilar.** `GET /favorites/` (bizneslar + `ids`), `POST /favorites/{business_id}/` (201/200), `DELETE` (204).

**Restoran broni.**
1. `GET /rooms/{room_id}/busy-hours/?date=` → `is_open`, `open_time`, `close_time`, `busy_ranges[]`, `deposit_amount`.
2. `POST /reservations/ {"room","date","start_time":"19:00","end_time":"21:00","guests_count",
   "menu_items":[uuid...]?, "special_request"?}` → 201, `status: pending`, `message` (depozit va admin Telegram).
   409 — vaqt band / jadval yo'q / ish vaqtidan tashqari.

**To'yxona broni.** `GET /halls/{hall_id}/busy-dates/?date_from=&date_to=` → `busy_dates[]`.
`POST /reservations/ {"hall","date","guests_count","dish_count":1|2|3?, "menu_items"?}`.
`dish_count` bo'lsa menyudan aynan shuncha taom kerak; bo'lmasa faqat zal ijarasi. Javobda `day_rent_price`, `price_per_person`, `total_price` (narx yo'q bo'lsa `null`).

**Bronlarim.** `GET /reservations/my/?status=&date_from=&date_to=` · `GET /reservations/{id}/` ·
`PATCH /reservations/{id}/cancel/` — `can_cancel`, `cancel_deadline`, `cancel_blocked_reason` maydonlariga qarab tugmani ko'rsating.
`GET /reservations/pending-review/` — sharh so'raladigan bronlar (ilovada oyna ochish uchun).

**Sharh.** `POST /reviews/ {"reservation","rating":1..5,"comment"}` — faqat `completed` bron. `POST /reviews/{id}/photos/` (5 tagacha).
`GET/PATCH/DELETE /reviews/{id}/`, `GET /reviews/my/`.

**Biznes ochish.** Profilda telefon bo'lishi shart (aks holda `phone_required`).
`POST /business-applications/ {"business_type","business_name","plan": uuid|null}` → `message`, `admin_telegram`, `is_trial`, `trial_days`.
`GET /business-applications/my/`. Ariza tasdiqlangunga qadar `user.business.is_approved=false`; `GET /owner/subscription/`
`status: awaiting_approval | rejected` (`can_reapply`) ekranini beradi. Tasdiqdan keyin rol `business`, obuna `trial` (yoki pullik → `active`).

**Egasi paneli** (`/owner/...`, `IsBusinessRole`): `overview/`, `business/` (GET/PATCH: `map_link` yuborilsa koordinata o'zi olinadi),
`rooms/` yoki `halls/`, `pricing/` (PUT, ro'yxat), `menu/restaurant/` yoki `menu/venue/`, `photos/`,
`availability/` + `availability/generate/ {"room"?, "start_time","end_time","year","months":[..]}`,
`reservations/` + `reservations/{id}/status/ {"status":"confirmed|cancelled|completed"}` (pending→confirmed/cancelled, confirmed→completed/cancelled),
`reviews/`, `subscription/`, `subscription/requests/` (uzaytirish arizasi), `payments/`.
Obuna tugagan bo'lsa **o'qish ishlaydi, yozish 403** (`message`: "Obunangiz muddati tugagan..."). Shu xabar bilan blok ekranini ko'rsating.

**Admin** (`/admin/...`, `is_staff`): `overview/?days=30` (umumiy sonlar + davr statistikasi, kunlik grafik, tugayotgan obunalar),
`users/`, `applications/` (+ `approve/`, `reject/`), `businesses/` (+ `create/`, `{id}/`, `toggle-block/`),
`subscriptions/` (+ `activate/`, `expire/`), `subscription-requests/` (+ `approve/`, `reject/`), `subscription-plans/`, `payments/`,
`feedback/`, `banners/`, `news/`, `settings/`, `reservations/`.

## 4. Bildirishnomalar

`GET /notifications/?is_read=false&kind=` (javobda `unread` ham bor), `GET /notifications/unread-count/`,
`PATCH /notifications/{id}/read/`, `POST /notifications/read-all/`, `DELETE /notifications/{id}/`.

`kind`: `reservation | application | subscription | review | system`; `level`: `info | success | warning`.
`link_url` — frontend marshrutiga moslashtiriladigan mantiqiy manzil:

| `link_url` | Kimga | Ochiladigan ekran |
|---|---|---|
| `/reservations` | mijoz | Bronlarim |
| `/business/apply` | mijoz | Biznes ochish / ariza holati |
| `/owner` | egasi | Panel bosh sahifasi |
| `/owner/reservations` | egasi | Bronlar |
| `/owner/reviews` | egasi | Sharhlar |
| `/owner/subscription` | egasi | Obuna |
| `/admin/applications` | admin | Arizalar |
| `/admin/subscription-requests` | admin | Obuna so'rovlari |
| `/admin/feedback` | admin | Takliflar |

**Push.** Ilova FCM tokenini `POST /notifications/devices/ {"token","platform":"android|ios|web"}` bilan ro'yxatdan o'tkazadi
(qayta yuborish — yangilash), chiqishda `DELETE /notifications/devices/{token}/`. Push `data` da `kind`, `link_url`, `id` keladi.

## 5. Taklif va sozlamalar

`POST /feedback/ {"kind":"idea|problem|other","message","contact"?,"page"?}` — kirish shart emas.
`GET /settings/` → `admin_telegram`, `support_phone`, `trial_days`, `deposits`, `plans[]` — "Biznes ochish" ekrani shu bilan to'ladi.
`GET /health/` → `{"status","checks":{"database","cache","celery"},"version"}`.
