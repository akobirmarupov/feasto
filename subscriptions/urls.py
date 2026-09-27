from django.urls import path

from subscriptions.routes.payment_log_api import (
    AdminPaymentLogListCreateAPIView,
    OwnerPaymentLogListAPIView,
)
from subscriptions.routes.subscription_api import (
    AdminSubscriptionActivateAPIView,
    AdminSubscriptionDetailAPIView,
    AdminSubscriptionExpireAPIView,
    AdminSubscriptionListAPIView,
    OwnerSubscriptionAPIView,
)
from subscriptions.routes.subscription_plan_api import (
    AdminSubscriptionPlanDetailAPIView,
    AdminSubscriptionPlanListCreateAPIView,
    SubscriptionPlanListAPIView,
)
from subscriptions.routes.subscription_request_api import (
    AdminSubscriptionRequestApproveAPIView,
    AdminSubscriptionRequestListAPIView,
    AdminSubscriptionRequestRejectAPIView,
    OwnerSubscriptionRequestAPIView,
)

app_name = "subscriptions"

urlpatterns = [
    # --- SubscriptionPlan (routes/subscription_plan_api.py) ---
    path("subscription-plans/", SubscriptionPlanListAPIView.as_view(), name="plan-list"),
    path("admin/subscription-plans/", AdminSubscriptionPlanListCreateAPIView.as_view(), name="admin-plan-list"),
    path("admin/subscription-plans/<uuid:pk>/", AdminSubscriptionPlanDetailAPIView.as_view(), name="admin-plan-detail"),

    # --- Subscription (routes/subscription_api.py) ---
    path("owner/subscription/", OwnerSubscriptionAPIView.as_view(), name="owner-subscription"),
    path("admin/subscriptions/", AdminSubscriptionListAPIView.as_view(), name="admin-subscription-list"),
    path("admin/subscriptions/<uuid:pk>/", AdminSubscriptionDetailAPIView.as_view(), name="admin-subscription-detail"),
    path("admin/subscriptions/<uuid:pk>/activate/", AdminSubscriptionActivateAPIView.as_view(), name="admin-subscription-activate"),
    path("admin/subscriptions/<uuid:pk>/expire/", AdminSubscriptionExpireAPIView.as_view(), name="admin-subscription-expire"),

    # --- SubscriptionRequest (routes/subscription_request_api.py) ---
    path("owner/subscription/requests/", OwnerSubscriptionRequestAPIView.as_view(), name="owner-subscription-requests"),
    path("admin/subscription-requests/", AdminSubscriptionRequestListAPIView.as_view(), name="admin-subscription-request-list"),
    path("admin/subscription-requests/<uuid:pk>/approve/", AdminSubscriptionRequestApproveAPIView.as_view(), name="admin-subscription-request-approve"),
    path("admin/subscription-requests/<uuid:pk>/reject/", AdminSubscriptionRequestRejectAPIView.as_view(), name="admin-subscription-request-reject"),

    # --- PaymentLog (routes/payment_log_api.py) ---
    path("owner/payments/", OwnerPaymentLogListAPIView.as_view(), name="owner-payments"),
    path("admin/payments/", AdminPaymentLogListCreateAPIView.as_view(), name="admin-payment-list"),
]
