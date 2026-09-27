import datetime
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from businesses.models import Business, Hall, Room, VenuePricing
from catalog.models import RestaurantMenuItem, VenueMenuItem
from reservations.models import Availability, Reservation

MAX_BOOKING_DAYS_AHEAD = 365
MAX_MENU_ITEMS = 20


class AvailabilitySerializer(serializers.ModelSerializer):
    room_name = serializers.CharField(source="room.name", read_only=True, default=None)

    class Meta:
        model = Availability
        fields = ["id", "business", "room", "room_name", "date", "start_time", "end_time", "is_booked"]
        read_only_fields = ["id", "business", "room", "is_booked"]

    def validate(self, attrs):
        instance = self.instance
        start = attrs.get("start_time", getattr(instance, "start_time", None))
        end = attrs.get("end_time", getattr(instance, "end_time", None))
        if instance is not None and start and end:
            is_restaurant = instance.business.business_type == Business.TYPE_RESTAURANT
            allow_midnight = not is_restaurant and end == datetime.time(0, 0)
            if not allow_midnight and end <= start:
                raise serializers.ValidationError(
                    {"end_time": "Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak."}
                )
        return attrs


class GenerateAvailabilitySerializer(serializers.Serializer):
    room = serializers.UUIDField(required=False, allow_null=True)
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    year = serializers.IntegerField(min_value=2020, max_value=2100)
    months = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=12), allow_empty=False,
    )

    def validate(self, attrs):
        business = self.context["business"]
        is_restaurant = business.business_type == Business.TYPE_RESTAURANT
        room_id = attrs.get("room")

        if is_restaurant and not room_id:
            raise serializers.ValidationError({"room": "Restoran uchun xona tanlanishi shart."})
        if not is_restaurant and room_id:
            raise serializers.ValidationError({"room": "To'yxona uchun xona tanlanmaydi — bo'sh qoldiring."})
        if room_id and not Room.objects.filter(pk=room_id, business=business).exists():
            raise serializers.ValidationError({"room": "Bu xona sizning biznesingizga tegishli emas."})

        is_midnight_end = (not is_restaurant) and attrs["end_time"] == datetime.time(0, 0)
        if not is_midnight_end and attrs["end_time"] <= attrs["start_time"]:
            raise serializers.ValidationError(
                {"end_time": "Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak."}
            )
        return attrs


class BusyRangeSerializer(serializers.Serializer):
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()


class RoomBusyHoursSerializer(serializers.Serializer):
    room_id = serializers.UUIDField()
    room_name = serializers.CharField()
    capacity = serializers.IntegerField()
    deposit_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    date = serializers.DateField()
    is_open = serializers.BooleanField()
    open_time = serializers.TimeField(allow_null=True)
    close_time = serializers.TimeField(allow_null=True)
    busy_ranges = BusyRangeSerializer(many=True)


class HallBusyDatesSerializer(serializers.Serializer):
    hall_id = serializers.UUIDField()
    hall_name = serializers.CharField()
    capacity = serializers.IntegerField()
    deposit_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    all_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    busy_dates = serializers.ListField(child=serializers.DateField())


class ReservationCustomerSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    phone_number = serializers.CharField(read_only=True)
    initials = serializers.CharField(read_only=True)
    avatar = serializers.SerializerMethodField()
    trust_bits = serializers.IntegerField(read_only=True)
    trust_level = serializers.SerializerMethodField()
    trust_level_display = serializers.SerializerMethodField()
    trust_tone = serializers.SerializerMethodField()
    cancelled_reservations_count = serializers.IntegerField(read_only=True)
    date_joined = serializers.DateTimeField(read_only=True)

    def get_avatar(self, obj) -> str | None:
        if not obj.avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.avatar.url) if request else obj.avatar.url

    def get_trust_level(self, obj) -> str:
        return obj.trust["level"]

    def get_trust_level_display(self, obj) -> str:
        return obj.trust["level_display"]

    def get_trust_tone(self, obj) -> str:
        return obj.trust["tone"]


class ReservationSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_phone = serializers.CharField(source="user.phone_number", read_only=True)
    customer = ReservationCustomerSerializer(source="user", read_only=True)

    business_name = serializers.CharField(source="business.name", read_only=True)
    business_type = serializers.CharField(source="business.business_type", read_only=True)
    business_telegram = serializers.CharField(source="business.telegram_username", read_only=True)
    business_address = serializers.CharField(source="business.address", read_only=True)
    business_district = serializers.CharField(source="business.district", read_only=True)
    business_map_links = serializers.SerializerMethodField()

    room_name = serializers.CharField(source="room.name", read_only=True, default=None)
    hall_name = serializers.CharField(source="hall.name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    date = serializers.DateField(source="availability.date", read_only=True, default=None)

    can_cancel = serializers.SerializerMethodField()
    cancel_deadline = serializers.SerializerMethodField()
    cancel_blocked_reason = serializers.SerializerMethodField()
    event_starts_at = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            "id", "user", "user_name", "user_phone", "customer",
            "business", "business_name", "business_type", "business_telegram",
            "business_address", "business_district", "business_map_links",
            "room", "room_name", "hall", "hall_name",
            "availability", "date", "start_time", "end_time", "event_starts_at",
            "guests_count", "special_request", "selected_menu",
            "dish_count", "price_per_person", "day_rent_price",
            "total_price", "deposit_amount",
            "status", "status_display", "confirmed_at",
            "can_cancel", "cancel_deadline", "cancel_blocked_reason",
            "created_at",
        ]
        read_only_fields = fields

    def get_business_map_links(self, obj) -> dict:
        return obj.business.map_links

    def get_can_cancel(self, obj) -> bool:
        return obj.cancel_check()[0]

    def get_cancel_deadline(self, obj) -> str | None:
        deadline = obj.cancel_deadline()
        return deadline.isoformat() if deadline else None

    def get_cancel_blocked_reason(self, obj) -> str:
        allowed, reason = obj.cancel_check()
        return "" if allowed else reason

    def get_event_starts_at(self, obj) -> str | None:
        event_at = obj.event_starts_at()
        return event_at.isoformat() if event_at else None


def _validate_date(date):
    today = timezone.localdate()
    if date < today:
        raise serializers.ValidationError({"date": "O'tib ketgan sanaga bron qilib bo'lmaydi."})
    if (date - today).days > MAX_BOOKING_DAYS_AHEAD:
        raise serializers.ValidationError(
            {"date": f"Eng ko'pi bilan {MAX_BOOKING_DAYS_AHEAD} kun oldin bron qilish mumkin."}
        )


class RestaurantReservationCreateSerializer(serializers.Serializer):
    room = serializers.UUIDField()
    date = serializers.DateField()
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()
    guests_count = serializers.IntegerField(min_value=1, max_value=1000)
    menu_items = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list, max_length=MAX_MENU_ITEMS,
    )
    special_request = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)

    def validate(self, attrs):
        if attrs["end_time"] <= attrs["start_time"]:
            raise serializers.ValidationError(
                {"end_time": "Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak."}
            )
        _validate_date(attrs["date"])
        if attrs["date"] == timezone.localdate() and attrs["start_time"] <= timezone.localtime().time():
            raise serializers.ValidationError({"start_time": "O'tib ketgan vaqtga bron qilib bo'lmaydi."})

        try:
            room = Room.objects.select_related("business").get(pk=attrs["room"])
        except Room.DoesNotExist:
            raise serializers.ValidationError({"room": "Xona topilmadi."})

        if room.business.business_type != Business.TYPE_RESTAURANT:
            raise serializers.ValidationError({"room": "Bu xona restoranga tegishli emas."})
        if not room.business.is_visible:
            raise serializers.ValidationError({"room": "Bu restoran hozir bron qabul qilmayapti."})
        if attrs["guests_count"] > room.capacity:
            raise serializers.ValidationError(
                {"guests_count": f"Bu xona eng ko'pi bilan {room.capacity} kishilik."}
            )

        menu_ids = list(dict.fromkeys(attrs.get("menu_items") or []))
        if menu_ids:
            items = list(
                RestaurantMenuItem.objects.filter(
                    id__in=menu_ids, business=room.business, is_available=True
                ).values("id", "name", "price")
            )
            if len(items) != len(menu_ids):
                raise serializers.ValidationError(
                    {"menu_items": "Ba'zi taomlar topilmadi yoki hozir mavjud emas."}
                )
            attrs["menu_snapshot"] = [
                {"id": str(i["id"]), "name": i["name"], "price": str(i["price"])} for i in items
            ]
        else:
            attrs["menu_snapshot"] = []

        attrs["room_obj"] = room
        return attrs


