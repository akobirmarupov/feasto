import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.filters import HallFilter
from businesses.models import Business, Hall
from businesses.routes.serializers import HallSerializer
from common.exceptions import BadRequest
from common.pagination import StandardResultsPagination
from common.permissions import HasActiveSubscription, IsOwnerOfBusinessType
from common.services import get_owner_business

logger = logging.getLogger("businesses")


class BusinessHallListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = HallFilter
    queryset = Hall.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=HallSerializer(many=True))
    def get(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True).exists():
            raise NotFound("Biznes topilmadi.")

        queryset = Hall.objects.filter(business_id=business_id).select_related("business").order_by("-people", "name")
        queryset = HallFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(HallSerializer(page, many=True, context={"request": request}).data)


class OwnerHallListCreateAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_VENUE
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = HallFilter
    queryset = Hall.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=HallSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        queryset = Hall.objects.filter(business=business).select_related("business").order_by("-people", "name")
        queryset = HallFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(HallSerializer(page, many=True, context={"request": request}).data)

    @extend_schema(request=HallSerializer, responses={201: HallSerializer})
    def post(self, request):
        business = get_owner_business(request.user)
        serializer = HallSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            hall = serializer.save(business=business)

        logger.info(f"Hall created: hall_id={hall.id}, business_id={business.id}")
        return Response(HallSerializer(hall, context={"request": request}).data, status=status.HTTP_201_CREATED)


class OwnerHallDetailAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_VENUE
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, request, pk):
        business = get_owner_business(request.user)
        try:
            return Hall.objects.select_related("business").get(pk=pk, business=business)
        except Hall.DoesNotExist:
            raise NotFound("Zal topilmadi yoki sizga tegishli emas.")

    @extend_schema(responses=HallSerializer)
    def get(self, request, pk):
        return Response(HallSerializer(self.get_object(request, pk), context={"request": request}).data)

    @extend_schema(request=HallSerializer, responses=HallSerializer)
    def patch(self, request, pk):
        hall = self.get_object(request, pk)
        serializer = HallSerializer(hall, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"Hall updated: hall_id={hall.id}, by={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        hall = self.get_object(request, pk)
        if hall.reservations.filter(status__in=["pending", "confirmed"]).exists():
            raise BadRequest("Bu zalda faol bronlar bor — avval ularni yakunlang yoki bekor qiling.")

        with transaction.atomic():
            hall_id = hall.id
            hall.delete()

        logger.info(f"Hall deleted: hall_id={hall_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
