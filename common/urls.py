from django.urls import path

from common.routes.feedback_api import (
    AdminFeedbackDetailAPIView,
    AdminFeedbackListAPIView,
    FeedbackCreateAPIView,
)
from common.routes.health_api import HealthCheckAPIView
from common.routes.platform_settings_api import AdminSettingsAPIView, PublicSettingsAPIView

app_name = "common"

urlpatterns = [
    # --- monitoring (routes/health_api.py) ---
    path("health/", HealthCheckAPIView.as_view(), name="health"),

    # --- platforma sozlamalari (routes/platform_settings_api.py) ---
    path("settings/", PublicSettingsAPIView.as_view(), name="public-settings"),
    path("admin/settings/", AdminSettingsAPIView.as_view(), name="admin-settings"),

    # --- takliflar (routes/feedback_api.py) ---
    path("feedback/", FeedbackCreateAPIView.as_view(), name="feedback-create"),
    path("admin/feedback/", AdminFeedbackListAPIView.as_view(), name="admin-feedback"),
    path("admin/feedback/<uuid:pk>/", AdminFeedbackDetailAPIView.as_view(), name="admin-feedback-detail"),
]
