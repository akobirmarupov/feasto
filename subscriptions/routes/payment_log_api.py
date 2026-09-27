import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardResultsPagination
from common.permissions import IsBusinessRole, IsSuperAdmin
from common.services import get_owner_business
from subscriptions.filters import PaymentLogFilter
from subscriptions.models import PaymentLog
from subscriptions.routes.serializers import PaymentLogSerializer

logger = logging.getLogger("subscriptions")

PAYMENT_RELATED = ("subscription__business", "subscription__business__owner", "confirmed_by")


class AdminPaymentLogListCreateAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = PaymentLogFilter
    queryset = PaymentLog.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=PaymentLogSerializer(many=True))
    def get(self, request):
        queryset = PaymentLog.objects.select_related(*PAYMENT_RELATED).order_by("-created_at")
        queryset = PaymentLogFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(PaymentLogSerializer(page, many=True).data)

    @extend_schema(request=PaymentLogSerializer, responses={201: PaymentLogSerializer})
    def post(self, request):
        serializer = PaymentLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            payment = serializer.save(confirmed_by=request.user)

        logger.info(
            f"PaymentLog created: payment_id={payment.id}, subscription_id={payment.subscription_id}, by={request.user.id}"
        )
        payment = PaymentLog.objects.select_related(*PAYMENT_RELATED).get(pk=payment.pk)
        return Response(PaymentLogSerializer(payment).data, status=status.HTTP_201_CREATED)


class OwnerPaymentLogListAPIView(APIView):
    permission_classes = [IsBusinessRole]
    pagination_class = StandardResultsPagination

    @extend_schema(responses=PaymentLogSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        queryset = (
            PaymentLog.objects.filter(subscription__business=business)
            .select_related(*PAYMENT_RELATED)
            .order_by("-created_at")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(PaymentLogSerializer(page, many=True).data)
