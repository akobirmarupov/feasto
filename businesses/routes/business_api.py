import datetime
import logging
from math import asin, cos, radians, sin, sqrt

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    F,
    FloatField,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import ACos, Coalesce, Cos, Greatest, Least, Radians, Round, Sin, TruncDate
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.filters import BusinessFilter
from businesses.models import Business, BusinessApplication, Hall, Room
from businesses.routes.serializers import (
    BusinessAdminCreateSerializer,
    BusinessAdminSerializer,
    BusinessApplicationSerializer,
    BusinessDetailSerializer,
    BusinessListSerializer,
    BusinessUpdateSerializer,
)
from businesses.services import TrialNotAvailable, approve_application, submit_application
from common.cache import build_cache_key, cached_response, invalidate_business_cache
from common.exceptions import BadRequest
from common.pagination import StandardResultsPagination
from common.permissions import HasActiveSubscription, IsBusinessRole, IsSuperAdmin
from common.services import get_owner_business

logger = logging.getLogger("businesses")
security_logger = logging.getLogger("django.security")

EARTH_RADIUS_KM = 6371.0
KM_PER_DEGREE_LAT = 111.32
MAX_RADIUS_KM = 100

RANKED_ORDER = ("unranked", "rank", "-rating_points", "-rating_avg", "-created_at")


def haversine_km(lat1, lng1, lat2, lng2):
    d_lat = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def bounding_box(lat, lng, radius_km):
    lat_delta = radius_km / KM_PER_DEGREE_LAT
    cos_lat = max(cos(radians(lat)), 0.01)
    lng_delta = radius_km / (KM_PER_DEGREE_LAT * cos_lat)
    return lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta


def distance_expression(lat, lng):
    lat_rad, lng_rad = radians(lat), radians(lng)
    cosine = (
        Cos(Value(lat_rad)) * Cos(Radians(F("latitude"))) * Cos(Radians(F("longitude")) - Value(lng_rad))
        + Sin(Value(lat_rad)) * Sin(Radians(F("latitude")))
    )
    clamped = Least(Value(1.0), Greatest(Value(-1.0), cosine))
    return Round(ACos(clamped) * Value(EARTH_RADIUS_KM), 2, output_field=FloatField())


