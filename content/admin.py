from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Banner, News


@admin.register(Banner)
class BannerAdmin(ModelAdmin):
    list_display = ("title_uz", "placement", "media_type", "is_active", "order", "starts_at", "ends_at")
    list_filter = ("placement", "media_type", "is_active")
    search_fields = ("title_uz", "title_ru", "title_en")
    list_editable = ("is_active", "order")

    fieldsets = (
        ("Joylashuv va holat", {
            "fields": ("placement", "is_active", "order", "accent_color"),
        }),
        ("Ko'rinish muddati", {
            "fields": ("starts_at", "ends_at"),
            "description": "Bo'sh qoldirilsa — darhol va muddatsiz ko'rinadi.",
        }),
        ("Media", {
            "fields": ("media_type", "image", "video", "video_url"),
            "description": "Turini tanlang: faqat matn, rasm yoki video.",
        }),
        ("O'zbekcha", {"fields": ("title_uz", "subtitle_uz", "body_uz", "cta_label_uz")}),
        ("Русский", {"fields": ("title_ru", "subtitle_ru", "body_ru", "cta_label_ru")}),
        ("English", {"fields": ("title_en", "subtitle_en", "body_en", "cta_label_en")}),
        ("Havola", {"fields": ("cta_url",)}),
    )


@admin.register(News)
class NewsAdmin(ModelAdmin):
    list_display = ("title_uz", "category", "is_pinned", "is_active", "order", "created_at")
    list_filter = ("category", "is_active", "is_pinned")
    search_fields = ("title_uz", "title_ru", "title_en")
    list_editable = ("is_active", "is_pinned", "order")

    fieldsets = (
        ("Turkum va holat", {"fields": ("category", "is_active", "is_pinned", "order", "cover", "link_url")}),
        ("Ko'rinish muddati", {"fields": ("starts_at", "ends_at")}),
        ("O'zbekcha", {"fields": ("title_uz", "excerpt_uz", "body_uz")}),
        ("Русский", {"fields": ("title_ru", "excerpt_ru", "body_ru")}),
        ("English", {"fields": ("title_en", "excerpt_en", "body_en")}),
    )
