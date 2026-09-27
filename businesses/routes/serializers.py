from django.contrib.auth import get_user_model
from rest_framework import serializers

from businesses.models import (
    Business,
    BusinessApplication,
    BusinessPhoto,
    Hall,
    Room,
    VenuePricing,
)
from subscriptions.models import SubscriptionPlan


class BusinessPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessPhoto
        fields = ["id", "image", "order"]
        read_only_fields = ["id"]


class RoomSerializer(serializers.ModelSerializer):
    room_type_display = serializers.CharField(source="get_room_type_display", read_only=True)
    deposit_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Room
        fields = [
            "id", "business", "name", "room_type", "room_type_display",
            "capacity", "photo", "deposit_tier", "deposit_price",
            "deposit_amount", "created_at",
        ]
        read_only_fields = ["id", "business", "created_at"]

    def validate(self, attrs):
        tier = attrs.get("deposit_tier", getattr(self.instance, "deposit_tier", None))
        if not tier:
            raise serializers.ValidationError(
                {"deposit_tier": "Restoran xonasi uchun depozit tarifi (premium/pro) tanlanishi shart."}
            )
        return attrs


class HallSerializer(serializers.ModelSerializer):
    deposit_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Hall
        fields = [
            "id", "business", "name", "people", "photo", "package",
            "all_price", "deposit_price", "deposit_amount", "created_at",
        ]
        read_only_fields = ["id", "business", "created_at"]

    def validate_all_price(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError(
                "Bir kunlik ijara narxi noldan katta bo'lishi kerak. "
                "Narx belgilamoqchi bo'lmasangiz, maydonni bo'sh qoldiring."
            )
        return value


class VenuePricingSerializer(serializers.ModelSerializer):
    class Meta:
        model = VenuePricing
        fields = ["id", "business", "dish_count", "price_per_person"]
        read_only_fields = ["id", "business"]


class LocationVisibilityMixin:
    def _can_see_private(self) -> bool:
        request = self.context.get("request")
        return bool(request and request.user and request.user.is_authenticated)

    def get_address(self, obj) -> str | None:
        return obj.address if self._can_see_private() else None

    def get_latitude(self, obj) -> float | None:
        return obj.latitude if self._can_see_private() else None

    def get_longitude(self, obj) -> float | None:
        return obj.longitude if self._can_see_private() else None

    def get_map_links(self, obj) -> dict:
        return obj.map_links if self._can_see_private() else {}

    def get_location_locked(self, obj) -> bool:
        return not self._can_see_private()


class BusinessListSerializer(LocationVisibilityMixin, serializers.ModelSerializer):
    business_type_display = serializers.CharField(source="get_business_type_display", read_only=True)
    cuisine_display = serializers.CharField(source="get_cuisine_display", read_only=True)
    rooms_count = serializers.IntegerField(read_only=True, default=0)
    halls_count = serializers.IntegerField(read_only=True, default=0)
    min_capacity = serializers.IntegerField(read_only=True, required=False)
    max_capacity = serializers.IntegerField(read_only=True, required=False)
    distance_km = serializers.FloatField(read_only=True, required=False)
    min_day_price = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True, required=False
    )

    address = serializers.SerializerMethodField()
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    map_links = serializers.SerializerMethodField()
    location_locked = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = [
            "id", "name", "business_type", "business_type_display",
            "address", "district", "latitude", "longitude", "map_links",
            "location_locked",
            "description", "cover_photo", "cuisine", "cuisine_display",
            "open_time", "close_time", "rating_avg", "reviews_count",
            "rating_points", "rank",
            "rooms_count", "halls_count", "min_capacity", "max_capacity",
            "min_day_price", "distance_km",
        ]


class BusinessDetailSerializer(LocationVisibilityMixin, serializers.ModelSerializer):
    business_type_display = serializers.CharField(source="get_business_type_display", read_only=True)
    cuisine_display = serializers.CharField(source="get_cuisine_display", read_only=True)
    gallery = BusinessPhotoSerializer(source="photos", many=True, read_only=True)
    rooms = serializers.SerializerMethodField()
    halls = serializers.SerializerMethodField()
    menu = serializers.SerializerMethodField()
    dish_pricing = serializers.SerializerMethodField()
    pricing_mode = serializers.SerializerMethodField()
    owner_username = serializers.CharField(source="owner.username", read_only=True)

    telegram_username = serializers.SerializerMethodField()
    phone_number = serializers.SerializerMethodField()
    contacts_locked = serializers.SerializerMethodField()

    address = serializers.SerializerMethodField()
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    map_links = serializers.SerializerMethodField()
    location_locked = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = [
            "id", "name", "business_type", "business_type_display",
            "address", "district", "latitude", "longitude", "map_links",
            "location_locked",
            "description", "cover_photo", "gallery",
            "cuisine", "cuisine_display", "open_time", "close_time",
            "rating_avg", "reviews_count", "rating_points", "rank",
            "telegram_username", "phone_number", "contacts_locked",
            "is_visible", "owner_username",
            "rooms", "halls", "menu", "dish_pricing", "pricing_mode", "created_at",
        ]

    def get_telegram_username(self, obj) -> str | None:
        return obj.telegram_username if self._can_see_private() else None

    def get_phone_number(self, obj) -> str | None:
        return obj.phone_number if self._can_see_private() else None

    def get_contacts_locked(self, obj) -> bool:
        return not self._can_see_private()

    def get_rooms(self, obj) -> list:
        if obj.business_type != Business.TYPE_RESTAURANT:
            return []
        return RoomSerializer(obj.rooms.all(), many=True, context=self.context).data

    def get_halls(self, obj) -> list:
        if obj.business_type != Business.TYPE_VENUE:
            return []
        return HallSerializer(obj.halls.all(), many=True, context=self.context).data

    def get_menu(self, obj) -> list:
        from catalog.routes.serializers import RestaurantMenuItemSerializer, VenueMenuItemSerializer

        if obj.business_type == Business.TYPE_RESTAURANT:
            items = [i for i in obj.restaurant_menu_items.all() if i.is_available]
            return RestaurantMenuItemSerializer(items, many=True, context=self.context).data
        return VenueMenuItemSerializer(obj.venue_menu_items.all(), many=True, context=self.context).data

    def get_dish_pricing(self, obj) -> list:
        if obj.business_type != Business.TYPE_VENUE:
            return []
        return VenuePricingSerializer(obj.pricings.all(), many=True).data

    def get_pricing_mode(self, obj) -> str:
        return obj.venue_pricing_mode()


