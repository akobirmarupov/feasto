import sys
from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

from .unfold_config import UNFOLD  # noqa: F401  (Django shu nom bo'yicha o'qiydi)


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
if DEBUG:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "*"]

AUTH_USER_MODEL = "account.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Application definition
DJANGO_APPS = [
    "unfold",
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    "whitenoise.runserver_nostatic",
    'django.contrib.staticfiles',
]


EXTERNAL_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
]

LOCAL_APPS = [
    "common",
    "account",
    "businesses",
    "catalog",
    "reservations",
    "reviews",
    "subscriptions",
    "content",
    "notifications",
]

INSTALLED_APPS = DJANGO_APPS + EXTERNAL_APPS + LOCAL_APPS


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "common.middleware.RequestIDMiddleware",
]


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST"),
        "PORT": config("DB_PORT"),
        "CONN_MAX_AGE": config("DB_CONN_MAX_AGE", default=60, cast=int),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            # Bitta so'rov bazani abadiy band qilib turmasligi uchun.
            "connect_timeout": 10,
            "options": "-c statement_timeout=15000",
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Argon2 — Django tavsiya qiladigan eng kuchli hasher. bcrypt/PBKDF2 zaxira
# sifatida qoladi, shunda eski parollar birinchi kirishda avtomatik yangilanadi.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=30, cast=int)),
    # Refresh ishlatilganda yangisi beriladi va eskisi qora ro'yxatga tushadi —
    # o'g'irlangan refresh token cheksiz ishlatilmasligi uchun.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardResultsPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "common.throttles.BurstUserThrottle",
        "common.throttles.SustainedUserThrottle",
        "common.throttles.BurstAnonThrottle",
        "common.throttles.SustainedAnonThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        # Ikki qatlamli cheklov: qisqa portlash (bot/skript) alohida,
        # kunlik umumiy hajm alohida ushlanadi.
        "burst_user": "120/min",
        "sustained_user": "5000/day",
        "burst_anon": "40/min",
        "sustained_anon": "1000/day",

        # Og'ir yoki xavfli amallar uchun aniq cheklovlar.
        #
        # `login` — Google orqali kirishga ham tegishli. Cheklov o'sha
        # yerda ham kerak: Google tokeni har safar tarmoq orqali
        # tekshiriladi, ya'ni cheksiz so'rov Google kvotasini yeb
        # qo'yardi.
        #
        # `register`, `sms_send`, `sms_verify` olib tashlandi —
        # bunday endpointlar endi yo'q.
        "login": "10/min",
        "reservation_create": "10/hour",
        "business_application": "3/day",
        "review_create": "20/day",

        # Taklif — kirish talab qilinmaydi, shuning uchun cheklov qattiqroq.
        "feedback": "10/day",
    },
    # DEBUG'da brauzerdan sinash qulay, productionda faqat JSON.
    "DEFAULT_RENDERER_CLASSES": (
        ("rest_framework.renderers.JSONRenderer",)
        if not DEBUG
        else (
            "rest_framework.renderers.JSONRenderer",
            "rest_framework.renderers.BrowsableAPIRenderer",
        )
    ),
}


SPECTACULAR_SETTINGS = {
    "TITLE": "Feasto API",
    "DESCRIPTION": "Restoran va to'yxonalarni onlayn qidirish va bron qilish platformasi",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api",
    # `category` nomi ikki joyda (menyu turkumi va yangilik turkumi) uchraydi —
    # schema'da avtomatik nom to'qnashmasligi uchun aniq nom beramiz.
    # Bir xil nomli maydonlar (`status`, `category`) har xil variantlar
    # to'plamiga ega — nomlarni qo'lda ajratmasak, hujjatda "Status2c5Enum"
    # kabi o'qib bo'lmaydigan nomlar paydo bo'ladi.
    "ENUM_NAME_OVERRIDES": {
        "MenuCategoryEnum": "catalog.models.MenuCategory.choices",
        "NewsCategoryEnum": "content.models.News.CATEGORY_CHOICES",
        "ReservationStatusEnum": "reservations.models.Reservation.STATUS_CHOICES",
        "SubscriptionStatusEnum": "subscriptions.models.Subscription.STATUS_CHOICES",
        # Ariza va obuna so'rovi AYNAN bir xil holatlarga ega
        # (to'lov kutilmoqda / tasdiqlangan / rad etilgan), shuning uchun
        # ikkalasiga bitta nom beriladi — aks holda drf-spectacular
        # "bir to'plamga ikki nom" deb ogohlantiradi.
        "ApprovalStatusEnum": "businesses.models.BusinessApplication.STATUS_CHOICES",
    },
}


