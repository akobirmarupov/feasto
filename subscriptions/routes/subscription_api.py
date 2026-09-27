import logging

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import BusinessApplication
from common.exceptions import BadRequest
from common.models import PlatformSettings
from common.pagination import StandardResultsPagination
from common.permissions import IsBusinessOwnerOrApplicant, IsSuperAdmin
from common.services import get_owner_business
from subscriptions.filters import SubscriptionFilter
from subscriptions.models import Subscription, SubscriptionPlan, SubscriptionRequest
from subscriptions.routes.serializers import (
    SubscriptionActivateSerializer,
    SubscriptionPlanSerializer,
    SubscriptionRequestSerializer,
    SubscriptionSerializer,
)
from subscriptions.services import activate_subscription, expire_subscription

logger = logging.getLogger("subscriptions")

SUBSCRIPTION_RELATED = ("business", "business__owner", "plan", "approved_by")


def _get_subscription(pk):
    try:
        return Subscription.objects.select_related(*SUBSCRIPTION_RELATED).prefetch_related("payments").get(pk=pk)
    except Subscription.DoesNotExist:
        raise NotFound("Obuna topilmadi.")


class OwnerSubscriptionAPIView(APIView):
    permission_classes = [IsBusinessOwnerOrApplicant]

    @extend_schema(responses=SubscriptionSerializer)
    def get(self, request):
        business = get_owner_business(request.user)
        subscription = (
            Subscription.objects.filter(business=business)
            .select_related(*SUBSCRIPTION_RELATED)
            .prefetch_related("payments")
            .first()
        )
        plans = SubscriptionPlanSerializer(
            SubscriptionPlan.objects.all().order_by("business_type", "duration_months"), many=True,
        ).data
        platform = PlatformSettings.get_solo()
        telegram = f"@{platform.admin_telegram_username}"

        pending = (
            SubscriptionRequest.objects.filter(business=business, status=SubscriptionRequest.STATUS_PENDING)
            .select_related("business", "business__owner", "plan")
            .first()
        )
        pending_data = SubscriptionRequestSerializer(pending).data if pending else None

        if subscription is None:
            application = business.application
            applied_plan = application.plan
            rejected = application.status == BusinessApplication.STATUS_REJECTED

            if rejected:
                detail = (
                    "Arizangiz rad etilgan. Sababini administrator bilan "
                    "aniqlashtiring va arizani qayta yuboring — tarifni quyidan tanlaysiz."
                )
            elif applied_plan is None:
                detail = (
                    "Arizangiz administrator tekshiruvida. Tasdiqlangach "
                    f"{platform.trial_days} kunlik bepul sinov boshlanadi."
                )
            else:
                detail = (
                    "Arizangiz administrator tekshiruvida. To'lov tasdiqlangach "
                    f"obunangiz o'sha kundan boshlab {applied_plan.duration_label}ga faollashadi."
                )

            return Response({
                "has_subscription": False,
                "status": "rejected" if rejected else "awaiting_approval",
                "detail": detail,
                "business_type": business.business_type,
                "can_reapply": rejected,
                "trial_used": request.user.has_used_trial,
                "is_trial_application": applied_plan is None,
                "trial_days": platform.trial_days if applied_plan is None else None,
                "applied_plan": SubscriptionPlanSerializer(applied_plan).data if applied_plan else None,
                "admin_telegram": telegram,
                "plans": plans,
                "pending_request": pending_data,
            }, status=status.HTTP_200_OK)

        data = SubscriptionSerializer(subscription).data
        data["has_subscription"] = True
        data["admin_telegram"] = telegram
        data["plans"] = plans
        data["pending_request"] = pending_data
        return Response(data, status=status.HTTP_200_OK)


class AdminSubscriptionListAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = SubscriptionFilter
    queryset = Subscription.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=SubscriptionSerializer(many=True))
    def get(self, request):
        queryset = (
            Subscription.objects.select_related(*SUBSCRIPTION_RELATED)
            .prefetch_related("payments")
            .order_by("-created_at")
        )
        queryset = SubscriptionFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SubscriptionSerializer(page, many=True).data)


class AdminSubscriptionDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(responses=SubscriptionSerializer)
    def get(self, request, pk):
        return Response(SubscriptionSerializer(_get_subscription(pk)).data)


class AdminSubscriptionActivateAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=SubscriptionActivateSerializer, responses=SubscriptionSerializer)
    def post(self, request, pk):
        subscription = _get_subscription(pk)
        serializer = SubscriptionActivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        activate_subscription(
            business=subscription.business,
            approved_by=request.user,
            amount=serializer.validated_data.get("amount"),
            note=serializer.validated_data.get("note", ""),
        )
        return Response(SubscriptionSerializer(_get_subscription(pk)).data, status=status.HTTP_200_OK)


class AdminSubscriptionExpireAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=None, responses=SubscriptionSerializer)
    def post(self, request, pk):
        subscription = _get_subscription(pk)
        if subscription.status == "expired":
            raise BadRequest("Bu obuna allaqachon tugatilgan.")

        expire_subscription(subscription=subscription)
        logger.info(f"Subscription expired manually: id={subscription.id}, by={request.user.id}")
        return Response(SubscriptionSerializer(_get_subscription(pk)).data, status=status.HTTP_200_OK)
