import logging

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from account.routes.helpers import BadRequest, revoke_all_tokens
from account.routes.serializers import (
    AvatarSerializer,
    CustomTokenObtainPairSerializer,
    LogoutSerializer,
    UserSerializer,
)
from common.throttles import LoginThrottle

logger = logging.getLogger("account")
security_logger = logging.getLogger("django.security")


class LoginAPIView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        username = request.data.get("username")
        if response.status_code == status.HTTP_200_OK:
            logger.info(f"Login OK: username={username}")
        else:
            security_logger.warning(
                f"Login failed: username={username} ip={request.META.get('REMOTE_ADDR')}"
            )
        return response


class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=LogoutSerializer, responses={205: None})
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            if str(token.get("user_id")) != str(request.user.pk):
                raise TokenError("foreign token")
            token.blacklist()
        except TokenError as error:
            raise BadRequest("Token yaroqsiz yoki allaqachon bekor qilingan.") from error

        logger.info(f"Logout: user_id={request.user.id}")
        return Response({"detail": "Tizimdan chiqdingiz."}, status=status.HTTP_205_RESET_CONTENT)


class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user, context={"request": request}).data)

    @extend_schema(request=UserSerializer, responses=UserSerializer)
    def patch(self, request):
        serializer = UserSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            serializer.save()
        logger.info(f"Profile updated: user_id={request.user.id}, fields={sorted(serializer.validated_data)}")
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(responses={204: None}, description="Hisobni faolsizlantiradi va shaxsiy ma'lumotlarni anonimlashtiradi.")
    def delete(self, request):
        user = request.user
        with transaction.atomic():
            if user.avatar:
                user.avatar.delete(save=False)
            user.is_active = False
            user.full_name = "O'chirilgan foydalanuvchi"
            user.username = f"deleted_{user.pk}"
            user.email = ""
            user.phone_number = None
            user.is_phone_verified = False
            user.google_sub = None
            user.avatar = None
            user.bio = ""
            user.birth_date = None
            user.set_unusable_password()
            user.save()
            revoke_all_tokens(user)

        logger.info(f"Account deactivated: user_id={user.pk}")
        return Response(status=status.HTTP_204_NO_CONTENT)


class AvatarAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=AvatarSerializer, responses=UserSerializer)
    def post(self, request):
        serializer = AvatarSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            old = request.user.avatar
            old_name = old.name if old else None
            serializer.save()
            if old_name and old_name != request.user.avatar.name:
                old.storage.delete(old_name)

        logger.info(f"Avatar updated: user_id={request.user.id}")
        return Response(UserSerializer(request.user, context={"request": request}).data)

    @extend_schema(responses=UserSerializer)
    def delete(self, request):
        with transaction.atomic():
            if request.user.avatar:
                request.user.avatar.delete(save=False)
                request.user.avatar = None
                request.user.save(update_fields=["avatar", "updated_at"])

        logger.info(f"Avatar removed: user_id={request.user.id}")
        return Response(UserSerializer(request.user, context={"request": request}).data)
