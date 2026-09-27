import logging

from django.db import transaction
from django.db.models import Exists, OuterRef
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from account.filters import UserFilter
from account.models import User
from account.routes.helpers import BadRequest, revoke_all_tokens
from account.routes.serializers import UserAdminSerializer, UserAdminUpdateSerializer
from businesses.models import Business
from common.pagination import StandardResultsPagination
from common.permissions import IsSuperAdmin

security_logger = logging.getLogger("django.security")


def admin_user_queryset():
    return User.objects.annotate(
        has_business=Exists(Business.objects.filter(owner=OuterRef("pk")))
    ).order_by("-date_joined")


class AdminUserListAPIView(APIView):
    permission_classes = [IsSuperAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = UserFilter
    queryset = User.objects.none()
    pagination_class = StandardResultsPagination

    @extend_schema(
        responses=UserAdminSerializer(many=True),
        parameters=[
            OpenApiParameter("role", str, description="user | business | admin"),
            OpenApiParameter("search", str, description="Ism, username, telefon yoki email"),
            OpenApiParameter("is_active", bool),
            OpenApiParameter("is_confirmed", bool),
            OpenApiParameter("is_phone_verified", bool),
        ],
    )
    def get(self, request):
        queryset = UserFilter(request.GET, queryset=admin_user_queryset()).qs

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        response = paginator.get_paginated_response(UserAdminSerializer(page, many=True).data)
        response.data["total"] = User.objects.count()
        return response


class AdminUserDetailAPIView(APIView):
    permission_classes = [IsSuperAdmin]

    def get_object(self, pk):
        try:
            return admin_user_queryset().get(pk=pk)
        except User.DoesNotExist:
            raise NotFound("Foydalanuvchi topilmadi.")

    @extend_schema(responses=UserAdminSerializer)
    def get(self, request, pk):
        return Response(UserAdminSerializer(self.get_object(pk)).data)

    @extend_schema(request=UserAdminUpdateSerializer, responses=UserAdminSerializer)
    def patch(self, request, pk):
        user = self.get_object(pk)
        serializer = UserAdminUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        if not data:
            raise BadRequest(f"Tahrirlash mumkin bo'lgan maydonlar: {sorted(serializer.fields)}")
        if user.pk == request.user.pk and data.get("is_active") is False:
            raise BadRequest("O'z hisobingizni bloklay olmaysiz.")
        if user.is_superuser and not request.user.is_superuser and ("is_active" in data or "role" in data):
            raise PermissionDenied("Superuser hisobini faqat superuser o'zgartira oladi.")

        with transaction.atomic():
            serializer.save()
            if data.get("is_active") is False:
                revoke_all_tokens(user)

        security_logger.info(
            f"User updated by admin: user_id={user.id}, by={request.user.id}, fields={sorted(data)}"
        )
        return Response(UserAdminSerializer(self.get_object(pk)).data, status=status.HTTP_200_OK)