def annotated_business_queryset():
    rooms = Room.objects.filter(business=OuterRef("pk")).order_by().values("business")
    halls = Hall.objects.filter(business=OuterRef("pk")).order_by().values("business")

    def sub(qs, expression):
        return Subquery(qs.annotate(v=expression).values("v")[:1], output_field=IntegerField())

    return (
        Business.objects.filter(is_visible=True)
        .annotate(
            unranked=Case(When(rank=0, then=Value(1)), default=Value(0), output_field=IntegerField()),
            rooms_count=Coalesce(sub(rooms, Count("id")), 0),
            halls_count=Coalesce(sub(halls, Count("id")), 0),
            min_capacity=Coalesce(sub(rooms, Min("capacity")), sub(halls, Min("people"))),
            max_capacity=Coalesce(sub(rooms, Max("capacity")), sub(halls, Max("people"))),
            min_day_price=Subquery(
                Hall.objects.filter(business=OuterRef("pk"), all_price__isnull=False)
                .order_by().values("business").annotate(v=Min("all_price")).values("v")[:1],
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        )
        .only(
            "id", "name", "business_type", "address", "district",
            "latitude", "longitude", "map_link", "description", "cover_photo",
            "cuisine", "open_time", "close_time", "rating_avg", "reviews_count",
            "rating_points", "rank", "created_at",
        )
    )


class BusinessListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BusinessFilter
    queryset = Business.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(
        responses=BusinessListSerializer(many=True),
        parameters=[
            OpenApiParameter("type", str, description="restaurant | venue"),
            OpenApiParameter("search", str, description="Nom, manzil yoki tuman"),
            OpenApiParameter("district", str),
            OpenApiParameter("cuisine", str, description="milliy | yevropa | fusion | ..."),
            OpenApiParameter("min_rating", float),
            OpenApiParameter("guests", int, description="Mehmonlar soni — sig'imi yetadiganlar"),
            OpenApiParameter("lat", float),
            OpenApiParameter("lng", float),
            OpenApiParameter("radius_km", float, description=f"1..{MAX_RADIUS_KM}"),
            OpenApiParameter("date", str, description="YYYY-MM-DD"),
        ],
    )
    def get(self, request):
        geo = self._parse_geo(request)
        audience = "auth" if request.user.is_authenticated else "anon"
        cache_key = build_cache_key(
            "biz:list",
            sorted(request.GET.items()),
            request.GET.get("page", 1),
            request.GET.get("page_size", ""),
            audience,
        )
        data = cached_response(
            cache_key, settings.CACHE_TTL_BUSINESS_LIST, lambda: self._build(request, geo),
        )
        return Response(data, status=status.HTTP_200_OK)

    def _parse_geo(self, request):
        lat, lng, radius = request.GET.get("lat"), request.GET.get("lng"), request.GET.get("radius_km")
        if not (lat and lng):
            return None
        try:
            lat, lng = float(lat), float(lng)
            radius = float(radius) if radius else 5.0
        except ValueError:
            raise BadRequest("lat, lng va radius_km son bo'lishi kerak.")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise BadRequest("Koordinatalar noto'g'ri.")
        radius = max(0.1, min(radius, MAX_RADIUS_KM))
        return lat, lng, radius

    def _build(self, request, geo):
        queryset = BusinessFilter(request.GET, queryset=annotated_business_queryset()).qs

        date = request.GET.get("date")
        if date:
            queryset = queryset.filter(availabilities__date=date, availabilities__is_booked=False).distinct()

        paginator = self.pagination_class()

        if geo is None:
            queryset = queryset.order_by(*RANKED_ORDER)
            page = paginator.paginate_queryset(queryset, request, view=self)
            return paginator.get_paginated_response(
                BusinessListSerializer(page, many=True, context={"request": request}).data
            ).data

        lat, lng, radius = geo
        min_lat, max_lat, min_lng, max_lng = bounding_box(lat, lng, radius)
        queryset = (
            queryset.filter(
                latitude__gte=min_lat, latitude__lte=max_lat,
                longitude__gte=min_lng, longitude__lte=max_lng,
            )
            .annotate(distance_km=distance_expression(lat, lng))
            .filter(distance_km__lte=radius)
            .order_by("distance_km", *RANKED_ORDER)
        )

        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            BusinessListSerializer(page, many=True, context={"request": request}).data
        ).data


class BusinessDetailAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=BusinessDetailSerializer)
    def get(self, request, pk):
        audience = "auth" if request.user.is_authenticated else "anon"
        cache_key = build_cache_key("biz:detail", pk, audience)
        data = cached_response(
            cache_key, settings.CACHE_TTL_BUSINESS_DETAIL, lambda: self._build(request, pk),
        )
        if data is None:
            raise NotFound("Biznes topilmadi yoki ommaviy ko'rinishda emas.")
        return Response(data, status=status.HTTP_200_OK)

    def _build(self, request, pk):
        business = (
            Business.objects.select_related("owner")
            .prefetch_related("photos", "rooms", "halls", "pricings", "restaurant_menu_items", "venue_menu_items")
            .filter(pk=pk, is_visible=True)
            .first()
        )
        if business is None:
            return None
        return BusinessDetailSerializer(business, context={"request": request}).data


class OwnerBusinessAPIView(APIView):
    permission_classes = [IsBusinessRole, HasActiveSubscription]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(responses=BusinessUpdateSerializer)
    def get(self, request):
        business = get_owner_business(request.user)
        return Response(BusinessUpdateSerializer(business, context={"request": request}).data)

    @extend_schema(request=BusinessUpdateSerializer, responses=BusinessUpdateSerializer)
    def patch(self, request):
        business = get_owner_business(request.user)
        serializer = BusinessUpdateSerializer(
            business, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        invalidate_business_cache()
        logger.info(f"Business updated by owner: business_id={business.id}, user_id={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)


class OwnerOverviewAPIView(APIView):
    permission_classes = [IsBusinessRole]

    @extend_schema(responses=None)
    def get(self, request):
        from reservations.models import Reservation
        from reservations.routes.serializers import ReservationSerializer

        business = get_owner_business(request.user)

        counts = Reservation.objects.filter(business=business).aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status="pending")),
            confirmed=Count("id", filter=Q(status="confirmed")),
            completed=Count("id", filter=Q(status="completed")),
            cancelled=Count("id", filter=Q(status="cancelled")),
        )
        recent = (
            Reservation.objects.filter(business=business)
            .select_related("user", "business", "room", "hall", "availability")
            .order_by("-created_at")[:5]
        )
        subscription = getattr(business, "subscription", None)

        return Response({
            "business": {
                "id": str(business.id),
                "name": business.name,
                "type": business.business_type,
                "cover_photo": request.build_absolute_uri(business.cover_photo.url)
                if business.cover_photo else None,
                "description": business.description,
                "district": business.district,
            },
            "stats": {
                "total_reservations": counts["total"],
                "pending_reservations": counts["pending"],
                "confirmed_reservations": counts["confirmed"],
                "completed_reservations": counts["completed"],
                "cancelled_reservations": counts["cancelled"],
                "rating_avg": business.rating_avg,
                "reviews_count": business.reviews_count,
            },
            "subscription": {
                "status": subscription.status if subscription else None,
                "trial_ends_at": subscription.trial_ends_at if subscription else None,
                "subscription_ends_at": subscription.subscription_ends_at if subscription else None,
            },
            "recent_reservations": ReservationSerializer(recent, many=True, context={"request": request}).data,
        }, status=status.HTTP_200_OK)


class AdminBusinessListAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BusinessFilter
    queryset = Business.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=BusinessAdminSerializer(many=True))
    def get(self, request):
        queryset = Business.objects.select_related("owner", "subscription").order_by("-created_at")
        queryset = BusinessFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(BusinessAdminSerializer(page, many=True).data)


class AdminBusinessCreateAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=BusinessAdminCreateSerializer, responses={201: BusinessAdminSerializer})
    def post(self, request):
        serializer = BusinessAdminCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        owner = data["owner"]

        try:
            with transaction.atomic():
                application, business, _ = submit_application(
                    applicant=owner,
                    business_type=data["business_type"],
                    business_name=data["name"],
                )
                business.district = data.get("district", "")
                business.address = data.get("address", "")
                business.telegram_username = data.get("telegram_username", "")
                business.save(update_fields=["district", "address", "telegram_username"])

                if data.get("approve"):
                    approve_application(application=application, approved_by=request.user)
        except TrialNotAvailable as error:
            raise BadRequest(str(error), code="trial_used")

        security_logger.info(
            f"Business created by admin: business_id={business.id}, owner_id={owner.id}, by={request.user.id}"
        )
        business = Business.objects.select_related("owner", "subscription").get(pk=business.pk)
        return Response(BusinessAdminSerializer(business).data, status=status.HTTP_201_CREATED)


class AdminBusinessDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    EDITABLE_FIELDS = {"name", "business_type", "address", "district", "is_visible", "telegram_username", "map_link"}

    def get_object(self, pk):
        try:
            return Business.objects.select_related("owner", "subscription").get(pk=pk)
        except Business.DoesNotExist:
            raise NotFound("Biznes topilmadi.")

    @extend_schema(responses=BusinessAdminSerializer)
    def get(self, request, pk):
        return Response(BusinessAdminSerializer(self.get_object(pk)).data)

    @extend_schema(request=BusinessAdminSerializer, responses=BusinessAdminSerializer)
    def patch(self, request, pk):
        business = self.get_object(pk)
        payload = {k: v for k, v in request.data.items() if k in self.EDITABLE_FIELDS}
        if not payload:
            raise BadRequest(f"Tahrirlash mumkin bo'lgan maydonlar: {sorted(self.EDITABLE_FIELDS)}")

        serializer = BusinessAdminSerializer(business, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            serializer.save()

        invalidate_business_cache()
        security_logger.info(
            f"Business updated by admin: business_id={business.id}, fields={sorted(payload)}, by={request.user.id}"
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        business = self.get_object(pk)

        active = business.reservations.filter(status__in=["pending", "confirmed"]).count()
        if active:
            raise BadRequest(
                f"Bu biznesda {active} ta faol bron bor — o'chirib bo'lmaydi. "
                "Avval bronlarni yakunlang yoki biznesni bloklang."
            )

        with transaction.atomic():
            business_id = business.id
            application = business.application
            business.delete()
            BusinessApplication.objects.filter(pk=application.pk).delete()

        invalidate_business_cache()
        security_logger.warning(f"Business DELETED by admin: business_id={business_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminBusinessToggleBlockAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=None, responses=BusinessAdminSerializer)
    def patch(self, request, pk):
        try:
            business = Business.objects.select_related("owner", "subscription").get(pk=pk)
        except Business.DoesNotExist:
            raise NotFound("Biznes topilmadi.")

        with transaction.atomic():
            business.is_visible = not business.is_visible
            business.save(update_fields=["is_visible"])

        security_logger.info(
            f"Business visibility toggled: business_id={business.id}, "
            f"is_visible={business.is_visible}, by={request.user.id}"
        )
        return Response(BusinessAdminSerializer(business).data, status=status.HTTP_200_OK)


class AdminOverviewAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    MAX_DAYS = 365
    EXPIRING_DAYS = 7

    @extend_schema(
        responses=None,
        parameters=[OpenApiParameter("days", int, description="Davr (kun), standart 30, ko'pi bilan 365")],
    )
    def get(self, request):
        from reservations.models import Reservation
        from subscriptions.models import PaymentLog, Subscription
        from subscriptions.routes.serializers import SubscriptionSerializer

        User = get_user_model()

        try:
            days = int(request.GET.get("days", 30))
        except ValueError:
            raise BadRequest("days butun son bo'lishi kerak.")
        days = max(1, min(days, self.MAX_DAYS))
        now = timezone.now()
        since = now - datetime.timedelta(days=days)

        business_counts = Business.objects.aggregate(
            total=Count("id"),
            restaurants=Count("id", filter=Q(business_type="restaurant")),
            venues=Count("id", filter=Q(business_type="venue")),
            visible=Count("id", filter=Q(is_visible=True)),
        )
        subscription_counts = Subscription.objects.aggregate(
            trial=Count("id", filter=Q(status="trial")),
            active=Count("id", filter=Q(status="active")),
            expired=Count("id", filter=Q(status="expired")),
        )
        reservation_counts = Reservation.objects.aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status="pending")),
        )
        recent_apps = BusinessApplication.objects.select_related("applicant", "plan").order_by("-created_at")[:5]

        period_reservations = Reservation.objects.filter(created_at__gte=since)
        period_payments = PaymentLog.objects.filter(created_at__gte=since)
        revenue = period_payments.aggregate(total=Sum("amount"), count=Count("id"))
        reservations_by_day = list(
            period_reservations.annotate(day=TruncDate("created_at"))
            .values("day").annotate(count=Count("id")).order_by("day")
        )
        payments_by_day = list(
            period_payments.annotate(day=TruncDate("created_at"))
            .values("day").annotate(total=Sum("amount"), count=Count("id")).order_by("day")
        )
        expiring = (
            Subscription.objects.filter(
                Q(status="active", subscription_ends_at__range=(now, now + datetime.timedelta(days=self.EXPIRING_DAYS)))
                | Q(status="trial", trial_ends_at__range=(now, now + datetime.timedelta(days=self.EXPIRING_DAYS)))
            )
            .select_related("business", "business__owner", "plan", "approved_by")
            .prefetch_related("payments")
            .order_by("subscription_ends_at", "trial_ends_at")[:20]
        )

        return Response({
            "period": {
                "days": days,
                "since": since,
                "new_users": User.objects.filter(date_joined__gte=since).count(),
                "new_businesses": Business.objects.filter(created_at__gte=since).count(),
                "new_applications": BusinessApplication.objects.filter(created_at__gte=since).count(),
                "reservations": period_reservations.count(),
                "completed_reservations": period_reservations.filter(status="completed").count(),
                "cancelled_reservations": period_reservations.filter(status="cancelled").count(),
                "revenue": revenue["total"] or 0,
                "payments_count": revenue["count"],
                "reservations_by_day": reservations_by_day,
                "payments_by_day": payments_by_day,
            },
            "expiring_subscriptions": SubscriptionSerializer(expiring, many=True).data,
            "stats": {
                "users_count": User.objects.filter(is_active=True).count(),
                "businesses_count": business_counts["total"],
                "restaurants_count": business_counts["restaurants"],
                "venues_count": business_counts["venues"],
                "visible_businesses": business_counts["visible"],
                "pending_applications": BusinessApplication.objects.filter(status="pending_payment").count(),
                "reservations_count": reservation_counts["total"],
                "pending_reservations": reservation_counts["pending"],
            },
            "subscriptions": subscription_counts,
            "recent_applications": BusinessApplicationSerializer(recent_apps, many=True).data,
        }, status=status.HTTP_200_OK)