class BusinessUpdateSerializer(serializers.ModelSerializer):
    map_links = serializers.DictField(read_only=True)

    class Meta:
        model = Business
        fields = [
            "id", "name", "business_type", "address", "district",
            "latitude", "longitude", "map_link", "map_links",
            "description", "cover_photo",
            "cuisine", "open_time", "close_time",
            "telegram_username", "phone_number",
            "rating_avg", "reviews_count", "is_visible",
        ]
        read_only_fields = ["id", "business_type", "rating_avg", "reviews_count", "is_visible", "map_links"]

    def validate(self, attrs):
        business_type = self.instance.business_type if self.instance else None
        if business_type == Business.TYPE_RESTAURANT:
            open_time = attrs.get("open_time", getattr(self.instance, "open_time", None))
            close_time = attrs.get("close_time", getattr(self.instance, "close_time", None))
            if open_time and close_time and open_time == close_time:
                raise serializers.ValidationError(
                    {"close_time": "Ish vaqti boshlanishi va tugashi bir xil bo'lolmaydi."}
                )

        link = attrs.get("map_link")
        if not link or link == getattr(self.instance, "map_link", ""):
            return attrs

        current_lat = getattr(self.instance, "latitude", None)
        current_lng = getattr(self.instance, "longitude", None)
        edited_by_hand = (
            attrs.get("latitude", current_lat) != current_lat
            or attrs.get("longitude", current_lng) != current_lng
        )
        if edited_by_hand:
            return attrs

        from common.maps import coordinates_from_link

        found = coordinates_from_link(link)
        if found:
            attrs["latitude"], attrs["longitude"] = found
        return attrs


class BusinessAdminSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source="owner.full_name", read_only=True)
    owner_phone = serializers.CharField(source="owner.phone_number", read_only=True)
    map_links = serializers.DictField(read_only=True)
    subscription_status = serializers.SerializerMethodField()

    class Meta:
        model = Business
        fields = [
            "id", "name", "business_type", "address", "district", "owner",
            "owner_name", "owner_phone", "telegram_username", "map_link", "map_links",
            "is_visible", "rating_avg",
            "reviews_count", "subscription_status", "created_at",
        ]
        read_only_fields = ["id", "owner", "rating_avg", "reviews_count", "created_at"]

    def get_subscription_status(self, obj) -> str | None:
        subscription = getattr(obj, "subscription", None)
        return subscription.status if subscription else None


class BusinessAdminCreateSerializer(serializers.Serializer):
    owner = serializers.IntegerField(help_text="Egasi bo'ladigan foydalanuvchi ID'si")
    business_type = serializers.ChoiceField(choices=Business.TYPE_CHOICES)
    name = serializers.CharField(max_length=200)
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    address = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    telegram_username = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    approve = serializers.BooleanField(default=False)

    def validate_owner(self, value):
        User = get_user_model()
        try:
            user = User.objects.get(pk=value, is_active=True)
        except User.DoesNotExist:
            raise serializers.ValidationError("Bunday foydalanuvchi topilmadi.")
        existing = user.businesses.select_related("application").first()
        if existing is not None and existing.application.status != BusinessApplication.STATUS_REJECTED:
            raise serializers.ValidationError("Bu foydalanuvchida allaqachon biznes bor.")
        return user


class BusinessApplicationCreateSerializer(serializers.ModelSerializer):
    plan = serializers.PrimaryKeyRelatedField(
        queryset=SubscriptionPlan.objects.all(), required=False, allow_null=True,
        help_text="Bo'sh — bepul sinov arizasi.",
    )

    class Meta:
        model = BusinessApplication
        fields = ["business_type", "business_name", "plan"]

    def validate_business_name(self, value):
        value = value.strip()
        if len(value) < 3:
            raise serializers.ValidationError("Biznes nomi kamida 3 ta belgidan iborat bo'lsin.")
        return value


class BusinessApplicationSerializer(serializers.ModelSerializer):
    applicant_name = serializers.CharField(source="applicant.full_name", read_only=True)
    applicant_username = serializers.CharField(source="applicant.username", read_only=True)
    applicant_phone = serializers.CharField(source="applicant.phone_number", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    business_type_display = serializers.CharField(source="get_business_type_display", read_only=True)
    plan_label = serializers.CharField(source="plan.duration_label", read_only=True, default=None)
    business_id = serializers.SerializerMethodField()

    class Meta:
        model = BusinessApplication
        fields = [
            "id", "applicant", "applicant_name", "applicant_username", "applicant_phone",
            "business_type", "business_type_display", "business_name", "plan", "plan_label",
            "status", "status_display", "business_id",
            "created_at", "approved_at", "approved_by",
        ]
        read_only_fields = fields

    def get_business_id(self, obj) -> str | None:
        business = getattr(obj, "business", None)
        return str(business.id) if business else None
