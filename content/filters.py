import django_filters as filters

from content.models import Banner, News


class BannerFilter(filters.FilterSet):
    class Meta:
        model = Banner
        fields = ["placement", "media_type", "is_active"]


class NewsFilter(filters.FilterSet):
    class Meta:
        model = News
        fields = ["category", "is_active", "is_pinned"]
