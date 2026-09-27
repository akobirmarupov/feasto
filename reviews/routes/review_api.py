import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import Business
from common.exceptions import BadRequest
from common.pagination import ReviewsPagination, StandardResultsPagination
from common.permissions import IsBusinessRole
from common.services import get_owner_business
from common.throttles import ReviewCreateThrottle
from reviews.filters import ReviewFilter
from reviews.models import Review
from reviews.routes.serializers import ReviewCreateSerializer, ReviewSerializer, ReviewUpdateSerializer

logger = logging.getLogger("reviews")


def _review_queryset():
    return Review.objects.select_related("user", "business").prefetch_related("photos")


def _paginate(view, request, queryset):
    queryset = ReviewFilter(request.GET, queryset=queryset.order_by("-created_at")).qs
    paginator = view.pagination_class()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(ReviewSerializer(page, many=True, context={"request": request}).data)


class BusinessReviewListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = ReviewFilter
    queryset = Review.objects.none()
    pagination_class = ReviewsPagination

    @extend_schema(responses=ReviewSerializer(many=True))
    def get(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True).exists():
            raise NotFound("Biznes topilmadi.")
        return _paginate(self, request, _review_queryset().filter(business_id=business_id))


class ReviewCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ReviewCreateThrottle]

    @extend_schema(request=ReviewCreateSerializer, responses={201: ReviewSerializer})
    def post(self, request):
        serializer = ReviewCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        reservation = serializer.validated_data["reservation"]

        with transaction.atomic():
            review = serializer.save(user=request.user, business=reservation.business)

        logger.info(
            f"Review created: review_id={review.id}, user_id={request.user.id}, "
            f"business_id={review.business_id}, rating={review.rating}"
        )
        review = _review_queryset().get(pk=review.pk)
        return Response(ReviewSerializer(review, context={"request": request}).data, status=status.HTTP_201_CREATED)


class MyReviewListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsPagination
    queryset = Review.objects.none()

    @extend_schema(responses=ReviewSerializer(many=True))
    def get(self, request):
        return _paginate(self, request, _review_queryset().filter(user=request.user))


class ReviewDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return _review_queryset().get(pk=pk)
        except Review.DoesNotExist:
            raise NotFound("Sharh topilmadi.")

    @extend_schema(responses=ReviewSerializer)
    def get(self, request, pk):
        return Response(ReviewSerializer(self.get_object(pk), context={"request": request}).data)

    @extend_schema(request=ReviewUpdateSerializer, responses=ReviewSerializer)
    def patch(self, request, pk):
        review = self.get_object(pk)
        if review.user_id != request.user.id:
            raise PermissionDenied("Bu sharhni tahrirlay olmaysiz.")

        serializer = ReviewUpdateSerializer(review, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if not serializer.validated_data:
            raise BadRequest("Tahrirlash mumkin bo'lgan maydonlar: ['comment', 'rating']")

        with transaction.atomic():
            serializer.save()

        logger.info(f"Review updated: review_id={review.id}, by={request.user.id}")
        return Response(ReviewSerializer(self.get_object(pk), context={"request": request}).data)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        review = self.get_object(pk)
        if review.user_id != request.user.id and not request.user.is_staff:
            raise PermissionDenied("Bu sharhni o'chira olmaysiz.")

        with transaction.atomic():
            review_id = review.id
            review.delete()

        logger.info(f"Review deleted: review_id={review_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)


class OwnerReviewListAPIView(APIView):
    permission_classes = [IsBusinessRole]
    filter_backends = [DjangoFilterBackend]
    filterset_class = ReviewFilter
    queryset = Review.objects.none()
    pagination_class = ReviewsPagination

    @extend_schema(responses=ReviewSerializer(many=True))
    def get(self, request):
        business = get_owner_business(request.user)
        return _paginate(self, request, _review_queryset().filter(business=business))
