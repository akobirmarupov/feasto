from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Feedback, PlatformSettings


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(ModelAdmin):

    list_display = ("admin_telegram_username", "trial_days", "subscription_days", "venue_deposit")

    fieldsets = (
        ("Aloqa", {"fields": ("admin_telegram_username", "support_phone")}),
        ("Depozit narxlari", {
            "fields": ("room_deposit_premium", "room_deposit_pro", "venue_deposit"),
            "description": "Bron qilishda mijoz oldindan to'laydigan summalar.",
        }),
        ("Obuna", {"fields": ("trial_days", "subscription_days")}),
    )

    def has_add_permission(self, request):
        return not PlatformSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Feedback)
class FeedbackAdmin(ModelAdmin):
    list_display = ("created_at", "kind", "short", "author", "status")
    list_filter = ("status", "kind", "created_at")
    search_fields = ("message", "contact", "user__username", "user__full_name")
    list_select_related = ("user",)
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "user", "kind", "message", "page", "contact")

    fieldsets = (
        ("Xabar", {"fields": ("created_at", "kind", "message")}),
        ("Kim yozgan", {
            "fields": ("user", "contact", "page"),
            "description": "Foydalanuvchi bo'sh bo'lsa — kirmagan mehmon yozgan.",
        }),
        ("Ko'rib chiqish", {"fields": ("status", "admin_note")}),
    )

    actions = ["mark_seen", "mark_done"]

    @admin.display(description="Muallif")
    def author(self, obj):
        if not obj.user_id:
            return "Mehmon"
        return f"{obj.user.full_name} ({obj.user.get_role_display()})"

    @admin.action(description="O'qilgan deb belgilash")
    def mark_seen(self, request, queryset):
        count = queryset.update(status=Feedback.STATUS_SEEN)
        self.message_user(request, f"{count} ta taklif o'qilgan deb belgilandi.")

    @admin.action(description="Hal qilingan deb belgilash")
    def mark_done(self, request, queryset):
        count = queryset.update(status=Feedback.STATUS_DONE)
        self.message_user(request, f"{count} ta taklif hal qilingan deb belgilandi.")

    def has_add_permission(self, request):
        return False
