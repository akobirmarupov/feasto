import django_filters

from notifications.models import Notification


class NotificationFilter(django_filters.FilterSet):

    kind = django_filters.CharFilter(field_name="kind", lookup_expr="exact")
    is_read = django_filters.BooleanFilter(field_name="is_read")

    class Meta:
        model = Notification
        fields = ["kind", "is_read"]
