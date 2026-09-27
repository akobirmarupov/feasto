import logging

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardResultsPagination
from common.permissions import IsSuperAdmin
from content.filters import BannerFilter
from content.models import Banner
from content.routes.serializers import BannerAdminSerializer, BannerSerializer

logger = logging.getLogger("content")


class BannerListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BannerFilter
    queryset = Banner.objects.none()

    @extend_schema(
        responses=BannerSerializer(many=True),
        parameters=[
            OpenApiParameter("placement", str, description="hero | inline | sidebar | auth"),
            OpenApiParameter("lang", str, description="uz | ru | en"),
        ],
    )
    def get(self, request):
        queryset = Banner.objects.live().order_by("order", "-created_at")
        placement = request.GET.get("placement")
        if placement:
            queryset = queryset.filter(placement=placement)
        return Response(BannerSerializer(queryset, many=True, context={"request": request}).data)


class AdminBannerListCreateAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = BannerFilter
    queryset = Banner.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=BannerAdminSerializer(many=True))
    def get(self, request):
        queryset = BannerFilter(request.GET, queryset=Banner.objects.all().order_by("order", "-created_at")).qs
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            BannerAdminSerializer(page, many=True, context={"request": request}).data
        )

    @extend_schema(request=BannerAdminSerializer, responses={201: BannerAdminSerializer})
    def post(self, request):
        serializer = BannerAdminSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            banner = serializer.save()

        logger.info(f"Banner created: id={banner.id}, by={request.user.id}")
        return Response(
            BannerAdminSerializer(banner, context={"request": request}).data, status=status.HTTP_201_CREATED,
        )


class AdminBannerDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, pk):
        try:
            return Banner.objects.get(pk=pk)
        except Banner.DoesNotExist:
            raise NotFound("Banner topilmadi.")

    @extend_schema(responses=BannerAdminSerializer)
    def get(self, request, pk):
        return Response(BannerAdminSerializer(self.get_object(pk), context={"request": request}).data)

    @extend_schema(request=BannerAdminSerializer, responses=BannerAdminSerializer)
    def patch(self, request, pk):
        banner = self.get_object(pk)
        serializer = BannerAdminSerializer(banner, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"Banner updated: id={banner.id}, by={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        banner = self.get_object(pk)
        with transaction.atomic():
            banner_id = banner.id
            banner.delete()
        logger.info(f"Banner deleted: id={banner_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
