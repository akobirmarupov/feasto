# Serverga chiqarish (systemd)

Docker/Nginx/CI konfiguratsiyasi bu yerga kirmaydi — ular alohida yoziladi.
Bu hujjat gunicorn + Celery worker + Celery beat + zaxira nusxani `systemd` bilan ko'tarishni tushuntiradi.

## 1. Tayyorgarlik

```bash
sudo useradd -r -m -d /srv/feasto -s /bin/bash feasto
sudo -u feasto git clone <repo> /srv/feasto
cd /srv/feasto
sudo -u feasto python3 -m venv venv
sudo -u feasto venv/bin/pip install -r requirements.txt
sudo -u feasto cp .env.example .env      # qiymatlarni to'ldiring (DEBUG=False!)
```

Majburiy `.env` qiymatlari: `SECRET_KEY` (uzun, tasodifiy), `ALLOWED_HOSTS`, `DB_*`, `REDIS_URL`,
`CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS`, `FRONTEND_LOGIN_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.

```bash
sudo -u feasto venv/bin/python manage.py check --deploy
sudo -u feasto venv/bin/python manage.py migrate
sudo -u feasto venv/bin/python manage.py seed_platform
sudo -u feasto venv/bin/python manage.py createsuperuser
sudo -u feasto venv/bin/python manage.py collectstatic --noinput
```

## 2. Xizmatlar

```bash
sudo cp deploy/feasto.service deploy/feasto-worker.service deploy/feasto-beat.service \
        deploy/feasto-backup.service deploy/feasto-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now feasto feasto-worker feasto-beat feasto-backup.timer
sudo systemctl status feasto feasto-worker feasto-beat
```

- `feasto` — API, `0.0.0.0:8000` (`GUNICORN_BIND` bilan o'zgartiriladi). Statik fayllar WhiteNoise orqali.
- `feasto-worker` — Celery vazifalari (bildirishnoma, Telegram, push).
- `feasto-beat` — jadval: obuna tekshiruvi 03:00, eslatma 09:00, bronlarni yakunlash har 15 daqiqa, heartbeat har daqiqa.
  **Faqat bitta nusxa** ishlashi shart.
- `feasto-backup.timer` — har kuni 04:00 da `deploy/backup.sh` (Postgres dump + media arxivi, 14 kun saqlanadi).

## 3. Media va statik

- Statik: `collectstatic` → `staticfiles/`, WhiteNoise beradi, reverse-proxy shart emas.
- Media (`/media/`): productionda `DEBUG=False` bo'lgani uchun Django bermaydi. Ikki yo'l:
  1. reverse-proxy `/media/` ni `/srv/feasto/media/` dan bersin (Nginx konfiguratsiyasi alohida);
  2. `USE_S3=True` + `AWS_*` — fayllar S3/MinIO ga yoziladi (`django-storages` o'rnatilgan).

## 4. Yangilash

```bash
cd /srv/feasto && sudo -u feasto git pull
sudo -u feasto venv/bin/pip install -r requirements.txt
sudo -u feasto venv/bin/python manage.py migrate
sudo -u feasto venv/bin/python manage.py collectstatic --noinput
sudo systemctl restart feasto feasto-worker feasto-beat
```

## 5. Kuzatuv

- `GET /api/health/` — `database`, `cache`, `celery` (beat heartbeat 5 daqiqa ichida bo'lsa `ok`).
- `SENTRY_DSN` berilsa xatolar Sentry'ga ketadi (Django + Celery).
- Loglar: `logs/feasto.log`, `logs/security.log` (aylanadigan), gunicorn `journalctl -u feasto`.
- Zaxira: `/var/backups/feasto`. Tiklash: `pg_restore -d feasto_db db-XXXX.dump`.
