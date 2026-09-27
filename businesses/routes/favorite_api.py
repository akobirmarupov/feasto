import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from businesses.models import Business, Favorite
from businesses.routes.business_api import annotated_business_queryset
from businesses.routes.serializers import BusinessListSerializer
from common.pagination import StandardResultsPagination

logger = logging.getLogger("businesses")


class FavoriteListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsPagination

    @extend_schema(responses=BusinessListSerializer(many=True))
    def get(self, request):
        favorite_ids = list(
            Favorite.objects.filter(user=request.user).order_by("-created_at").values_list("business_id", flat=True)
        )
        businesses = {b.id: b for b in annotated_business_queryset().filter(id__in=favorite_ids)}
        ordered = [businesses[i] for i in favorite_ids if i in businesses]

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(ordered, request, view=self)
        response = paginator.get_paginated_response(
            BusinessListSerializer(page, many=True, context={"request": request}).data
        )
        response.data["ids"] = [str(i) for i in favorite_ids]
        return response


class FavoriteDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={201: None, 200: None})
    def post(self, request, business_id):
        if not Business.objects.filter(pk=business_id, is_visible=True).exists():
            raise NotFound("Biznes topilmadi.")

        with transaction.atomic():
            _, created = Favorite.objects.get_or_create(user=request.user, business_id=business_id)

        return Response(
            {"business": str(business_id), "is_favorite": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(responses={204: None})
    def delete(self, request, business_id):
        deleted, _ = Favorite.objects.filter(user=request.user, business_id=business_id).delete()
        if not deleted:
            raise NotFound("Sevimlilarda yo'q.")
        return Response(status=status.HTTP_204_NO_CONTENT)
