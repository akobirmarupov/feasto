from django.contrib import admin, messages
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action

from .models import Business, BusinessApplication, BusinessPhoto, Hall, Room, VenuePricing


class FilterByBusinessMixin:

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        business_id = request.GET.get("business_id")
        if business_id:
            try:
                queryset = queryset.filter(business_id=business_id)
            except ValidationError:
                queryset = queryset.none()
        return queryset, may_have_duplicates


class RoomInline(TabularInline):
    model = Room
    extra = 1
    fields = ("name", "room_type", "capacity", "deposit_tier", "deposit_price")


class BusinessPhotoInline(TabularInline):
    model = BusinessPhoto
    extra = 1
    fields = ("image", "order")


class VenuePricingInline(TabularInline):
    model = VenuePricing
    extra = 0
    fields = ("dish_count", "price_per_person")


class HallInline(TabularInline):
    model = Hall
    extra = 1
    fields = ("name", "people", "all_price", "deposit_price")


@admin.register(Business)
class BusinessAdmin(ModelAdmin):
    list_display = (
        "name", "business_type", "district", "owner", "is_visible",
        "telegram_username", "rating_avg", "reviews_count",
    )
    list_filter = ("business_type", "is_visible", "district", "cuisine")
    search_fields = ("name", "address", "district", "owner__username", "owner__full_name")
    readonly_fields = ("rating_avg", "reviews_count", "map_preview")
    list_select_related = ("owner",)

    @admin.display(description="Xaritada")
    def map_preview(self, obj):
        from django.utils.html import format_html

        links = obj.map_links
        if not links:
            return "— (koordinata ham, manzil ham kiritilmagan)"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">Google Maps</a> · '
            '<a href="{}" target="_blank" rel="noopener">Yandex</a>',
            links.get("google", ""), links.get("yandex", ""),
        )

    def get_inlines(self, request, obj=None):
        if obj is None:
            return []
        if obj.business_type == Business.TYPE_RESTAURANT:
            return [BusinessPhotoInline, RoomInline]
        if obj.business_type == Business.TYPE_VENUE:
            return [BusinessPhotoInline, HallInline, VenuePricingInline]
        return []


@admin.register(Room)
class RoomAdmin(FilterByBusinessMixin, ModelAdmin):
    list_display = ("business", "name", "room_type", "capacity", "deposit_tier", "deposit_price")
    list_select_related = ("business",)
    autocomplete_fields = ("business",)
    list_filter = ("room_type", "deposit_tier", "business__business_type")
    search_fields = ("name", "business__name")
    actions_detail = ["quick_add_room"]

    @action(description=_("Yangi xona qo'shish"), url_path="quick-add-room")
    def quick_add_room(self, request, object_id):
        from django.shortcuts import redirect
        return redirect(reverse("admin:businesses_room_add"))


@admin.register(Hall)
class HallAdmin(FilterByBusinessMixin, ModelAdmin):
    list_display = ("business", "name", "people", "package", "all_price", "deposit_price")
    list_select_related = ("business",)
    autocomplete_fields = ("business",)
    list_filter = ("business__business_type",)
    search_fields = ("name", "business__name")
    actions_detail = ["quick_add_hall"]

    @action(description=_("Yangi zal qo'shish"), url_path="quick-add-hall")
    def quick_add_hall(self, request, object_id):
        from django.shortcuts import redirect
        return redirect(reverse("admin:businesses_hall_add"))


@admin.register(BusinessApplication)
class BusinessApplicationAdmin(ModelAdmin):
    list_display = ("business_name", "business_type", "applicant", "status", "created_at", "approved_at")
    list_filter = ("business_type", "status")
    search_fields = ("business_name", "applicant__username", "applicant__phone_number")
    readonly_fields = ("approved_at", "approved_by")
    list_select_related = ("applicant", "plan")
    autocomplete_fields = ("applicant",)

    actions = ["approve_payment", "reject"]

    def save_model(self, request, obj, form, change):
        new_status = obj.status
        previous_status = (
            BusinessApplication.objects.filter(pk=obj.pk)
            .values_list("status", flat=True)
            .first()
            if change else None
        )
        status_changed = change and new_status != previous_status

        if status_changed:
            obj.status = previous_status
        super().save_model(request, obj, form, change)

        if status_changed:
            self._change_status(request, obj, new_status)

    def _change_status(self, request, application, new_status):
        from businesses.services import (
            TrialNotAvailable,
            approve_application,
            reject_application,
        )

        try:
            if new_status == BusinessApplication.STATUS_APPROVED:
                approve_application(application=application, approved_by=request.user)
                self.message_user(request, f"«{application.business_name}» tasdiqlandi: obuna ochildi, joy ommaga chiqdi.")
            elif new_status == BusinessApplication.STATUS_REJECTED:
                reject_application(application=application, rejected_by=request.user)
                self.message_user(request, f"«{application.business_name}» rad etildi: joy qidiruvdan yashirildi.")
            else:
                self.message_user(
                    request,
                    "Arizani qayta \"to'lov kutilmoqda\" holatiga qaytarib bo'lmaydi.",
                    level=messages.WARNING,
                )
            return True
        except TrialNotAvailable as error:
            self.message_user(request, f"«{application.business_name}»: {error}", level=messages.ERROR)
            return False

    @admin.action(description=_("To'lovni tasdiqlash (obuna faollashadi)"))
    def approve_payment(self, request, queryset):
        count = 0
        for application in queryset.exclude(status=BusinessApplication.STATUS_APPROVED).select_related("applicant", "plan"):
            if self._change_status(request, application, BusinessApplication.STATUS_APPROVED):
                count += 1
        self.message_user(request, f"Jami {count} ta ariza tasdiqlandi.")

    @admin.action(description=_("Arizani rad etish"))
    def reject(self, request, queryset):
        count = 0
        for application in queryset.exclude(status=BusinessApplication.STATUS_REJECTED).select_related("applicant"):
            if self._change_status(request, application, BusinessApplication.STATUS_REJECTED):
                count += 1
        self.message_user(request, f"Jami {count} ta ariza rad etildi.")


@admin.register(VenuePricing)
class VenuePricingAdmin(ModelAdmin):
    list_display = ("business", "dish_count", "price_per_person")
    list_select_related = ("business",)
    list_filter = ("dish_count",)
    search_fields = ("business__name",)
    autocomplete_fields = ("business",)


@admin.register(BusinessPhoto)
class BusinessPhotoAdmin(ModelAdmin):
    list_display = ("business", "order", "created_at")
    list_select_related = ("business",)
    search_fields = ("business__name",)
    autocomplete_fields = ("business",)