class VenueReservationCreateSerializer(serializers.Serializer):
    hall = serializers.UUIDField()
    date = serializers.DateField()
    guests_count = serializers.IntegerField(min_value=1, max_value=5000)
    dish_count = serializers.IntegerField(min_value=1, max_value=3, required=False, allow_null=True, default=None)
    menu_items = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list, max_length=MAX_MENU_ITEMS,
    )
    special_request = serializers.CharField(required=False, allow_blank=True, default="", max_length=1000)

    def validate(self, attrs):
        _validate_date(attrs["date"])

        try:
            hall = Hall.objects.select_related("business").get(pk=attrs["hall"])
        except Hall.DoesNotExist:
            raise serializers.ValidationError({"hall": "Zal topilmadi."})

        if hall.business.business_type != Business.TYPE_VENUE:
            raise serializers.ValidationError({"hall": "Bu zal to'yxonaga tegishli emas."})
        if not hall.business.is_visible:
            raise serializers.ValidationError({"hall": "Bu to'yxona hozir bron qabul qilmayapti."})
        if attrs["guests_count"] > hall.people:
            raise serializers.ValidationError(
                {"guests_count": f"Bu zal eng ko'pi bilan {hall.people} kishilik."}
            )

        menu_ids = list(dict.fromkeys(attrs.get("menu_items") or []))
        explicit_dish_count = attrs.get("dish_count")
        dish_count = explicit_dish_count
        if dish_count is None and menu_ids:
            dish_count = len(menu_ids)

        packages = {p.dish_count: p for p in VenuePricing.objects.filter(business=hall.business)}
        pricing = packages.get(dish_count) if dish_count else None

        if dish_count and pricing is None and (packages or explicit_dish_count):
            if packages:
                available = ", ".join(str(n) for n in sorted(packages))
                message = (f"Bu to'yxonada {dish_count} xil taom uchun narx "
                           f"belgilanmagan. Mavjud paketlar: {available}.")
            else:
                message = ("Bu to'yxonada taom paketlari belgilanmagan — taom sonini "
                           "bo'sh qoldiring, faqat zal ijarasi to'lanadi.")
            raise serializers.ValidationError({"dish_count": message})

        day_rent = hall.all_price
        food_total = pricing.price_per_person * attrs["guests_count"] if pricing is not None else None
        if day_rent is None and food_total is None:
            total = None
        else:
            total = (day_rent or Decimal(0)) + (food_total or Decimal(0))

        attrs["price_per_person"] = pricing.price_per_person if pricing is not None else None
        attrs["day_rent_price"] = day_rent
        attrs["total_price"] = total
        attrs["dish_count"] = dish_count

        if pricing is not None and len(menu_ids) != dish_count:
            raise serializers.ValidationError({
                "menu_items": f"Menyudan aynan {dish_count} xil taom tanlang "
                              f"(hozir {len(menu_ids)} ta belgilangan). Taom "
                              "kerak bo'lmasa, taom sonini bo'sh qoldiring — "
                              "u holda faqat zal ijarasi to'lanadi."
            })

        if menu_ids:
            items = list(
                VenueMenuItem.objects.filter(id__in=menu_ids, business=hall.business).values("id", "name")
            )
            if len(items) != len(menu_ids):
                raise serializers.ValidationError({"menu_items": "Ba'zi taomlar topilmadi."})
            attrs["menu_snapshot"] = [{"id": str(i["id"]), "name": i["name"]} for i in items]
        else:
            attrs["menu_snapshot"] = []

        attrs["hall_obj"] = hall
        return attrs


class ReservationStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["confirmed", "cancelled", "completed"])
