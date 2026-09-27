import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import BadRequest
from common.pagination import StandardResultsPagination
from common.permissions import IsSuperAdmin
from subscriptions.filters import SubscriptionPlanFilter
from subscriptions.models import SubscriptionPlan
from subscriptions.routes.serializers import SubscriptionPlanSerializer

logger = logging.getLogger("subscriptions")


class SubscriptionPlanListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = SubscriptionPlanFilter
    queryset = SubscriptionPlan.objects.none()

    @extend_schema(responses=SubscriptionPlanSerializer(many=True))
    def get(self, request):
        queryset = SubscriptionPlan.objects.all().order_by("business_type", "duration_months")
        queryset = SubscriptionPlanFilter(request.GET, queryset=queryset).qs
        return Response(SubscriptionPlanSerializer(queryset, many=True).data, status=status.HTTP_200_OK)


class AdminSubscriptionPlanListCreateAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = SubscriptionPlanFilter
    pagination_class = StandardResultsPagination
    queryset = SubscriptionPlan.objects.none()

    @extend_schema(responses=SubscriptionPlanSerializer(many=True))
    def get(self, request):
        queryset = SubscriptionPlan.objects.all().order_by("business_type", "duration_months")
        queryset = SubscriptionPlanFilter(request.GET, queryset=queryset).qs
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(SubscriptionPlanSerializer(page, many=True).data)

    @extend_schema(request=SubscriptionPlanSerializer, responses={201: SubscriptionPlanSerializer})
    def post(self, request):
        serializer = SubscriptionPlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            plan = serializer.save()

        logger.info(f"SubscriptionPlan created: plan_id={plan.id}, by={request.user.id}")
        return Response(SubscriptionPlanSerializer(plan).data, status=status.HTTP_201_CREATED)


class AdminSubscriptionPlanDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    def get_object(self, pk):
        try:
            return SubscriptionPlan.objects.get(pk=pk)
        except SubscriptionPlan.DoesNotExist:
            raise NotFound("Tarif rejasi topilmadi.")

    @extend_schema(responses=SubscriptionPlanSerializer)
    def get(self, request, pk):
        return Response(SubscriptionPlanSerializer(self.get_object(pk)).data)

    @extend_schema(request=SubscriptionPlanSerializer, responses=SubscriptionPlanSerializer)
    def patch(self, request, pk):
        plan = self.get_object(pk)
        serializer = SubscriptionPlanSerializer(plan, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"SubscriptionPlan updated: plan_id={plan.id}, by={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        plan = self.get_object(pk)
        if plan.subscriptions.exists() or plan.requests.exists() or plan.applications.exists():
            raise BadRequest("Bu tarifga bog'langan obuna yoki arizalar bor — o'chirib bo'lmaydi.")

        with transaction.atomic():
            plan_id = plan.id
            plan.delete()

        logger.info(f"SubscriptionPlan deleted: plan_id={plan_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
