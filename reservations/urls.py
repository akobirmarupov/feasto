from django.urls import path

from reservations.routes.availability_api import (
    BusinessAvailabilityAPIView,
    HallBusyDatesAPIView,
    OwnerAvailabilityDetailAPIView,
    OwnerAvailabilityGenerateAPIView,
    OwnerAvailabilityListAPIView,
    RoomBusyHoursAPIView,
)
from reservations.routes.reservation_api import (
    AdminReservationListAPIView,
    MyReservationListAPIView,
    OwnerReservationListAPIView,
    OwnerReservationStatusAPIView,
    PendingReviewAPIView,
    ReservationCancelAPIView,
    ReservationCreateAPIView,
    ReservationDetailAPIView,
)

app_name = "reservations"

urlpatterns = [
    # --- Availability (routes/availability_api.py) ---
    path("businesses/<uuid:business_id>/availability/", BusinessAvailabilityAPIView.as_view(), name="business-availability"),
    path("rooms/<uuid:room_id>/busy-hours/", RoomBusyHoursAPIView.as_view(), name="room-busy-hours"),
    path("halls/<uuid:hall_id>/busy-dates/", HallBusyDatesAPIView.as_view(), name="hall-busy-dates"),
    path("owner/availability/", OwnerAvailabilityListAPIView.as_view(), name="owner-availability-list"),
    path("owner/availability/generate/", OwnerAvailabilityGenerateAPIView.as_view(), name="owner-availability-generate"),
    path("owner/availability/<uuid:pk>/", OwnerAvailabilityDetailAPIView.as_view(), name="owner-availability-detail"),

    # --- Reservation (routes/reservation_api.py) ---
    path("reservations/", ReservationCreateAPIView.as_view(), name="reservation-create"),
    path("reservations/my/", MyReservationListAPIView.as_view(), name="reservation-my"),
    path("reservations/pending-review/", PendingReviewAPIView.as_view(), name="reservation-pending-review"),
    path("reservations/<uuid:pk>/", ReservationDetailAPIView.as_view(), name="reservation-detail"),
    path("reservations/<uuid:pk>/cancel/", ReservationCancelAPIView.as_view(), name="reservation-cancel"),
    path("owner/reservations/", OwnerReservationListAPIView.as_view(), name="owner-reservation-list"),
    path("owner/reservations/<uuid:pk>/status/", OwnerReservationStatusAPIView.as_view(), name="owner-reservation-status"),
    path("admin/reservations/", AdminReservationListAPIView.as_view(), name="admin-reservation-list"),
]
