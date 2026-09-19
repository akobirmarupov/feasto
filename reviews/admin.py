from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Review, ReviewPhoto


class ReviewPhotoInline(TabularInline):
    model = ReviewPhoto
    extra = 1
    fields = ("image",)


@admin.register(Review)
class ReviewAdmin(ModelAdmin):
    list_display = ("business", "user", "rating", "created_at")
    list_filter = ("rating", "business")
    list_filter_submit = True
    search_fields = ("business__name", "user__username", "comment")
    autocomplete_fields = ("user", "business", "reservation")
    list_select_related = ("user", "business")
    inlines = [ReviewPhotoInline]


@admin.register(ReviewPhoto)
class ReviewPhotoAdmin(ModelAdmin):
    list_display = ("review", "image", "created_at")
    search_fields = ("review__business__name", "review__user__username")
    autocomplete_fields = ("review",)
