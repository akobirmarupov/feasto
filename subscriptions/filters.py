import django_filters as filters

from subscriptions.models import PaymentLog, Subscription, SubscriptionPlan


class SubscriptionFilter(filters.FilterSet):
    business_type = filters.CharFilter(field_name="business__business_type", lookup_expr="exact")
    search = filters.CharFilter(field_name="business__name", lookup_expr="icontains")

    class Meta:
        model = Subscription
        fields = ["status", "plan"]


class SubscriptionPlanFilter(filters.FilterSet):
    class Meta:
        model = SubscriptionPlan
        fields = ["business_type"]


class PaymentLogFilter(filters.FilterSet):
    business = filters.UUIDFilter(field_name="subscription__business_id")

    class Meta:
        model = PaymentLog
        fields = ["subscription"]
