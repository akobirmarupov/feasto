from django.contrib import admin
from unfold.admin import ModelAdmin

from notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ("title", "user", "kind", "level", "is_read", "created_at")
    list_filter = ("kind", "level", "is_read")
    search_fields = ("title", "body", "user__username", "user__phone_number")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "read_at")
    list_per_page = 50
