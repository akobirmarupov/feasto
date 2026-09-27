import math

from django.utils import timezone
from rest_framework import serializers

from subscriptions.models import PaymentLog, Subscription, SubscriptionPlan, SubscriptionRequest


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    business_type_display = serializers.CharField(source="get_business_type_display", read_only=True)
    duration_label = serializers.CharField(read_only=True)
    price_per_month = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    savings = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = [
            "id", "business_type", "business_type_display",
            "duration_months", "duration_label",
            "price", "price_per_month", "savings",
            "trial_days", "created_at",
        ]
        read_only_fields = ["id", "created_at", "duration_label", "price_per_month", "savings"]

    def get_savings(self, obj) -> str | None:
        if obj.duration_months <= 1:
            return None
        monthly = (
            SubscriptionPlan.objects.filter(business_type=obj.business_type, duration_months=1)
            .values_list("price", flat=True)
            .first()
        )
        if monthly is None:
            return None
        saved = monthly * obj.duration_months - obj.price
        return str(saved) if saved > 0 else None


class PaymentLogSerializer(serializers.ModelSerializer):
    confirmed_by_name = serializers.CharField(source="confirmed_by.full_name", read_only=True, default=None)
    business_name = serializers.CharField(source="subscription.business.name", read_only=True)
    business_type = serializers.CharField(source="subscription.business.business_type", read_only=True)
    owner_phone = serializers.CharField(source="subscription.business.owner.phone_number", read_only=True)

    class Meta:
        model = PaymentLog
        fields = [
            "id", "subscription", "business_name", "business_type", "owner_phone",
            "amount", "confirmed_by", "confirmed_by_name", "note", "created_at",
        ]
        read_only_fields = ["id", "confirmed_by", "created_at", "business_name", "business_type", "owner_phone"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("To'lov summasi noldan katta bo'lishi kerak.")
        return value


class SubscriptionSerializer(serializers.ModelSerializer):
    business_name = serializers.CharField(source="business.name", read_only=True)
    business_type = serializers.CharField(source="business.business_type", read_only=True)
    owner_name = serializers.CharField(source="business.owner.full_name", read_only=True)
    owner_phone = serializers.CharField(source="business.owner.phone_number", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    plan_label = serializers.CharField(source="plan.duration_label", read_only=True)
    price = serializers.DecimalField(source="plan.price", max_digits=12, decimal_places=2, read_only=True)
    days_left = serializers.SerializerMethodField()
    payments = PaymentLogSerializer(many=True, read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id", "business", "business_name", "business_type", "owner_name", "owner_phone",
            "plan", "plan_label", "price", "status", "status_display",
            "trial_ends_at", "subscription_ends_at", "days_left",
            "approved_by", "payments", "created_at",
        ]
        read_only_fields = fields

    def get_days_left(self, obj) -> int | None:
        deadline = obj.subscription_ends_at if obj.status == "active" else obj.trial_ends_at
        if deadline is None:
            return None
        return max(math.ceil((deadline - timezone.now()).total_seconds() / 86400), 0)


class SubscriptionActivateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class SubscriptionRequestSerializer(serializers.ModelSerializer):
    business_name = serializers.CharField(source="business.name", read_only=True)
    business_type = serializers.CharField(source="business.business_type", read_only=True)
    owner_name = serializers.CharField(source="business.owner.full_name", read_only=True)
    owner_phone = serializers.CharField(source="business.owner.phone_number", read_only=True)
    plan_label = serializers.CharField(source="plan.duration_label", read_only=True)
    duration_months = serializers.IntegerField(source="plan.duration_months", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = SubscriptionRequest
        fields = [
            "id", "business", "business_name", "business_type",
            "owner_name", "owner_phone",
            "plan", "plan_label", "duration_months", "price",
            "status", "status_display", "note", "admin_note",
            "reviewed_at", "reviewed_by", "created_at",
        ]
        read_only_fields = fields


class SubscriptionRequestCreateSerializer(serializers.Serializer):
    plan = serializers.UUIDField()
    note = serializers.CharField(required=False, allow_blank=True, default="", max_length=255)


class SubscriptionRequestReviewSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, min_value=0)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
