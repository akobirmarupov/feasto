import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import PlatformSettings
from common.permissions import IsSuperAdmin
from common.routes.serializers import PlatformSettingsSerializer, PublicSettingsSerializer

logger = logging.getLogger("common")


class PublicSettingsAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=PublicSettingsSerializer)
    def get(self, request):
        from subscriptions.models import SubscriptionPlan
        from subscriptions.routes.serializers import SubscriptionPlanSerializer

        platform = PlatformSettings.get_solo()
        plans = SubscriptionPlan.objects.all().order_by("business_type", "duration_months")

        return Response({
            "admin_telegram": f"@{platform.admin_telegram_username}",
            "support_phone": platform.support_phone,
            "trial_days": platform.trial_days,
            "subscription_days": platform.subscription_days,
            "deposits": {
                "room_premium": platform.room_deposit_premium,
                "room_pro": platform.room_deposit_pro,
                "venue": platform.venue_deposit,
            },
            "plans": SubscriptionPlanSerializer(plans, many=True).data,
        }, status=status.HTTP_200_OK)


class AdminSettingsAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(responses=PlatformSettingsSerializer)
    def get(self, request):
        return Response(PlatformSettingsSerializer(PlatformSettings.get_solo()).data)

    @extend_schema(request=PlatformSettingsSerializer, responses=PlatformSettingsSerializer)
    def patch(self, request):
        platform = PlatformSettings.get_solo()
        serializer = PlatformSettingsSerializer(platform, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"Platform settings updated by admin: user_id={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)
