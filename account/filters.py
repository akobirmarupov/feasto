import django_filters as filters
from django.contrib.auth import get_user_model

User = get_user_model()


class UserFilter(filters.FilterSet):

    search = filters.CharFilter(method="filter_search", label="Ism/username/telefon")

    class Meta:
        model = User
        fields = ["role", "is_phone_verified", "is_confirmed", "is_active"]

    def filter_search(self, queryset, name, value):
        from django.db.models import Q
        return queryset.filter(
            Q(full_name__icontains=value)
            | Q(username__icontains=value)
            | Q(phone_number__icontains=value)
        )
