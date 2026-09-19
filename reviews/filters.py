import django_filters as filters

from reviews.models import Review


class ReviewFilter(filters.FilterSet):
    min_rating = filters.NumberFilter(field_name="rating", lookup_expr="gte")
    max_rating = filters.NumberFilter(field_name="rating", lookup_expr="lte")

    class Meta:
        model = Review
        fields = ["business", "rating", "user"]
