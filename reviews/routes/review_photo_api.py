import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import BadRequest
from reviews.models import Review, ReviewPhoto
from reviews.routes.serializers import ReviewPhotoSerializer

logger = logging.getLogger("reviews")

MAX_PHOTOS_PER_REVIEW = 5


class ReviewPhotoCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=ReviewPhotoSerializer, responses={201: ReviewPhotoSerializer})
    def post(self, request, review_id):
        try:
            review = Review.objects.get(pk=review_id)
        except Review.DoesNotExist:
            raise NotFound("Sharh topilmadi.")

        if review.user_id != request.user.id:
            raise PermissionDenied("Bu sharhga rasm qo'sha olmaysiz.")
        if review.photos.count() >= MAX_PHOTOS_PER_REVIEW:
            raise BadRequest(f"Bitta sharhga eng ko'pi bilan {MAX_PHOTOS_PER_REVIEW} ta rasm qo'shiladi.")

        serializer = ReviewPhotoSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            photo = serializer.save(review=review)

        logger.info(f"ReviewPhoto created: photo_id={photo.id}, review_id={review.id}")
        return Response(
            ReviewPhotoSerializer(photo, context={"request": request}).data, status=status.HTTP_201_CREATED,
        )


class ReviewPhotoDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        try:
            photo = ReviewPhoto.objects.select_related("review").get(pk=pk)
        except ReviewPhoto.DoesNotExist:
            raise NotFound("Rasm topilmadi.")

        if photo.review.user_id != request.user.id and not request.user.is_staff:
            raise PermissionDenied("Bu rasmni o'chira olmaysiz.")

        with transaction.atomic():
            photo_id = photo.id
            photo.image.delete(save=False)
            photo.delete()

        logger.info(f"ReviewPhoto deleted: photo_id={photo_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
