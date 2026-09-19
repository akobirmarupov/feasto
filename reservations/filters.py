import django_filters as filters

from reservations.models import Availability, Reservation


class AvailabilityFilter(filters.FilterSet):
    date_from = filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = Availability
        fields = ["business", "room", "date", "is_booked"]


class ReservationFilter(filters.FilterSet):
    date = filters.DateFilter(field_name="availability__date")
    date_from = filters.DateFilter(field_name="availability__date", lookup_expr="gte")
    date_to = filters.DateFilter(field_name="availability__date", lookup_expr="lte")
    business_type = filters.CharFilter(field_name="business__business_type")

    class Meta:
        model = Reservation
        fields = ["status", "business", "room", "hall"]
