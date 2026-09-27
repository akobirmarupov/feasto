import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import BadRequest
from common.pagination import StandardResultsPagination
from common.permissions import HasContactPhone, IsBusinessRole, IsSuperAdmin
from common.services import get_owner_business
from subscriptions.models import SubscriptionPlan, SubscriptionRequest
from subscriptions.routes.serializers import (
    SubscriptionRequestCreateSerializer,
    SubscriptionRequestReviewSerializer,
    SubscriptionRequestSerializer,
)
from subscriptions.services import approve_renewal, reject_renewal, request_renewal

logger = logging.getLogger("subscriptions")

REQUEST_RELATED = ("business", "business__owner", "plan", "reviewed_by")


def _get_request(pk):
    try:
        return SubscriptionRequest.objects.select_related(*REQUEST_RELATED).get(pk=pk)
    except SubscriptionRequest.DoesNotExist:
        raise NotFound("Obuna arizasi topilmadi.")


class OwnerSubscriptionRequestAPIView(APIView):
    permission_classes = [IsBusinessRole, HasContactPhone]
    phone_message = (
        "Obuna so'rovi uchun aloqa raqamingizni kiriting — administrator to'lovni siz bilan kelishadi."
    )
    pagination_class = StandardResultsPagination

    @extend_schema(responses=SubscriptionRequestSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        queryset = (
            SubscriptionRequest.objects.filter(business=business)
            .select_related(*REQUEST_RELATED)
            .order_by("-created_at")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SubscriptionRequestSerializer(page, many=True).data)

    @extend_schema(
        request=SubscriptionRequestCreateSerializer,
        responses={201: SubscriptionRequestSerializer, 200: SubscriptionRequestSerializer},
    )
    def post(self, request):
        business = get_owner_business(request.user)
        serializer = SubscriptionRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            plan = SubscriptionPlan.objects.get(pk=serializer.validated_data["plan"])
        except SubscriptionPlan.DoesNotExist:
            raise ValidationError({"plan": "Bunday tarif rejasi topilmadi."})

        try:
            renewal, created = request_renewal(
                business=business, plan=plan, note=serializer.validated_data.get("note", ""),
            )
        except ValueError as error:
            raise ValidationError({"plan": str(error)})

        return Response(
            SubscriptionRequestSerializer(_get_request(renewal.pk)).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminSubscriptionRequestListAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "business"]
    queryset = SubscriptionRequest.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(
        responses=SubscriptionRequestSerializer(many=True),
        parameters=[
            OpenApiParameter("status", str, description="pending_payment | approved | rejected"),
            OpenApiParameter("business", str, description="Biznes ID'si"),
        ],
    )
    def get(self, request):
        queryset = SubscriptionRequest.objects.select_related(*REQUEST_RELATED).order_by("-created_at")

        state = request.GET.get("status")
        if state:
            queryset = queryset.filter(status=state)
        business = request.GET.get("business")
        if business:
            queryset = queryset.filter(business_id=business)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SubscriptionRequestSerializer(page, many=True).data)


class AdminSubscriptionRequestApproveAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=SubscriptionRequestReviewSerializer, responses=SubscriptionRequestSerializer)
    def post(self, request, pk):
        renewal = _get_request(pk)
        serializer = SubscriptionRequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                approve_renewal(
                    request=renewal,
                    approved_by=request.user,
                    amount=serializer.validated_data.get("amount"),
                    note=serializer.validated_data.get("note", ""),
                )
        except ValueError as error:
            raise BadRequest(str(error))

        return Response(SubscriptionRequestSerializer(_get_request(pk)).data, status=status.HTTP_200_OK)


class AdminSubscriptionRequestRejectAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    @extend_schema(request=SubscriptionRequestReviewSerializer, responses=SubscriptionRequestSerializer)
    def post(self, request, pk):
        renewal = _get_request(pk)
        serializer = SubscriptionRequestReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                reject_renewal(
                    request=renewal, rejected_by=request.user, note=serializer.validated_data.get("note", ""),
                )
        except ValueError as error:
            raise BadRequest(str(error))

        return Response(SubscriptionRequestSerializer(_get_request(pk)).data, status=status.HTTP_200_OK)
