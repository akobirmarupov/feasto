from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from businesses.models import Business
from common.models import BaseModel


class SubscriptionPlan(BaseModel):
    business_type = models.CharField(max_length=15, choices=Business.TYPE_CHOICES)
    duration_months = models.PositiveSmallIntegerField(
        default=1, verbose_name="Muddat (oy)", validators=[MinValueValidator(1)],
        help_text="Reja necha oyga amal qiladi. 1 = oylik, 3 = choraklik.",
    )
    price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal(0))],
        verbose_name="Narx", help_text="Shu muddat uchun TO'LIQ summa.",
    )
    trial_days = models.PositiveIntegerField(default=7, verbose_name="Bepul sinov (kun)")

    class Meta:
        ordering = ["business_type", "duration_months"]
        verbose_name = "Tarif rejasi"
        verbose_name_plural = "Tarif rejalari"
        constraints = [
            models.UniqueConstraint(
                fields=["business_type", "duration_months"],
                name="uniq_plan_type_duration",
            )
        ]

    def __str__(self):
        return f"{self.get_business_type_display()} — {self.duration_months} oy — {self.price}"

    @property
    def price_per_month(self):
        return self.price / self.duration_months

    @property
    def duration_label(self):
        return f"{self.duration_months} oy"

    @property
    def days(self):
        return self.duration_months * 30


class Subscription(BaseModel):
    STATUS_CHOICES = (
        ("trial", "Trial (bepul)"),
        ("active", "Faol"),
        ("expired", "Muddati tugagan"),
    )

    business = models.OneToOneField(Business, on_delete=models.CASCADE, related_name="subscription")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="trial", db_index=True)
    trial_ends_at = models.DateTimeField()
    subscription_ends_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reminded_days = models.JSONField(default=list, blank=True, verbose_name="Yuborilgan eslatmalar")

    class Meta:
        verbose_name = "Obuna"
        verbose_name_plural = "Obunalar"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "trial_ends_at"], name="idx_sub_status_trial_end"),
            models.Index(fields=["status", "subscription_ends_at"], name="idx_sub_status_sub_end"),
        ]

    def __str__(self):
        return f"{self.business} — {self.status}"


class PaymentLog(BaseModel):
    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "To'lov"
        verbose_name_plural = "To'lov jurnali"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["subscription", "-created_at"], name="idx_payment_sub_created")]

    def __str__(self):
        return f"{self.subscription.business} — {self.amount:,.0f} so'm".replace(",", " ")


class SubscriptionRequest(BaseModel):

    STATUS_PENDING = "pending_payment"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CHOICES = (
        (STATUS_PENDING, "To'lov kutilmoqda"),
        (STATUS_APPROVED, "Tasdiqlangan"),
        (STATUS_REJECTED, "Rad etilgan"),
    )

    business = models.ForeignKey(
        Business, on_delete=models.CASCADE, related_name="subscription_requests"
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="requests",
        help_text="Egasi qaysi muddatni tanlagani.",
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True
    )
    note = models.CharField(max_length=255, blank=True, verbose_name="Egasining izohi")
    admin_note = models.CharField(max_length=255, blank=True, verbose_name="Admin izohi")

    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviewed_subscription_requests",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Obuna arizasi"
        verbose_name_plural = "Obuna arizalari"
        indexes = [
            models.Index(fields=["status", "-created_at"], name="idx_subreq_status_created"),
            models.Index(fields=["business", "-created_at"], name="idx_subreq_biz_created"),
        ]

    def __str__(self):
        return f"{self.business} — {self.plan.duration_label} ({self.get_status_display()})"
