from django.contrib import admin
from unfold.admin import ModelAdmin

from notifications.models import Device, Notification


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ("title", "user", "kind", "level", "is_read", "created_at")
    list_filter = ("kind", "level", "is_read")
    search_fields = ("title", "body", "user__username", "user__phone_number")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "read_at")
    list_per_page = 50


@admin.register(Device)
class DeviceAdmin(ModelAdmin):
    list_display = ("user", "platform", "is_active", "last_seen_at", "created_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__username", "user__phone_number", "token")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at", "last_seen_at")
