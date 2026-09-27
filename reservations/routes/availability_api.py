import datetime
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import Business, Hall, Room
from common.exceptions import BadRequest
from common.pagination import StandardResultsPagination
from common.permissions import HasActiveSubscription, IsBusinessRole
from common.services import get_owner_business
from reservations.filters import AvailabilityFilter
from reservations.models import ACTIVE_STATUSES, Availability, Reservation
from reservations.routes.serializers import (
    AvailabilitySerializer,
    GenerateAvailabilitySerializer,
    HallBusyDatesSerializer,
    RoomBusyHoursSerializer,
)

logger = logging.getLogger("reservations")


def _parse_date(value, *, required=False):
    if not value:
        if required:
            raise BadRequest("`date` parametri majburiy (YYYY-MM-DD).")
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise BadRequest("Sana YYYY-MM-DD ko'rinishida bo'lishi kerak.")


class BusinessAvailabilityAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = AvailabilityFilter
    queryset = Availability.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(
        responses=AvailabilitySerializer(many=True),
        parameters=[
            OpenApiParameter("date", str, description="YYYY-MM-DD"),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
            OpenApiParameter("room", str, description="Xona UUID (restoran uchun)"),
        ],
    )
    def get(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True).exists():
            raise NotFound("Biznes topilmadi.")

        queryset = Availability.objects.filter(business_id=business_id).select_related("room").order_by("date", "start_time")
        if not request.GET.get("date") and not request.GET.get("date_from"):
            queryset = queryset.filter(date__gte=timezone.localdate())
        queryset = AvailabilityFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AvailabilitySerializer(page, many=True).data)


class RoomBusyHoursAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        responses=RoomBusyHoursSerializer,
        parameters=[OpenApiParameter("date", str, description="YYYY-MM-DD", required=True)],
    )
    def get(self, request, room_id):
        date = _parse_date(request.GET.get("date"), required=True)

        try:
            room = Room.objects.select_related("business").get(pk=room_id)
        except Room.DoesNotExist:
            raise NotFound("Xona topilmadi.")

        availability = Availability.objects.filter(room=room, date=date).first()
        busy = (
            Reservation.objects.filter(room=room, availability__date=date, status__in=ACTIVE_STATUSES)
            .exclude(start_time__isnull=True)
            .values("start_time", "end_time")
            .order_by("start_time")
        )

        payload = RoomBusyHoursSerializer({
            "room_id": room.id,
            "room_name": room.name,
            "capacity": room.capacity,
            "deposit_amount": room.deposit_amount,
            "date": date,
            "is_open": availability is not None,
            "open_time": availability.start_time if availability else None,
            "close_time": availability.end_time if availability else None,
            "busy_ranges": list(busy),
        }).data
        return Response(payload, status=status.HTTP_200_OK)


class HallBusyDatesAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        responses=HallBusyDatesSerializer,
        parameters=[OpenApiParameter("date_from", str), OpenApiParameter("date_to", str)],
    )
    def get(self, request, hall_id):
        try:
            hall = Hall.objects.select_related("business").get(pk=hall_id)
        except Hall.DoesNotExist:
            raise NotFound("Zal topilmadi.")

        date_from = _parse_date(request.GET.get("date_from")) or timezone.localdate()
        date_to = _parse_date(request.GET.get("date_to"))

        queryset = Reservation.objects.filter(
            hall=hall, status__in=ACTIVE_STATUSES, availability__date__gte=date_from,
        )
        if date_to:
            queryset = queryset.filter(availability__date__lte=date_to)

        busy_dates = queryset.values_list("availability__date", flat=True).order_by("availability__date")

        payload = HallBusyDatesSerializer({
            "hall_id": hall.id,
            "hall_name": hall.name,
            "capacity": hall.people,
            "deposit_amount": hall.deposit_amount,
            "all_price": hall.all_price,
            "busy_dates": sorted({d for d in busy_dates if d}),
        }).data
        return Response(payload, status=status.HTTP_200_OK)


class OwnerAvailabilityListAPIView(APIView):
    permission_classes = [IsBusinessRole, HasActiveSubscription]
    filter_backends = [DjangoFilterBackend]
    filterset_class = AvailabilityFilter
    queryset = Availability.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=AvailabilitySerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        queryset = Availability.objects.filter(business=business).select_related("room").order_by("date", "start_time")
        queryset = AvailabilityFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(AvailabilitySerializer(page, many=True).data)


class OwnerAvailabilityGenerateAPIView(APIView):
    permission_classes = [IsBusinessRole, HasActiveSubscription]

    @extend_schema(request=GenerateAvailabilitySerializer, responses={201: None})
    def post(self, request):
        business = get_owner_business(request.user)
        serializer = GenerateAvailabilitySerializer(data=request.data, context={"business": business})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        room = Room.objects.get(pk=data["room"], business=business) if data.get("room") else None
        months = [datetime.date(data["year"], m, 1) for m in sorted(set(data["months"]))]

        try:
            with transaction.atomic():
                created, skipped = Availability.generate_for_months(
                    business=business, room=room,
                    start_time=data["start_time"], end_time=data["end_time"], months=months,
                )
        except DjangoValidationError as exc:
            raise BadRequest(" ".join(exc.messages))

        logger.info(
            f"Availability generated: business_id={business.id}, room_id={room.id if room else None}, "
            f"created={created}, skipped={skipped}"
        )
        return Response({
            "created": created,
            "skipped": skipped,
            "detail": f"{created} ta kun uchun bo'sh vaqt yaratildi, {skipped} ta kun allaqachon mavjud edi.",
        }, status=status.HTTP_201_CREATED)


class OwnerAvailabilityDetailAPIView(APIView):
    permission_classes = [IsBusinessRole, HasActiveSubscription]

    def get_object(self, request, pk):
        business = get_owner_business(request.user)
        try:
            return Availability.objects.select_related("room", "business").get(pk=pk, business=business)
        except Availability.DoesNotExist:
            raise NotFound("Bo'sh vaqt yozuvi topilmadi yoki sizga tegishli emas.")

    @extend_schema(responses=AvailabilitySerializer)
    def get(self, request, pk):
        return Response(AvailabilitySerializer(self.get_object(request, pk)).data)

    @extend_schema(request=AvailabilitySerializer, responses=AvailabilitySerializer)
    def patch(self, request, pk):
        availability = self.get_object(request, pk)
        serializer = AvailabilitySerializer(availability, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        availability = self.get_object(request, pk)
        if availability.reservations.filter(status__in=ACTIVE_STATUSES).exists():
            raise BadRequest("Bu kunda faol bronlar bor — o'chirib bo'lmaydi.")

        with transaction.atomic():
            availability_id = availability.id
            availability.delete()

        logger.info(f"Availability deleted: id={availability_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
