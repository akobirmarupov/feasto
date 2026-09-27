import logging
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from account.routes.helpers import BLOCKED_MESSAGE, BadRequest, issue_tokens
from account.routes.serializers import GoogleAuthSerializer, build_user_payload
from account.services import (
    GoogleAuthError,
    build_auth_url,
    exchange_code,
    get_or_create_google_user,
    verify_google_token,
)
from common.throttles import LoginThrottle

logger = logging.getLogger("account")
security_logger = logging.getLogger("django.security")


def google_redirect_uri(request):
    return request.build_absolute_uri("/api/auth/google/callback/")


def frontend_login_url():
    return getattr(settings, "FRONTEND_LOGIN_URL", "") or "/kirish/"


class GoogleStartAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(
        responses={302: None},
        parameters=[OpenApiParameter("next", str, description="Kirishdan keyin qaytiladigan ichki manzil")],
        description="Foydalanuvchini Google kirish sahifasiga yo'naltiradi.",
    )
    def get(self, request):
        request.session.cycle_key()

        state = secrets.token_urlsafe(24)
        request.session["google_state"] = state
        next_url = request.GET.get("next") or "/"
        request.session["google_next"] = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"

        try:
            url = build_auth_url(redirect_uri=google_redirect_uri(request), state=state)
        except GoogleAuthError as error:
            raise BadRequest(str(error)) from error
        return HttpResponseRedirect(url)


class GoogleCallbackAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        responses={302: None},
        description="Google shu yerga qaytaradi; tokenlar frontend kirish sahifasiga URL fragmenti bilan uzatiladi.",
    )
    def get(self, request):
        error = request.GET.get("error")
        if error:
            return self._back(request, error="cancelled" if error == "access_denied" else error)

        state = request.GET.get("state")
        expected = request.session.pop("google_state", None)
        if not state or state != expected:
            security_logger.warning("Google callback: state mos kelmadi")
            return self._back(request, error="state")

        code = request.GET.get("code")
        if not code:
            return self._back(request, error="nocode")

        try:
            id_token_value = exchange_code(code=code, redirect_uri=google_redirect_uri(request))
            payload = verify_google_token(id_token_value)
            user, created = get_or_create_google_user(payload)
        except GoogleAuthError as exc:
            logger.warning(f"Google callback xatosi: {exc}")
            return self._back(request, error="google")

        if not user.is_active:
            security_logger.warning(f"Bloklangan hisob Google orqali urindi: user_id={user.id}")
            return self._back(request, error="blocked")

        tokens = issue_tokens(user)
        logger.info(f"Google login OK: user_id={user.id}, yangi={created}")

        fragment = urlencode({
            **tokens,
            "created": "1" if created else "0",
            "next": request.session.pop("google_next", None) or "/",
        })
        return HttpResponseRedirect(f"{frontend_login_url()}#{fragment}")

    def _back(self, request, *, error):
        request.session.pop("google_next", None)
        return HttpResponseRedirect(f"{frontend_login_url()}?google_error={error}")


class GoogleAuthAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(
        request=GoogleAuthSerializer,
        responses={200: None},
        description="Google `id_token` ni tekshirib, access/refresh token qaytaradi. Hisob bo'lmasa yaratiladi.",
    )
    def post(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payload = verify_google_token(serializer.validated_data["credential"])
            user, created = get_or_create_google_user(payload)
        except GoogleAuthError as error:
            raise BadRequest(str(error)) from error

        if not user.is_active:
            security_logger.warning(f"Bloklangan hisob Google orqali urindi: user_id={user.id}")
            raise PermissionDenied(BLOCKED_MESSAGE)

        logger.info(f"Google login OK: user_id={user.id}, yangi={created}")
        return Response(
            {**issue_tokens(user), "user": build_user_payload(user, request), "created": created},
            status=status.HTTP_200_OK,
        )
