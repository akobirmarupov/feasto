from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from common.models import BaseModel
from common.validators import validate_image_file


class Language(models.TextChoices):
    UZ = "uz", "O'zbekcha"
    RU = "ru", "Русский"
    EN = "en", "English"


class TranslatedMixin:
    def tr(self, field, lang="uz"):
        value = getattr(self, f"{field}_{lang}", "") or ""
        if value.strip():
            return value
        return getattr(self, f"{field}_uz", "") or ""


class ActiveContentQuerySet(models.QuerySet):
    def live(self):
        now = timezone.now()
        return self.filter(is_active=True).filter(
            models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now),
            models.Q(ends_at__isnull=True) | models.Q(ends_at__gte=now),
        )


class Banner(BaseModel, TranslatedMixin):

    PLACEMENT_HERO = "hero"
    PLACEMENT_INLINE = "inline"
    PLACEMENT_SIDEBAR = "sidebar"
    PLACEMENT_AUTH = "auth"
    PLACEMENT_CHOICES = (
        (PLACEMENT_HERO, "Bosh banner (asosiy)"),
        (PLACEMENT_INLINE, "Sahifa ichida"),
        (PLACEMENT_SIDEBAR, "Yon panelda"),
        (PLACEMENT_AUTH, "Kirish sahifasi (chap tomon)"),
    )

    MEDIA_NONE = "none"
    MEDIA_IMAGE = "image"
    MEDIA_VIDEO = "video"
    MEDIA_CHOICES = (
        (MEDIA_NONE, "Faqat matn"),
        (MEDIA_IMAGE, "Rasm"),
        (MEDIA_VIDEO, "Video"),
    )

    title_uz = models.CharField(max_length=200)
    title_ru = models.CharField(max_length=200, blank=True)
    title_en = models.CharField(max_length=200, blank=True)
    subtitle_uz = models.CharField(max_length=300, blank=True)
    subtitle_ru = models.CharField(max_length=300, blank=True)
    subtitle_en = models.CharField(max_length=300, blank=True)
    body_uz = models.TextField(blank=True)
    body_ru = models.TextField(blank=True)
    body_en = models.TextField(blank=True)
    cta_label_uz = models.CharField(max_length=60, blank=True, verbose_name="Tugma matni (uz)")
    cta_label_ru = models.CharField(max_length=60, blank=True, verbose_name="Tugma matni (ru)")
    cta_label_en = models.CharField(max_length=60, blank=True, verbose_name="Tugma matni (en)")
    cta_url = models.CharField(max_length=500, blank=True, verbose_name="Tugma havolasi")
    media_type = models.CharField(max_length=10, choices=MEDIA_CHOICES, default=MEDIA_NONE)
    image = models.ImageField(
        upload_to="banners/", null=True, blank=True, validators=[validate_image_file],
        help_text="Tavsiya: 1600×700 px",
    )
    video = models.FileField(
        upload_to="banners/video/", null=True, blank=True,
        help_text="MP4 fayl. Yoki tashqi havolani `video_url` ga yozing.",
    )
    video_url = models.URLField(blank=True, help_text="Tashqi video havolasi (mp4 yoki embed)")
    placement = models.CharField(max_length=10, choices=PLACEMENT_CHOICES,
                                 default=PLACEMENT_HERO, db_index=True)
    accent_color = models.CharField(
        max_length=7, blank=True, default="",
        help_text="Banner aksent rangi, masalan #C9A227. Bo'sh — standart oltin.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True, help_text="Bo'sh — darhol")
    ends_at = models.DateTimeField(null=True, blank=True, help_text="Bo'sh — muddatsiz")

    objects = ActiveContentQuerySet.as_manager()

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "Banner"
        verbose_name_plural = "Bannerlar"
        indexes = [
            models.Index(fields=["placement", "is_active", "order"], name="idx_banner_place_active"),
        ]

    def __str__(self):
        return self.title_uz

    def clean(self):
        if self.media_type == self.MEDIA_IMAGE and not self.image:
            raise ValidationError({"image": "Rasm turini tanladingiz — rasm yuklang."})
        if self.media_type == self.MEDIA_VIDEO and not (self.video or self.video_url):
            raise ValidationError({"video": "Video turini tanladingiz — fayl yuklang yoki havola kiriting."})
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError({"ends_at": "Tugash vaqti boshlanishdan keyin bo'lishi kerak."})

    @property
    def media_src(self):
        if self.media_type == self.MEDIA_IMAGE and self.image:
            return self.image.url
        if self.media_type == self.MEDIA_VIDEO:
            if self.video:
                return self.video.url
            return self.video_url or None
        return None


class News(BaseModel, TranslatedMixin):

    CATEGORY_NEWS = "news"
    CATEGORY_TIP = "tip"
    CATEGORY_EVENT = "event"
    CATEGORY_UPDATE = "update"
    CATEGORY_CHOICES = (
        (CATEGORY_NEWS, "Yangilik"),
        (CATEGORY_TIP, "Foydali maslahat"),
        (CATEGORY_EVENT, "Tadbir"),
        (CATEGORY_UPDATE, "Platforma yangilanishi"),
    )

    title_uz = models.CharField(max_length=200)
    title_ru = models.CharField(max_length=200, blank=True)
    title_en = models.CharField(max_length=200, blank=True)
    excerpt_uz = models.CharField(max_length=300, blank=True, verbose_name="Qisqacha (uz)")
    excerpt_ru = models.CharField(max_length=300, blank=True, verbose_name="Qisqacha (ru)")
    excerpt_en = models.CharField(max_length=300, blank=True, verbose_name="Qisqacha (en)")
    body_uz = models.TextField(blank=True)
    body_ru = models.TextField(blank=True)
    body_en = models.TextField(blank=True)
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES,
                                default=CATEGORY_NEWS, db_index=True)
    cover = models.ImageField(
        upload_to="news/", null=True, blank=True, validators=[validate_image_file]
    )
    link_url = models.CharField(max_length=500, blank=True,
                                help_text="Bosilganda ochiladigan sahifa (ixtiyoriy)")

    is_active = models.BooleanField(default=True, db_index=True)
    is_pinned = models.BooleanField(default=False, help_text="Lentaning boshida turadi")
    order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveContentQuerySet.as_manager()

    class Meta:
        ordering = ["-is_pinned", "order", "-created_at"]
        verbose_name = "Yangilik"
        verbose_name_plural = "Yangiliklar"
        indexes = [
            models.Index(fields=["is_active", "-is_pinned", "-created_at"], name="idx_news_active_pinned"),
        ]

    def __str__(self):
        return self.title_uz
