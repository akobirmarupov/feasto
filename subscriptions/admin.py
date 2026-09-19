from django.contrib import admin, messages
from unfold.admin import ModelAdmin, TabularInline

from .models import PaymentLog, Subscription, SubscriptionPlan, SubscriptionRequest


class PaymentLogInline(TabularInline):
    model = PaymentLog
    extra = 0
    fields = ("amount", "confirmed_by", "note", "created_at")
    readonly_fields = ("created_at",)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(ModelAdmin):
    list_display = ("business_type", "duration_months", "price", "price_per_month")
    list_filter = ("business_type",)
    list_filter_submit = True
    search_fields = ("business_type",)

    @admin.display(description="Oyiga")
    def price_per_month(self, obj):
        return f"{obj.price_per_month:,.0f}".replace(",", " ")


@admin.register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    list_display = ("business", "plan", "status", "trial_ends_at", "subscription_ends_at", "approved_by")
    list_filter = ("status", "plan")
    list_filter_submit = True
    list_select_related = ("business", "plan", "approved_by")
    search_fields = ("business__name",)
    autocomplete_fields = ("business", "plan", "approved_by")
    inlines = [PaymentLogInline]

    actions = ["mark_active", "mark_expired"]

    # Ikkala amal ham servis orqali: muddat, to'lov jurnali va biznesning
    # qidiruvda ko'rinishi birga yangilanadi.
    @admin.action(description="To'lovni tasdiqlash — obunani tarif muddatiga uzaytirish")
    def mark_active(self, request, queryset):
        from subscriptions.services import activate_subscription

        count = 0
        for subscription in queryset.select_related("business", "plan"):
            activate_subscription(
                business=subscription.business,
                approved_by=request.user,
                note="Admin panelidan qo'lda tasdiqlandi",
            )
            count += 1
        self.message_user(request, f"{count} ta obuna uzaytirildi (to'lov jurnaliga yozildi).")

    @admin.action(description="Muddati tugagan deb belgilash (joy qidiruvdan yashiriladi)")
    def mark_expired(self, request, queryset):
        from subscriptions.services import expire_subscription

        count = 0
        for subscription in queryset.exclude(status="expired").select_related("business"):
            expire_subscription(subscription=subscription)
            count += 1
        self.message_user(request, f"{count} ta obunaning muddati tugadi.")


@admin.register(PaymentLog)
class PaymentLogAdmin(ModelAdmin):
    list_display = ("subscription", "amount", "confirmed_by", "created_at")
    list_select_related = ("subscription__business", "confirmed_by")
    search_fields = ("subscription__business__name", "note")
    autocomplete_fields = ("subscription", "confirmed_by")


@admin.register(SubscriptionRequest)
class SubscriptionRequestAdmin(ModelAdmin):
    list_display = ("business", "plan", "price", "status", "created_at", "reviewed_by", "reviewed_at")
    list_filter = ("status", "plan__business_type")
    list_filter_submit = True
    list_select_related = ("business", "plan", "reviewed_by")
    search_fields = ("business__name", "business__owner__username", "note", "admin_note")
    autocomplete_fields = ("business", "plan")
    readonly_fields = ("status", "reviewed_at", "reviewed_by")

    fieldsets = (
        ("Ariza", {"fields": ("business", "plan", "price", "note")}),
        ("Ko'rib chiqish", {
            "fields": ("status", "admin_note", "reviewed_by", "reviewed_at"),
            "description": "Holat faqat ro'yxatdagi \"Tasdiqlash\" / \"Rad etish\" amallari orqali o'zgaradi.",
        }),
    )

    actions = ["approve", "reject"]

    @admin.action(description="Tasdiqlash — to'lov qabul qilindi, obuna uzaytiriladi")
    def approve(self, request, queryset):
        from subscriptions.services import approve_renewal

        count = 0
        for subscription_request in queryset.filter(status=SubscriptionRequest.STATUS_PENDING).select_related("business", "plan"):
            try:
                approve_renewal(
                    request=subscription_request, approved_by=request.user,
                    note=subscription_request.admin_note,
                )
            except ValueError as error:
                self.message_user(request, str(error), level=messages.ERROR)
                continue
            count += 1
        self.message_user(request, f"{count} ta ariza tasdiqlandi.")

    @admin.action(description="Rad etish")
    def reject(self, request, queryset):
        from subscriptions.services import reject_renewal

        count = 0
        for subscription_request in queryset.filter(status=SubscriptionRequest.STATUS_PENDING).select_related("business"):
            try:
                reject_renewal(
                    request=subscription_request, rejected_by=request.user,
                    note=subscription_request.admin_note,
                )
            except ValueError as error:
                self.message_user(request, str(error), level=messages.ERROR)
                continue
            count += 1
        self.message_user(request, f"{count} ta ariza rad etildi.")
