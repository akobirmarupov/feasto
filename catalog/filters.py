import django_filters as filters

from catalog.models import RestaurantMenuItem, VenueMenuItem


class RestaurantMenuItemFilter(filters.FilterSet):
    search = filters.CharFilter(field_name="name", lookup_expr="icontains")
    min_price = filters.NumberFilter(field_name="price", lookup_expr="gte")
    max_price = filters.NumberFilter(field_name="price", lookup_expr="lte")

    class Meta:
        model = RestaurantMenuItem
        fields = ["business", "category", "is_available"]


class VenueMenuItemFilter(filters.FilterSet):
    search = filters.CharFilter(field_name="name", lookup_expr="icontains")

    class Meta:
        model = VenueMenuItem
        fields = ["business", "category"]