REDIS_URL = config("REDIS_URL", default="redis://127.0.0.1:6379/1")


CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "CONNECTION_POOL_KWARGS": {"max_connections": 100, "retry_on_timeout": True},
            "SOCKET_CONNECT_TIMEOUT": 3,
            "SOCKET_TIMEOUT": 3,
            # Redis o'chib qolsa sayt ham o'lmasin — kesh shunchaki
            # "bo'sh" bo'lib qoladi va so'rovlar bazaga tushadi.
            "IGNORE_EXCEPTIONS": True,
        },
        "KEY_PREFIX": "feasto",
    }
}
DJANGO_REDIS_IGNORE_EXCEPTIONS = True


SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_CACHE_ALIAS = "default"


if "test" in sys.argv:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "feasto-tests",
        }
    }


CACHE_TTL_BUSINESS_LIST = config("CACHE_TTL_BUSINESS_LIST", default=60, cast=int)
CACHE_TTL_BUSINESS_DETAIL = config("CACHE_TTL_BUSINESS_DETAIL", default=120, cast=int)


CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Tashkent"
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True


CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())
CORS_ALLOW_ALL_ORIGINS = DEBUG and not CORS_ALLOWED_ORIGINS
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Yuklanadigan ma'lumot hajmi — xotira bilan DoS qilishning oldini oladi.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024   # 10 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024    # 5 MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = 1000

if config("USE_PROXY_SSL_HEADER", default=not DEBUG, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

if not DEBUG:
    SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
    SECURE_HSTS_SECONDS = 31536000  # 1 yil
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    CSRF_COOKIE_SAMESITE = "Lax"


TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default="")
TELEGRAM_ADMIN_CHAT_ID = config("TELEGRAM_ADMIN_CHAT_ID", default="")


GOOGLE_CLIENT_ID = config("GOOGLE_CLIENT_ID", default="")


GOOGLE_CLIENT_SECRET = config("GOOGLE_CLIENT_SECRET", default="")


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/
LANGUAGE_CODE = "uz"
TIME_ZONE = "Asia/Tashkent"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

if DEBUG:
    WHITENOISE_MAX_AGE = 0
    WHITENOISE_AUTOREFRESH = True
else:
    WHITENOISE_MAX_AGE = 31536000


if config("USE_S3", default=False, cast=bool):
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
    AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = config("AWS_S3_ENDPOINT_URL", default=None)
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False


# Email
# https://docs.djangoproject.com/en/5.2/topics/email/#topic-email-configuration

EMAIL_BACKEND = config(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)


LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_LEVEL = config("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "common.middleware.RequestIDFilter"},
    },
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} req={request_id} — {message}",
            "style": "{",
        },
        "simple": {"format": "{levelname} {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id"],
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "feasto.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "verbose",
            "filters": ["request_id"],
            "encoding": "utf-8",
        },
        "security_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "security.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 20,
            "formatter": "verbose",
            "filters": ["request_id"],
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.security": {"handlers": ["security_file", "console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["file", "console"], "level": "ERROR", "propagate": False},
        # Loyihaning o'z ilovalari
        "account": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "businesses": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "catalog": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "reservations": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "reviews": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "subscriptions": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "common": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "content": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
        "notifications": {"handlers": ["console", "file"], "level": LOG_LEVEL, "propagate": False},
    },
}
