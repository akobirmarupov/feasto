from django.urls import path

from businesses.routes.business_api import (
    AdminBusinessCreateAPIView,
    AdminBusinessDetailAPIView,
    AdminBusinessListAPIView,
    AdminBusinessToggleBlockAPIView,
    AdminOverviewAPIView,
    BusinessDetailAPIView,
    BusinessListAPIView,
    OwnerBusinessAPIView,
    OwnerOverviewAPIView,
)
from businesses.routes.business_application_api import (
    AdminApplicationApproveAPIView,
    AdminApplicationListAPIView,
    AdminApplicationRejectAPIView,
    BusinessApplicationCreateAPIView,
    MyBusinessApplicationAPIView,
)
from businesses.routes.business_photo_api import (
    BusinessPhotoListAPIView,
    OwnerBusinessPhotoDetailAPIView,
    OwnerBusinessPhotoListCreateAPIView,
    ShowcasePhotoListAPIView,
)
from businesses.routes.favorite_api import FavoriteDetailAPIView, FavoriteListAPIView
from businesses.routes.hall_api import (
    BusinessHallListAPIView,
    OwnerHallDetailAPIView,
    OwnerHallListCreateAPIView,
)
from businesses.routes.room_api import (
    BusinessRoomListAPIView,
    OwnerRoomDetailAPIView,
    OwnerRoomListCreateAPIView,
)
from businesses.routes.venue_pricing_api import (
    BusinessPricingListAPIView,
    OwnerVenuePricingAPIView,
)

app_name = "businesses"

urlpatterns = [
    # --- Business (routes/business_api.py) ---
    path("businesses/", BusinessListAPIView.as_view(), name="business-list"),
    path("businesses/<uuid:pk>/", BusinessDetailAPIView.as_view(), name="business-detail"),
    path("owner/overview/", OwnerOverviewAPIView.as_view(), name="owner-overview"),
    path("owner/business/", OwnerBusinessAPIView.as_view(), name="owner-business"),
    path("admin/overview/", AdminOverviewAPIView.as_view(), name="admin-overview"),
    path("admin/businesses/", AdminBusinessListAPIView.as_view(), name="admin-business-list"),
    path("admin/businesses/create/", AdminBusinessCreateAPIView.as_view(), name="admin-business-create"),
    path("admin/businesses/<uuid:pk>/", AdminBusinessDetailAPIView.as_view(), name="admin-business-detail"),
    path("admin/businesses/<uuid:pk>/toggle-block/", AdminBusinessToggleBlockAPIView.as_view(), name="admin-business-toggle-block"),

    # --- BusinessApplication (routes/business_application_api.py) ---
    path("business-applications/", BusinessApplicationCreateAPIView.as_view(), name="application-create"),
    path("business-applications/my/", MyBusinessApplicationAPIView.as_view(), name="application-my"),
    path("admin/applications/", AdminApplicationListAPIView.as_view(), name="admin-application-list"),
    path("admin/applications/<uuid:pk>/approve/", AdminApplicationApproveAPIView.as_view(), name="admin-application-approve"),
    path("admin/applications/<uuid:pk>/reject/", AdminApplicationRejectAPIView.as_view(), name="admin-application-reject"),

    # --- BusinessPhoto (routes/business_photo_api.py) ---
    path("businesses/<uuid:business_id>/photos/", BusinessPhotoListAPIView.as_view(), name="business-photos"),
    path("showcase/photos/", ShowcasePhotoListAPIView.as_view(), name="showcase-photos"),
    path("owner/photos/", OwnerBusinessPhotoListCreateAPIView.as_view(), name="owner-photo-list"),
    path("owner/photos/<uuid:pk>/", OwnerBusinessPhotoDetailAPIView.as_view(), name="owner-photo-detail"),

    # --- Room (routes/room_api.py) ---
    path("businesses/<uuid:business_id>/rooms/", BusinessRoomListAPIView.as_view(), name="business-rooms"),
    path("owner/rooms/", OwnerRoomListCreateAPIView.as_view(), name="owner-room-list"),
    path("owner/rooms/<uuid:pk>/", OwnerRoomDetailAPIView.as_view(), name="owner-room-detail"),

    # --- Hall (routes/hall_api.py) ---
    path("businesses/<uuid:business_id>/halls/", BusinessHallListAPIView.as_view(), name="business-halls"),
    path("owner/halls/", OwnerHallListCreateAPIView.as_view(), name="owner-hall-list"),
    path("owner/halls/<uuid:pk>/", OwnerHallDetailAPIView.as_view(), name="owner-hall-detail"),

    # --- Favorite (routes/favorite_api.py) ---
    path("favorites/", FavoriteListAPIView.as_view(), name="favorite-list"),
    path("favorites/<uuid:business_id>/", FavoriteDetailAPIView.as_view(), name="favorite-detail"),

    # --- VenuePricing (routes/venue_pricing_api.py) ---
    path("businesses/<uuid:business_id>/pricing/", BusinessPricingListAPIView.as_view(), name="business-pricing"),
    path("owner/pricing/", OwnerVenuePricingAPIView.as_view(), name="owner-pricing"),
]
