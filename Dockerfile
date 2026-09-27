# 1) Asosiy (base) image — yengil Python 3.12 versiyasi
FROM python:3.12-slim

# 2) Python konteyner ichida qanday ishlashini sozlash
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 3) Ishchi papka — keyingi barcha buyruqlar shu yerda bajariladi
WORKDIR /app

# 4) Tizim kutubxonalari. psycopg2-BINARY ishlatilgani uchun kompilyator ham,
#    libpq-dev ham kerak emas (binary g'ildirak libpq ni o'zi olib keladi).
#    curl faqat HEALTHCHECK uchun.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 5) Avval faqat requirements.txt ni nusxalaymiz (cache uchun muhim!)
COPY requirements.txt .

# 6) Python kutubxonalarni o'rnatish
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 7) Loyihaning qolgan barcha fayllarini nusxalash
COPY . .

# 8) Root ostida ishlamaymiz. UID 1000 — host foydalanuvchisi bilan bir xil,
#    shuning uchun dev'da bind mount qilingan fayllarga yozish ishlaydi.
#    Papkalar image ichida OLDIN yaratiladi: named volume birinchi marta
#    ulanganda egalikni shu papkadan oladi, aks holda root'ga tegishli bo'lib
#    qoladi va collectstatic/log yozish ishlamaydi.
RUN mkdir -p /app/staticfiles /app/media /app/logs && \
    chmod +x entrypoint.sh && \
    groupadd -g 1000 appuser && \
    useradd -u 1000 -g 1000 -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# 9) Qaysi portda ishlashini hujjatlash (informatsion, real ochmaydi)
EXPOSE 8000

# 10) Konteyner sog'lig'i — /api/health/ baza va keshni tekshiradi.
#     DIQQAT: DEBUG=False bo'lsa ALLOWED_HOSTS ichida 127.0.0.1 bo'lishi shart,
#     aks holda Django 400 qaytaradi va konteyner "unhealthy" bo'lib qoladi.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health/ > /dev/null || exit 1

# 11) entrypoint tayyorgarlikni qiladi, keyin CMD (yoki compose dagi command) ni
#     ishga tushiradi. Shu ajratma bo'lmasa celery konteynerlari ham gunicorn
#     ishga tushirib yuboradi.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
