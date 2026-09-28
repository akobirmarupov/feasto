#!/bin/bash
set -e

# Bazani kutamiz. showmigrations DB ga ulanadi — ulanmaguncha qayta uriniladi.
# Cheksiz kutib qolmaslik uchun timeout bor (standart 60 sekund).
DB_WAIT_TIMEOUT="${DB_WAIT_TIMEOUT:-60}"
deadline=$((SECONDS + DB_WAIT_TIMEOUT))

echo "Ma'lumotlar bazasi tayyor bo'lishini kutyapmiz..."
until last_error=$(python manage.py showmigrations 2>&1 > /dev/null); do
  # Sozlama xatosi (masalan SECRET_KEY, ALLOWED_HOSTS) kutish bilan tuzalmaydi —
  # darhol to'xtaymiz va sababini ko'rsatamiz.
  if echo "$last_error" | grep -q "ImproperlyConfigured"; then
    echo "XATO: sozlamalarda muammo bor, baza bilan bog'liq emas:" >&2
    echo "$last_error" | tail -n 3 >&2
    exit 1
  fi
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "XATO: baza ${DB_WAIT_TIMEOUT} sekund ichida javob bermadi (DB_HOST=${DB_HOST:-?})." >&2
    echo "$last_error" | tail -n 3 >&2
    exit 1
  fi
  sleep 1
done
echo "Baza tayyor."

# Migratsiya va static faqat BITTA konteynerda bajarilishi kerak — aks holda
# web/celery/celery-beat uchtasi bir vaqtda migrate qilib to'qnashadi.
# compose da faqat `web` ga RUN_MIGRATIONS=1 berilgan.
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Migratsiyalarni bajarish..."
  python manage.py migrate --noinput

  echo "Static fayllarni yig'ish..."
  python manage.py collectstatic --noinput
fi

# Konteynerga berilgan buyruqni ishga tushiramiz (Dockerfile dagi CMD yoki
# compose dagi `command`). Shu qator bo'lmasa celery konteynerlari ham
# gunicorn ishga tushirib yuboradi.
echo "Ishga tushirilmoqda: $*"
exec "$@"
