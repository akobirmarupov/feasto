from rest_framework import serializers

from catalog.models import RestaurantMenuItem, VenueMenuItem


class RestaurantMenuItemSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = RestaurantMenuItem
        fields = [
            "id", "business", "name", "category", "category_display",
            "description", "price", "photo", "is_available", "created_at",
        ]
        read_only_fields = ["id", "business", "created_at"]

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Narx manfiy bo'lolmaydi.")
        return value


class VenueMenuItemSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)

    class Meta:
        model = VenueMenuItem
        fields = ["id", "business", "name", "category", "category_display", "description", "photo", "created_at"]
        read_only_fields = ["id", "business", "created_at"]


class ShowcaseRestaurantMenuItemSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    business_district = serializers.CharField(source="business.district", read_only=True)

    class Meta:
        model = RestaurantMenuItem
        fields = [
            "id", "business", "business_name", "business_district",
            "name", "category", "category_display", "price", "photo",
        ]


class ShowcaseVenueMenuItemSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    business_district = serializers.CharField(source="business.district", read_only=True)
    price_from = serializers.SerializerMethodField()

    class Meta:
        model = VenueMenuItem
        fields = [
            "id", "business", "business_name", "business_district",
            "name", "category", "category_display", "photo", "price_from",
        ]

    def get_price_from(self, obj) -> str | None:
        value = getattr(obj, "min_price", None)
        return str(value) if value is not None else None
