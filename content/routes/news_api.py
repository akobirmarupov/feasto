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
from content.filters import NewsFilter
from content.models import News
from content.routes.serializers import NewsAdminSerializer, NewsSerializer

logger = logging.getLogger("content")


class NewsListAPIView(APIView):
    permission_classes = [AllowAny]
    filter_backends = [DjangoFilterBackend]
    filterset_class = NewsFilter
    queryset = News.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(
        responses=NewsSerializer(many=True),
        parameters=[
            OpenApiParameter("category", str, description="news | tip | event | update"),
            OpenApiParameter("lang", str, description="uz | ru | en"),
        ],
    )
    def get(self, request):
        queryset = News.objects.live().order_by("-is_pinned", "order", "-created_at")
        category = request.GET.get("category")
        if category:
            queryset = queryset.filter(category=category)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(NewsSerializer(page, many=True, context={"request": request}).data)


class NewsDetailAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=NewsSerializer)
    def get(self, request, pk):
        item = News.objects.live().filter(pk=pk).first()
        if item is None:
            raise NotFound("Yangilik topilmadi.")
        return Response(NewsSerializer(item, context={"request": request}).data)


class AdminNewsListCreateAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    filter_backends = [DjangoFilterBackend]
    filterset_class = NewsFilter
    queryset = News.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(responses=NewsAdminSerializer(many=True))
    def get(self, request):
        queryset = NewsFilter(request.GET, queryset=News.objects.all().order_by("-is_pinned", "order", "-created_at")).qs
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(
            NewsAdminSerializer(page, many=True, context={"request": request}).data
        )

    @extend_schema(request=NewsAdminSerializer, responses={201: NewsAdminSerializer})
    def post(self, request):
        serializer = NewsAdminSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            item = serializer.save()

        logger.info(f"News created: id={item.id}, by={request.user.id}")
        return Response(NewsAdminSerializer(item, context={"request": request}).data, status=status.HTTP_201_CREATED)


class AdminNewsDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, pk):
        try:
            return News.objects.get(pk=pk)
        except News.DoesNotExist:
            raise NotFound("Yangilik topilmadi.")

    @extend_schema(responses=NewsAdminSerializer)
    def get(self, request, pk):
        return Response(NewsAdminSerializer(self.get_object(pk), context={"request": request}).data)

    @extend_schema(request=NewsAdminSerializer, responses=NewsAdminSerializer)
    def patch(self, request, pk):
        item = self.get_object(pk)
        serializer = NewsAdminSerializer(item, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            serializer.save()

        logger.info(f"News updated: id={item.id}, by={request.user.id}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        item = self.get_object(pk)
        with transaction.atomic():
            item_id = item.id
            item.delete()
        logger.info(f"News deleted: id={item_id}, by={request.user.id}")
        return Response(status=status.HTTP_204_NO_CONTENT)
