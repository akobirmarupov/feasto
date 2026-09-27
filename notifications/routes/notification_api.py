import logging

from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardResultsPagination
from notifications.filters import NotificationFilter
from notifications.models import Notification
from notifications.routes.serializers import NotificationSerializer, UnreadCountSerializer

logger = logging.getLogger("notifications")


class NotificationListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = NotificationFilter
    queryset = Notification.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=NotificationSerializer(many=True))
    def get(self, request):
        queryset = Notification.objects.for_user(request.user).order_by("-created_at")
        queryset = NotificationFilter(request.GET, queryset=queryset).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        response = paginator.get_paginated_response(NotificationSerializer(page, many=True).data)
        response.data["unread"] = Notification.objects.for_user(request.user).unread().count()
        return response


class NotificationUnreadCountAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UnreadCountSerializer)
    def get(self, request):
        unread = Notification.objects.for_user(request.user).unread().count()
        return Response({"unread": unread}, status=status.HTTP_200_OK)


class NotificationReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=NotificationSerializer)
    def patch(self, request, pk):
        try:
            notification = Notification.objects.for_user(request.user).get(pk=pk)
        except Notification.DoesNotExist:
            raise NotFound("Bildirishnoma topilmadi.")

        with transaction.atomic():
            notification.mark_read()

        return Response(NotificationSerializer(notification).data, status=status.HTTP_200_OK)


class NotificationReadAllAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses=UnreadCountSerializer)
    def post(self, request):
        with transaction.atomic():
            updated = (
                Notification.objects.for_user(request.user).unread()
                .update(is_read=True, read_at=timezone.now())
            )
        logger.info(f"Notifications marked read: user_id={request.user.pk}, count={updated}")
        return Response({"unread": 0, "updated": updated}, status=status.HTTP_200_OK)


class NotificationDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        deleted, _ = Notification.objects.for_user(request.user).filter(pk=pk).delete()
        if not deleted:
            raise NotFound("Bildirishnoma topilmadi.")
        return Response(status=status.HTTP_204_NO_CONTENT)
