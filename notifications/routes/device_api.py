import logging

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.models import Device
from notifications.routes.serializers import DeviceSerializer

logger = logging.getLogger("notifications")


class DeviceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=DeviceSerializer(many=True))
    def get(self, request):
        devices = Device.objects.filter(user=request.user, is_active=True)
        return Response(DeviceSerializer(devices, many=True).data)

    @extend_schema(request=DeviceSerializer, responses={201: DeviceSerializer, 200: DeviceSerializer})
    def post(self, request):
        serializer = DeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            device, created = Device.objects.update_or_create(
                token=data["token"],
                defaults={
                    "user": request.user,
                    "platform": data.get("platform", "android"),
                    "is_active": True,
                    "last_seen_at": timezone.now(),
                },
            )

        logger.info(f"Device registered: user_id={request.user.id}, platform={device.platform}, created={created}")
        return Response(
            DeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DeviceDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: None})
    def delete(self, request, token):
        deleted, _ = Device.objects.filter(user=request.user, token=token).delete()
        if not deleted:
            raise NotFound("Qurilma topilmadi.")
        logger.info(f"Device removed: user_id={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
