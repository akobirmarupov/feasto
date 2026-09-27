# Feasto — Docker bilan ishga tushirish

Ikki xil rejim bor:

| Rejim | Buyruq | Nima farqi |
|---|---|---|
| **Dev** (mahalliy) | `docker compose up -d` | `docker-compose.override.yml` avtomatik qo'shiladi: kod host'dan bog'lanadi, 8000 va 5433 portlari ochiladi, loglar `logs/` da |
| **Prod** (server) | `docker compose -f docker-compose.yml up -d` | bind mount yo'q (image ichidagi kod), portlar ochilmaydi — tashqariga faqat nginx chiqaradi |

**Serverda `-f docker-compose.yml` ni yozishni unutmang**, aks holda dev sozlamalari ishlatiladi.

Servislar: `web` (gunicorn) · `db` (Postgres 16) · `redis` · `celery` (worker) · `celery-beat` (jadval).

---

## 1. Birinchi ishga tushirish

```bash
cp .env.example .env            # qiymatlarni to'ldiring
```

`.env` da Docker uchun majburiy qiymatlar:

```
DB_HOST=db
DB_PORT=5432
REDIS_URL=redis://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/2
CELERY_RESULT_BACKEND=redis://redis:6379/3
ALLOWED_HOSTS=<domen>,127.0.0.1      # 127.0.0.1 HEALTHCHECK uchun shart
```

```bash
docker compose up -d --build

docker compose exec web python manage.py seed_platform      # sozlamalar + tarif rejalari
docker compose exec web python manage.py createsuperuser    # admin panel uchun
```

Migratsiya va `collectstatic` **avtomatik** bajariladi — `web` konteyneri
har ko'tarilganda `entrypoint.sh` ularni o'zi qiladi (`RUN_MIGRATIONS=1`).
`celery` va `celery-beat` da bu o'chirilgan, shuning uchun uchta konteyner
bir vaqtda migrate qilib to'qnashmaydi.

## 2. Hammasi ishlayotganini tekshirish

```bash
docker compose ps                        # web "healthy" bo'lishi kerak
curl -s localhost:8000/api/health/       # {"database":"ok","cache":"ok","celery":"ok"}
docker compose logs celery | tail -20    # "celery@... ready." ko'rinishi kerak
docker compose logs celery-beat | tail   # "beat: Starting..."
```

`celery: "ok"` heartbeat orqali aniqlanadi — beat bir daqiqa ishlagach paydo bo'ladi.
Agar `celery: "unknown"` bo'lsa, `celery-beat` ko'tarilmagan.

## 2.1. Eski volume'lar bo'lsa — bir martalik `chown`

Konteyner endi root ostida ishlamaydi (UID 1000). Agar volume'lar ILGARI root
ostidagi image tomonidan yaratilgan bo'lsa, `collectstatic` va media yuklash
"Permission denied" beradi. Bir marta egalikni to'g'rilash kerak:

```bash
docker compose down
docker run --rm -v feasto_static_volume:/s -v feasto_media_volume:/m \
  alpine chown -R 1000:1000 /s /m
docker compose up -d
```

Yangi o'rnatishda bu shart emas — volume egaligi image'dan to'g'ri olinadi.

## 3. Kundalik buyruqlar

```bash
docker compose stop                 # to'xtatish (ma'lumot saqlanadi)
docker compose start                # qayta ishga tushirish
docker compose up -d --build        # kod yoki requirements.txt o'zgargach
docker compose down                 # konteynerlarni o'chirish (volume'lar qoladi)

docker compose logs -f web          # web logi
docker compose logs -f celery       # worker logi
docker compose logs --tail=100 web  # oxirgi 100 qator

docker compose exec web python manage.py shell
docker compose exec web python manage.py migrate
docker compose exec web python manage.py test        # testlar
```

## 4. Zaxira va tiklash

```bash
docker compose exec db pg_dump -U postgres feasto_db > backup_$(date +%Y%m%d).sql
docker compose exec -T db psql -U postgres feasto_db < backup_20260927.sql
```

