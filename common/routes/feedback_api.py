import logging

from django.contrib.auth import get_user_model
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import Feedback
from common.pagination import StandardResultsPagination
from common.permissions import IsSuperAdmin
from common.routes.serializers import FeedbackCreateSerializer, FeedbackSerializer
from common.throttles import FeedbackThrottle
from notifications import links
from notifications.telegram import notify_admins

logger = logging.getLogger("common")


class FeedbackCreateAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [FeedbackThrottle]

    @extend_schema(request=FeedbackCreateSerializer, responses={201: None})
    def post(self, request):
        serializer = FeedbackCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        feedback = Feedback.objects.create(
            **serializer.validated_data,
            user=request.user if request.user.is_authenticated else None,
            page=str(request.data.get("page", ""))[:200],
        )

        try:
            from notifications.models import Notification
            from notifications.services import notify_many

            staff = get_user_model().objects.filter(is_staff=True, is_active=True)
            notify_many(
                list(staff),
                kind=Notification.KIND_SYSTEM,
                title=f"Yangi taklif: {feedback.get_kind_display().lower()}",
                body=feedback.short,
                link_url=links.ADMIN_FEEDBACK,
            )
            notify_admins(
                f"<b>Yangi taklif</b> ({feedback.get_kind_display()})\n{feedback.short}\n"
                f"Aloqa: {feedback.contact or '—'}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Taklif haqida xabar yuborilmadi: {exc}")

        logger.info(
            f"Feedback created: id={feedback.id}, kind={feedback.kind}, "
            f"user_id={feedback.user_id}, page={feedback.page}"
        )
        return Response(
            {"detail": "Rahmat! Fikringiz administratorga yuborildi."},
            status=status.HTTP_201_CREATED,
        )


class AdminFeedbackListAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "kind"]
    queryset = Feedback.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(
        responses=FeedbackSerializer(many=True),
        parameters=[
            OpenApiParameter("status", str, description="new | seen | done"),
            OpenApiParameter("kind", str, description="idea | problem | other"),
        ],
    )
    def get(self, request):
        queryset = Feedback.objects.select_related("user").order_by("-created_at")
        for field in ("status", "kind"):
            value = request.GET.get(field)
            if value:
                queryset = queryset.filter(**{field: value})

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        data = paginator.get_paginated_response(FeedbackSerializer(page, many=True).data).data
        data["unread"] = Feedback.objects.filter(status=Feedback.STATUS_NEW).count()
        return Response(data, status=status.HTTP_200_OK)


class AdminFeedbackDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    def get_object(self, pk):
        try:
            return Feedback.objects.select_related("user").get(pk=pk)
        except Feedback.DoesNotExist:
            raise NotFound("Taklif topilmadi.")

    @extend_schema(responses=FeedbackSerializer)
    def get(self, request, pk):
        return Response(FeedbackSerializer(self.get_object(pk)).data)

    @extend_schema(request=FeedbackSerializer, responses=FeedbackSerializer)
    def patch(self, request, pk):
        serializer = FeedbackSerializer(self.get_object(pk), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)
