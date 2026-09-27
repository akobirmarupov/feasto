import datetime
import logging

from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from account.trust import TRUST_CANCEL_PENALTY
from common.exceptions import BadRequest, Conflict
from common.models import PlatformSettings
from common.pagination import StandardResultsPagination
from common.permissions import HasContactPhone, IsBusinessRole, IsCustomer, IsSuperAdmin
from common.services import get_owner_business
from common.throttles import ReservationCreateThrottle
from reservations.filters import ReservationFilter
from reservations.models import ACTIVE_STATUSES, Availability, Reservation
from reservations.routes.serializers import (
    ReservationSerializer,
    ReservationStatusSerializer,
    RestaurantReservationCreateSerializer,
    VenueReservationCreateSerializer,
)

logger = logging.getLogger("reservations")

RESERVATION_RELATED = ("user", "business", "room", "hall", "availability")


def _overlaps(qs, start_time, end_time):
    return qs.filter(start_time__lt=end_time, end_time__gt=start_time).exists()


def _get_reservation(pk, **filters):
    try:
        return Reservation.objects.select_related(*RESERVATION_RELATED).get(pk=pk, **filters)
    except Reservation.DoesNotExist:
        raise NotFound("Bron topilmadi.")


class ReservationCreateAPIView(APIView):
    permission_classes = [IsAuthenticated, HasContactPhone, IsCustomer]
    phone_message = (
        "Bron qilish uchun aloqa raqamingizni kiriting — "
        "joy egasi bronni tasdiqlash uchun sizga qo'ng'iroq qiladi."
    )
    throttle_classes = [ReservationCreateThrottle]

    @extend_schema(request=RestaurantReservationCreateSerializer, responses={201: ReservationSerializer})
    def post(self, request):
        is_venue = "hall" in request.data
        serializer_class = VenueReservationCreateSerializer if is_venue else RestaurantReservationCreateSerializer
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            with transaction.atomic():
                if is_venue:
                    reservation = self._create_venue_reservation(request.user, data)
                else:
                    reservation = self._create_restaurant_reservation(request.user, data)
        except ValueError as exc:
            raise Conflict(str(exc))

        reservation = _get_reservation(reservation.pk)
        admin_telegram = reservation.business.telegram_username or PlatformSettings.get_solo().admin_telegram_username
        payload = ReservationSerializer(reservation, context={"request": request}).data
        payload["message"] = (
            "So'rovingiz qabul qilindi va hozircha \"kutilmoqda\" holatida. "
            f"Bronni yakuniy tasdiqlash uchun @{admin_telegram} administratoriga "
            f"Telegram orqali murojaat qiling va oldindan {reservation.deposit_amount} so'm "
            "depozit to'lovini amalga oshiring."
        )
        payload["admin_telegram"] = f"@{admin_telegram}"
        return Response(payload, status=status.HTTP_201_CREATED)

    def _create_restaurant_reservation(self, user, data):
        room = data["room_obj"]
        business = room.business

        availability = (
            Availability.objects.select_for_update()
            .filter(business=business, room=room, date=data["date"])
            .first()
        )
        if availability is None:
            raise ValueError("Bu kun uchun ish jadvali ochilmagan.")

        day_end = datetime.time.max if availability.ends_at_midnight else availability.end_time
        if not (availability.start_time <= data["start_time"] < day_end) or data["end_time"] > day_end:
            raise ValueError(
                f"Tanlangan vaqt ish vaqtidan tashqarida "
                f"({availability.start_time:%H:%M}–{availability.end_time:%H:%M})."
            )

        existing = Reservation.objects.select_for_update().filter(
            room=room, availability=availability, status__in=ACTIVE_STATUSES,
        )
        if _overlaps(existing, data["start_time"], data["end_time"]):
            raise ValueError("Bu vaqt oralig'i allaqachon band qilingan.")

        reservation = Reservation(
            user=user, business=business, room=room, availability=availability,
            start_time=data["start_time"], end_time=data["end_time"],
            guests_count=data["guests_count"],
            selected_menu=data.get("menu_snapshot", []),
            special_request=data.get("special_request", ""),
            status="pending",
        )
        reservation.deposit_amount = reservation.resolve_deposit_amount()
        reservation.save()

        logger.info(
            f"Restaurant reservation created: id={reservation.id}, user_id={user.id}, "
            f"room_id={room.id}, date={data['date']}, {data['start_time']}-{data['end_time']}"
        )
        return reservation

    def _create_venue_reservation(self, user, data):
        hall = data["hall_obj"]
        business = hall.business

        availability = (
            Availability.objects.select_for_update()
            .filter(business=business, room__isnull=True, date=data["date"])
            .first()
        )
        if availability is None:
            raise ValueError("Bu kun uchun to'yxona jadvali ochilmagan.")

        taken = Reservation.objects.select_for_update().filter(
            availability=availability, hall__isnull=False, status__in=ACTIVE_STATUSES,
        ).exists()
        if taken or availability.is_booked:
            raise ValueError("Bu kun uchun zal allaqachon band. Boshqa sanani tanlang.")

        reservation = Reservation(
            user=user, business=business, hall=hall, availability=availability,
            guests_count=data["guests_count"],
            dish_count=data.get("dish_count"),
            selected_menu=data.get("menu_snapshot", []),
            special_request=data.get("special_request", ""),
            price_per_person=data.get("price_per_person"),
            day_rent_price=data.get("day_rent_price"),
            total_price=data.get("total_price"),
            status="pending",
        )
        reservation.deposit_amount = reservation.resolve_deposit_amount()
        reservation.save()

        logger.info(
            f"Venue reservation created: id={reservation.id}, user_id={user.id}, "
            f"hall_id={hall.id}, date={data['date']}, guests={data['guests_count']}"
        )
        return reservation