Media fayllar `media_volume` da:

```bash
docker run --rm -v feasto_media_volume:/m -v $(pwd):/out alpine \
  tar czf /out/media_$(date +%Y%m%d).tar.gz -C /m .
```

## 5. Serverga yangilash

```bash
git pull
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml logs -f web
```

Migratsiya o'z-o'zidan bajariladi, alohida buyruq shart emas.

---

## 6. Nginx yozadigan odamga kerak bo'ladigan ma'lumot

- Backend konteyner ichida **`http://web:8000`** manzilida (compose tarmog'ida
  xost nomi = servis nomi). `web` tashqariga port chiqarmaydi, faqat `expose`.
- Statik va media fayllar volume'larda:
  - `/static/` → `static_volume:/app/staticfiles:ro`
  - `/media/` → `media_volume:/app/media:ro`
  - `DEBUG=False` da Django media bermaydi — **nginx bermasa barcha rasm URL'lari 404**.
  - Statik fayllarni WhiteNoise ham beradi, lekin nginx orqali berish tezroq.
- Sarlavhalar: `Host`, `X-Real-IP`, `X-Forwarded-For` va **`X-Forwarded-Proto $scheme`**.
  Oxirgisi bo'lmasa `SECURE_SSL_REDIRECT=True` bilan cheksiz redirect bo'ladi.
- `client_max_body_size 10m;` — rasm chegarasi 5 MB, `DATA_UPLOAD_MAX_MEMORY_SIZE` 10 MB.
- `X-Request-ID` ni o'tkazib yuborsangiz loglarda so'rovni oxirigacha kuzatish mumkin.
- `/api/health/` — autentifikatsiyasiz, upstream tekshiruvi uchun tayyor.
- 80/443 portlarini faqat nginx chiqaradi. `docker-compose.yml` ichida nginx
  qo'shiladigan joy izoh bilan belgilangan.

## 7. CI/CD yozadigan odamga kerak bo'ladigan ma'lumot

**Testlar.** `python manage.py test` — 733 test, ~3 daqiqa (parallel: `--parallel 4`).
Kerak bo'ladigani: **Postgres**. Redis kerak emas — test paytida kesh LocMem'ga
o'tadi (`settings.py` dagi `if "test" in sys.argv`). Shu sababli `pytest` ga
o'tsangiz bu shart ishlamaydi va Redis talab qilinadi.

Test uchun minimal env: `SECRET_KEY`, `DEBUG=True`, `DB_*`.
Telegram/FCM/Google secretlari **kerak emas** — testlar ularni o'zi o'chirib qo'yadi.

**Sifat tekshiruvlari** (testdan oldin ishlatish tavsiya etiladi):

```bash
python manage.py check                          # 0 muammo bo'lishi kerak
python manage.py makemigrations --check --dry-run   # "No changes detected"
python manage.py spectacular --file /dev/null   # OpenAPI sxemasi — 0 warning
DEBUG=False python manage.py check --deploy     # prod sozlamalari
```

**Build.** `docker build -t <registry>/feasto:<sha> .`
Image root ostida ishlamaydi (UID 1000), `HEALTHCHECK` ichida bor.

**Deploy.** Serverda:

```bash
docker compose -f docker-compose.yml pull        # (registry ishlatilsa)
docker compose -f docker-compose.yml up -d
```

Migratsiya `web` entrypoint'ida avtomatik. Deploy'dan keyin tekshiruv:
`curl -f https://<domen>/api/health/`.

**Eslatmalar:**
- `celery-beat` **faqat bitta nusxada** ishlashi shart (`replicas: 1`) — aks holda
  davriy vazifalar ikki marta bajariladi.
- `GUNICORN_WORKERS` env bilan worker sonini cheklang: konteyner ichida gunicorn
  host'ning barcha CPU'sini ko'radi va kichik serverda ortiqcha worker ochadi.
- `.env` git'ga tushmaydi — CI secretlaridan yasang yoki serverda qo'lda qo'ying.
