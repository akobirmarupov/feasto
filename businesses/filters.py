import django_filters as filters
from django.db.models import Q

from businesses.models import Business, BusinessApplication, Hall, Room


class BusinessFilter(filters.FilterSet):

    type = filters.CharFilter(field_name="business_type", lookup_expr="exact")
    search = filters.CharFilter(method="filter_search", label="Nom, manzil yoki tuman")
    district = filters.CharFilter(field_name="district", lookup_expr="iexact")
    cuisine = filters.CharFilter(field_name="cuisine", lookup_expr="exact")
    min_rating = filters.NumberFilter(field_name="rating_avg", lookup_expr="gte")
    guests = filters.NumberFilter(method="filter_guests", label="Shuncha kishi sig'adigan joy")

    class Meta:
        model = Business
        fields = ["business_type", "is_visible", "district", "cuisine"]

    def filter_search(self, queryset, name, value):
        words = [w for w in value.split() if w][:6]
        for word in words:
            queryset = queryset.filter(
                Q(name__icontains=word)
                | Q(address__icontains=word)
                | Q(district__icontains=word)
            )
        return queryset

    def filter_guests(self, queryset, name, value):
        if "max_capacity" not in queryset.query.annotations:
            return queryset
        return queryset.filter(max_capacity__gte=value)


class RoomFilter(filters.FilterSet):
    min_capacity = filters.NumberFilter(field_name="capacity", lookup_expr="gte")
    max_capacity = filters.NumberFilter(field_name="capacity", lookup_expr="lte")

    class Meta:
        model = Room
        fields = ["business", "room_type", "deposit_tier"]


class HallFilter(filters.FilterSet):
    min_people = filters.NumberFilter(field_name="people", lookup_expr="gte")
    max_people = filters.NumberFilter(field_name="people", lookup_expr="lte")

    class Meta:
        model = Hall
        fields = ["business"]


class BusinessApplicationFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search", label="Biznes nomi yoki arizachi")

    class Meta:
        model = BusinessApplication
        fields = ["status", "business_type"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(business_name__icontains=value)
            | Q(applicant__full_name__icontains=value)
            | Q(applicant__phone_number__icontains=value)
        )
