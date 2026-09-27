from django.urls import path

from notifications.routes.device_api import DeviceDeleteAPIView, DeviceListCreateAPIView
from notifications.routes.notification_api import (
    NotificationDeleteAPIView,
    NotificationListAPIView,
    NotificationReadAllAPIView,
    NotificationReadAPIView,
    NotificationUnreadCountAPIView,
)

app_name = "notifications"

urlpatterns = [
    # --- Notification (routes/notification_api.py) ---
    path("notifications/", NotificationListAPIView.as_view(), name="list"),
    path("notifications/unread-count/", NotificationUnreadCountAPIView.as_view(), name="unread-count"),
    path("notifications/read-all/", NotificationReadAllAPIView.as_view(), name="read-all"),
    path("notifications/<uuid:pk>/read/", NotificationReadAPIView.as_view(), name="read"),
    path("notifications/<uuid:pk>/", NotificationDeleteAPIView.as_view(), name="delete"),

    # --- Device / push token (routes/device_api.py) ---
    path("notifications/devices/", DeviceListCreateAPIView.as_view(), name="device-list"),
    path("notifications/devices/<str:token>/", DeviceDeleteAPIView.as_view(), name="device-delete"),
]
