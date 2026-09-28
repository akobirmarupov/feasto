# Ishga tushirish
docker compose up -d

# To'xtatish (ma'lumotlar saqlanadi)
docker compose stop

# Qayta ishga tushirish
docker compose start

# Superuser yaratish (interaktiv: username, email, parol so'raydi)
docker compose exec web python manage.py createsuperuser

# Kodni o'zgartirgach qayta build qilish kerak bo'lsa (masalan requirements.txt o'zgarsa)
docker compose up -d --build

# Loglarni ko'rish
docker compose logs -f web

# Konteyner ichiga kirish (masalan migratsiya, shell uchun)
docker compose exec web python manage.py shell
docker compose exec web python manage.py migrate

git pull                          # (agar git bilan ishlasangiz)
docker compose up -d --build      # qayta build va ishga tushirish
docker compose exec web python manage.py migrate
docker compose logs -f web        # xato yo'qligini tekshirish

# Faqat bitta servisni qayta build qilish
docker compose build web
docker compose up -d web

# Faqat web konteyneri logini real vaqtda kuzatish
docker compose logs -f web

# Barcha servislarning logini ko'rish
docker compose logs -f

# Celery worker logini ko'rish
docker compose logs -f celery

# Celery beat (davriy vazifalar) logini ko'rish
docker compose logs -f celery-beat

# Oxirgi 100 qatorni ko'rish (kuzatmasdan)
docker compose logs --tail=100 web

# Bazani .sql faylga eksport qilish
docker compose exec db pg_dump -U postgres feasto_db > backup_$(date +%Y%m%d).sql

# Zaxiradan bazani tiklash
docker compose exec -T db psql -U postgres feasto_db < backup_20260927.sql