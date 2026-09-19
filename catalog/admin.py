from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import RestaurantMenuItem, VenueMenuItem


@admin.register(RestaurantMenuItem)
class RestaurantMenuItemAdmin(ModelAdmin):
    list_display = ("name", "business", "category", "price", "is_available", "created_at")
    list_filter = ("is_available", "category", "business")
    list_filter_submit = True
    search_fields = ("name", "business__name")
    autocomplete_fields = ("business",)
    list_editable = ("is_available",)
    list_select_related = ("business",)


@admin.register(VenueMenuItem)
class VenueMenuItemAdmin(ModelAdmin):
    list_display = ("name", "business", "category", "created_at")
    list_filter = ("category", "business")
    list_filter_submit = True
    search_fields = ("name", "business__name")
    autocomplete_fields = ("business",)
    list_select_related = ("business",)