class _ReservationListMixin:
    filter_backends = [DjangoFilterBackend]
    filterset_class = ReservationFilter
    queryset = Reservation.objects.none()
    pagination_class = StandardResultsPagination

    def paginate(self, request, queryset):
        queryset = ReservationFilter(
            request.GET, queryset=queryset.select_related(*RESERVATION_RELATED).order_by("-created_at")
        ).qs
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ReservationSerializer(page, many=True, context={"request": request}).data
        )


class MyReservationListAPIView(_ReservationListMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ReservationSerializer(many=True))
    def get(self, request):
        return self.paginate(request, Reservation.objects.filter(user=request.user))


class PendingReviewAPIView(APIView):
    permission_classes = [IsAuthenticated]
    REVIEW_WINDOW_DAYS = 14
    LIMIT = 3

    @extend_schema(responses=ReservationSerializer(many=True))
    def get(self, request):
        since = timezone.localdate() - datetime.timedelta(days=self.REVIEW_WINDOW_DAYS)
        queryset = (
            Reservation.objects.filter(user=request.user, status="completed", availability__date__gte=since)
            .filter(review__isnull=True)
            .select_related(*RESERVATION_RELATED)
            .order_by("-availability__date", "-created_at")[: self.LIMIT]
        )
        return Response(ReservationSerializer(queryset, many=True, context={"request": request}).data)


class ReservationDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ReservationSerializer)
    def get(self, request, pk):
        reservation = _get_reservation(pk)

        is_customer = reservation.user_id == request.user.id
        is_owner = reservation.business.owner_id == request.user.id
        if not (is_customer or is_owner or request.user.is_staff):
            raise PermissionDenied("Bu bronni ko'rish huquqingiz yo'q.")

        return Response(ReservationSerializer(reservation, context={"request": request}).data)


class ReservationCancelAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=ReservationSerializer)
    def patch(self, request, pk):
        reservation = _get_reservation(pk)

        is_staff = request.user.is_staff
        if reservation.user_id != request.user.id and not is_staff:
            raise PermissionDenied("Bu bronni bekor qila olmaysiz.")

        with transaction.atomic():
            reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
            allowed, reason = reservation.cancel_check()
            if reservation.status in ("cancelled", "completed"):
                raise BadRequest(reason)
            if not allowed and not is_staff:
                raise BadRequest(reason, code="cancel_window_closed")

            reservation.status = "cancelled"
            reservation.save(update_fields=["status"])

            if reservation.user_id == request.user.id:
                request.user.penalize_trust(reason=f"reservation:{reservation.id}")

        logger.info(f"Reservation cancelled: id={reservation.id}, by={request.user.id}")
        reservation = _get_reservation(reservation.pk)
        payload = ReservationSerializer(reservation, context={"request": request}).data
        if reservation.user_id == request.user.id:
            trust = request.user.trust
            payload["trust"] = trust
            payload["message"] = (
                f"Bron bekor qilindi. Ishonchlilik balingizdan "
                f"{TRUST_CANCEL_PENALTY} Bit ayirildi — hozir {trust['bits']} Bit "
                f"({trust['level_display']})."
            )
        return Response(payload, status=status.HTTP_200_OK)


class OwnerReservationListAPIView(_ReservationListMixin, APIView):
    permission_classes = [IsBusinessRole]

    @extend_schema(responses=ReservationSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        return self.paginate(request, Reservation.objects.filter(business=business))


class OwnerReservationStatusAPIView(APIView):
    permission_classes = [IsBusinessRole]

    TRANSITIONS = {
        "pending": {"confirmed", "cancelled"},
        "confirmed": {"completed", "cancelled"},
    }

    @extend_schema(request=ReservationStatusSerializer, responses=ReservationSerializer)
    def patch(self, request, pk):
        business = get_owner_business(request.user)
        reservation = _get_reservation(pk, business=business)

        serializer = ReservationStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        with transaction.atomic():
            reservation = Reservation.objects.select_for_update().get(pk=reservation.pk)
            allowed = self.TRANSITIONS.get(reservation.status, set())
            if new_status not in allowed:
                raise BadRequest(
                    f'"{reservation.get_status_display()}" holatidagi bronni '
                    f'"{dict(Reservation.STATUS_CHOICES)[new_status]}" holatiga o\'tkazib bo\'lmaydi.'
                )
            reservation.status = new_status
            reservation.save(update_fields=["status"])

        logger.info(f"Reservation status changed: id={reservation.id}, status={new_status}, by={request.user.id}")
        return Response(
            ReservationSerializer(_get_reservation(reservation.pk), context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class AdminReservationListAPIView(_ReservationListMixin, APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(responses=ReservationSerializer(many=True))
    def get(self, request):
        return self.paginate(request, Reservation.objects.all())
