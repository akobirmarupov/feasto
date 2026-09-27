import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import Business, VenuePricing
from businesses.routes.serializers import VenuePricingSerializer
from common.cache import invalidate_business_cache
from common.exceptions import BadRequest
from common.permissions import HasActiveSubscription, IsOwnerOfBusinessType
from common.services import get_owner_business

logger = logging.getLogger("businesses")


class BusinessPricingListAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=VenuePricingSerializer(many=True))
    def get(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True, business_type=Business.TYPE_VENUE).exists():
            raise NotFound("To'yxona topilmadi.")

        pricings = VenuePricing.objects.filter(business_id=business_id)
        return Response(VenuePricingSerializer(pricings, many=True).data, status=status.HTTP_200_OK)


class OwnerVenuePricingAPIView(APIView):
    permission_classes = [IsOwnerOfBusinessType, HasActiveSubscription]
    required_business_type = Business.TYPE_VENUE

    @extend_schema(responses=VenuePricingSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        pricings = VenuePricing.objects.filter(business=business)
        return Response(VenuePricingSerializer(pricings, many=True).data, status=status.HTTP_200_OK)

    @extend_schema(request=VenuePricingSerializer(many=True), responses=VenuePricingSerializer(many=True))
    def put(self, request):
        business = get_owner_business(request.user)

        if not isinstance(request.data, list):
            raise BadRequest("Ro'yxat yuboring: [{dish_count, price_per_person}, ...].")

        serializer = VenuePricingSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)

        dish_counts = [item["dish_count"] for item in serializer.validated_data]
        if len(dish_counts) != len(set(dish_counts)):
            raise BadRequest("Har bir taom soni uchun faqat bitta narx bo'lishi kerak.")

        with transaction.atomic():
            for item in serializer.validated_data:
                VenuePricing.objects.update_or_create(
                    business=business,
                    dish_count=item["dish_count"],
                    defaults={"price_per_person": item["price_per_person"]},
                )
            VenuePricing.objects.filter(business=business).exclude(dish_count__in=dish_counts).delete()

        invalidate_business_cache()
        logger.info(f"VenuePricing updated: business_id={business.id}, packages={sorted(dish_counts)}")

        pricings = VenuePricing.objects.filter(business=business)
        return Response(VenuePricingSerializer(pricings, many=True).data, status=status.HTTP_200_OK)
