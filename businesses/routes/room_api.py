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

from businesses.filters import RoomFilter
from businesses.models import Business, Room
from businesses.routes.serializers import RoomSerializer
from common.exceptions import BadRequest
from common.pagination import StandardResultsPagination
from common.permissions import HasActiveSubscription, IsOwnerOfBusinessType
from common.services import get_owner_business

logger = logging.getLogger("businesses")


class BusinessRoomListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = RoomFilter
    queryset = Room.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=RoomSerializer(many=True))
    def get(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True).exists():
            raise NotFound("Biznes topilmadi.")

        queryset = Room.objects.filter(business_id=business_id).select_related("business").order_by("capacity", "name")
        queryset = RoomFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(RoomSerializer(page, many=True, context={"request": request}).data)


class OwnerRoomListCreateAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_RESTAURANT
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = RoomFilter
    queryset = Room.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=RoomSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        queryset = Room.objects.filter(business=business).select_related("business").order_by("capacity", "name")
        queryset = RoomFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(RoomSerializer(page, many=True, context={"request": request}).data)

    @extend_schema(request=RoomSerializer, responses={201: RoomSerializer})
    def post(self, request):
        business = get_owner_business(request.user)
        serializer = RoomSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            room = serializer.save(business=business)

        logger.info(f"Room created: room_id={room.id}, business_id={business.id}")
        return Response(RoomSerializer(room, context={"request": request}).data, status=status.HTTP_201_CREATED)


class OwnerRoomDetailAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_RESTAURANT
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, request, pk):
        business = get_owner_business(request.user)
        try:
            return Room.objects.select_related("business").get(pk=pk, business=business)
        except Room.DoesNotExist:
            raise NotFound("Xona topilmadi yoki sizga tegishli emas.")

    @extend_schema(responses=RoomSerializer)
    def get(self, request, pk):
        return Response(RoomSerializer(self.get_object(request, pk), context={"request": request}).data)

    @extend_schema(request=RoomSerializer, responses=RoomSerializer)
    def patch(self, request, pk):
        room = self.get_object(request, pk)
        serializer = RoomSerializer(room, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"Room updated: room_id={room.id}, by={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        room = self.get_object(request, pk)
        if room.reservations.filter(status__in=["pending", "confirmed"]).exists():
            raise BadRequest("Bu xonada faol bronlar bor — avval ularni yakunlang yoki bekor qiling.")

        with transaction.atomic():
            room_id = room.id
            room.delete()

        logger.info(f"Room deleted: room_id={room_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
