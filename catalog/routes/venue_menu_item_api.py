import logging

from django.db import transaction
from django.db.models import Min, OuterRef, Subquery
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import Business, VenuePricing
from catalog.filters import VenueMenuItemFilter
from catalog.models import VenueMenuItem
from catalog.routes.serializers import ShowcaseVenueMenuItemSerializer, VenueMenuItemSerializer
from common.pagination import StandardResultsPagination
from common.permissions import HasActiveSubscription, IsOwnerOfBusinessType
from common.services import get_owner_business

logger = logging.getLogger("catalog")


class BusinessVenueMenuAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = VenueMenuItemFilter
    queryset = VenueMenuItem.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=VenueMenuItemSerializer(many=True))
    def get(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True).exists():
            raise NotFound("Biznes topilmadi.")

        queryset = (
            VenueMenuItem.objects.filter(business_id=business_id)
            .select_related("business")
            .order_by("category", "name")
        )
        queryset = VenueMenuItemFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            VenueMenuItemSerializer(page, many=True, context={"request": request}).data
        )


class ShowcaseVenueMenuAPIView(APIView):
    permission_classes = [AllowAny]
    queryset = VenueMenuItem.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=ShowcaseVenueMenuItemSerializer(many=True))
    def get(self, request):
        cheapest = (
            VenuePricing.objects.filter(business=OuterRef("business_id"))
            .values("business")
            .annotate(low=Min("price_per_person"))
            .values("low")[:1]
        )
        queryset = (
            VenueMenuItem.objects.filter(business__is_visible=True, business__business_type=Business.TYPE_VENUE)
            .select_related("business")
            .annotate(min_price=Subquery(cheapest))
            .order_by("-created_at")
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            ShowcaseVenueMenuItemSerializer(page, many=True, context={"request": request}).data
        )


class OwnerVenueMenuListCreateAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_VENUE
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = VenueMenuItemFilter
    queryset = VenueMenuItem.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=VenueMenuItemSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        queryset = VenueMenuItem.objects.filter(business=business).select_related("business").order_by("category", "name")
        queryset = VenueMenuItemFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            VenueMenuItemSerializer(page, many=True, context={"request": request}).data
        )

    @extend_schema(request=VenueMenuItemSerializer, responses={201: VenueMenuItemSerializer})
    def post(self, request):
        business = get_owner_business(request.user)
        serializer = VenueMenuItemSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            item = serializer.save(business=business)

        logger.info(f"Venue menu item created: item_id={item.id}, business_id={business.id}")
        return Response(
            VenueMenuItemSerializer(item, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class OwnerVenueMenuDetailAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_VENUE
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, request, pk):
        business = get_owner_business(request.user)
        try:
            return VenueMenuItem.objects.select_related("business").get(pk=pk, business=business)
        except VenueMenuItem.DoesNotExist:
            raise NotFound("Taom topilmadi yoki sizga tegishli emas.")

    @extend_schema(responses=VenueMenuItemSerializer)
    def get(self, request, pk):
        return Response(VenueMenuItemSerializer(self.get_object(request, pk), context={"request": request}).data)

    @extend_schema(request=VenueMenuItemSerializer, responses=VenueMenuItemSerializer)
    def patch(self, request, pk):
        item = self.get_object(request, pk)
        serializer = VenueMenuItemSerializer(item, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"Venue menu item updated: item_id={item.id}, by={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        item = self.get_object(request, pk)
        with transaction.atomic():
            item_id = item.id
            item.delete()
        logger.info(f"Venue menu item deleted: item_id={item_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
