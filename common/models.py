import uuid
from contextvars import ContextVar
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.validators import MinValueValidator
from django.db import models

_solo_memo: ContextVar = ContextVar("platform_settings_memo", default=None)


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class Role(models.TextChoices):
    USER = "user", "Oddiy foydalanuvchi"
    BUSINESS = "business", "Biznes admin"
    ADMIN = "admin", "Platforma admini"


class PlatformSettings(models.Model):

    CACHE_KEY = "platform_settings"
    CACHE_TTL = 300

    admin_telegram_username = models.CharField(
        max_length=32, default="akobir_marupov",
        help_text="@ belgisiz kiriting. Business ariza/to'lov oqimida foydalanuvchiga shu ko'rsatiladi.",
    )
    support_phone = models.CharField(max_length=20, blank=True, default="+998771210418")
    room_deposit_premium = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal(99000),
        validators=[MinValueValidator(Decimal(0))],
        help_text="Restoran Premium xonasi uchun oldindan to'lov.",
    )
    room_deposit_pro = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal(49000),
        validators=[MinValueValidator(Decimal(0))],
        help_text="Restoran Pro xonasi uchun oldindan to'lov.",
    )
    venue_deposit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal(599000),
        validators=[MinValueValidator(Decimal(0))],
        help_text="To'yxona zalini bron qilishda oldindan to'lov.",
    )

    trial_days = models.PositiveSmallIntegerField(
        default=7, verbose_name="Bepul sinov (kun)",
        help_text="Yangi biznesga beriladigan bepul sinov muddati.",
    )
    subscription_days = models.PositiveSmallIntegerField(
        default=30, verbose_name="Obuna muddati (kun)",
        help_text="Standart obuna muddati. Aniq muddat tarif rejasidan olinadi.",
    )

    class Meta:
        verbose_name = "Platforma sozlamalari"
        verbose_name_plural = "Platforma sozlamalari"

    def __str__(self):
        return "Platforma sozlamalari"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)
        _solo_memo.set(None)

    @classmethod
    def get_solo(cls) -> "PlatformSettings":
        memo = _solo_memo.get()
        if memo is not None:
            return memo

        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, cls.CACHE_TTL)

        _solo_memo.set(obj)
        return obj


class Feedback(BaseModel):

    KIND_IDEA = "idea"
    KIND_PROBLEM = "problem"
    KIND_OTHER = "other"
    KIND_CHOICES = (
        (KIND_IDEA, "Taklif"),
        (KIND_PROBLEM, "Muammo"),
        (KIND_OTHER, "Boshqa"),
    )

    STATUS_NEW = "new"
    STATUS_SEEN = "seen"
    STATUS_DONE = "done"
    STATUS_CHOICES = (
        (STATUS_NEW, "Yangi"),
        (STATUS_SEEN, "O'qilgan"),
        (STATUS_DONE, "Hal qilingan"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="feedbacks",
        verbose_name="Kim yozgan",
        help_text="Bo'sh — kirmagan foydalanuvchi yozgan.",
    )
    kind = models.CharField(
        max_length=10, choices=KIND_CHOICES, default=KIND_IDEA, db_index=True,
        verbose_name="Turi",
    )
    message = models.TextField(max_length=1000, verbose_name="Matn")
    page = models.CharField(
        max_length=200, blank=True, verbose_name="Qaysi sahifadan",
    )
    contact = models.CharField(
        max_length=120, blank=True, verbose_name="Aloqa (ixtiyoriy)",
        help_text="Telefon yoki Telegram — javob kerak bo'lsa.",
    )

    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_NEW, db_index=True,
        verbose_name="Holati",
    )
    admin_note = models.TextField(
        blank=True, verbose_name="Administrator izohi",
        help_text="Faqat ichki foydalanish uchun — foydalanuvchiga ko'rinmaydi.",
    )

    class Meta:
        verbose_name = "Taklif"
        verbose_name_plural = "Takliflar"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="idx_feedback_status_created"),
        ]

    def __str__(self):
        author = self.user.username if self.user_id else "mehmon"
        return f"{self.get_kind_display()} — {author}"

    @property
    def short(self) -> str:
        text = self.message.strip().replace("\n", " ")
        return text if len(text) <= 80 else text[:77] + "…"
