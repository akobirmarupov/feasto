from django.urls import path

from reviews.routes.review_api import (
    BusinessReviewListAPIView,
    MyReviewListAPIView,
    OwnerReviewListAPIView,
    ReviewCreateAPIView,
    ReviewDetailAPIView,
)
from reviews.routes.review_photo_api import ReviewPhotoCreateAPIView, ReviewPhotoDeleteAPIView

app_name = "reviews"

urlpatterns = [
    # --- Review (routes/review_api.py) ---
    path("businesses/<uuid:business_id>/reviews/", BusinessReviewListAPIView.as_view(), name="business-reviews"),
    path("reviews/", ReviewCreateAPIView.as_view(), name="review-create"),
    path("reviews/my/", MyReviewListAPIView.as_view(), name="review-my"),
    path("reviews/<uuid:pk>/", ReviewDetailAPIView.as_view(), name="review-detail"),
    path("owner/reviews/", OwnerReviewListAPIView.as_view(), name="owner-reviews"),

    # --- ReviewPhoto (routes/review_photo_api.py) ---
    path("reviews/<uuid:review_id>/photos/", ReviewPhotoCreateAPIView.as_view(), name="review-photo-create"),
    path("review-photos/<uuid:pk>/", ReviewPhotoDeleteAPIView.as_view(), name="review-photo-delete"),
]
