from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = (
        "username", "full_name", "phone_number",
        "role", "trust_bits", "cancelled_reservations_count",
        "is_phone_verified", "is_confirmed", "is_active", "date_joined",
    )
    list_filter = ("role", "is_phone_verified", "is_confirmed", "is_active", "is_staff")
    search_fields = ("username", "full_name", "phone_number", "email")
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_login", "google_sub")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Shaxsiy ma'lumotlar"), {"fields": ("full_name", "email", "phone_number", "avatar", "bio", "birth_date", "preferred_language")}),
        (_("Rol va tasdiqlanganlik"), {"fields": ("role", "is_phone_verified", "is_confirmed", "has_used_trial", "google_sub")}),
        (_("Ishonchlilik"), {
            "fields": ("trust_bits", "cancelled_reservations_count"),
            "description": "1–100 Bit. Har bir bekor qilingan bronda 5 Bit ayiriladi.",
        }),
        (_("Ruxsatlar"), {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("Muhim sanalar"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "full_name", "phone_number", "role", "password1", "password2"),
        }),
    )

    actions = ["mark_as_confirmed", "mark_phone_verified"]

    @admin.action(description="Tanlanganlarni tasdiqlash (is_confirmed = True)")
    def mark_as_confirmed(self, request, queryset):
        updated = queryset.update(is_confirmed=True)
        self.message_user(request, f"{updated} ta foydalanuvchi tasdiqlandi.")

    @admin.action(description="Telefonini tasdiqlangan deb belgilash (is_phone_verified = True)")
    def mark_phone_verified(self, request, queryset):
        updated = queryset.update(is_phone_verified=True)
        self.message_user(request, f"{updated} ta foydalanuvchining telefoni tasdiqlandi.")
