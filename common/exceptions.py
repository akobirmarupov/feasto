import logging

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, IntegrityError
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from common.middleware import get_request_id

logger = logging.getLogger("common")


ERROR_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "too_many_requests",
    500: "server_error",
}


def api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        from rest_framework.exceptions import ValidationError as DRFValidationError
        exc = DRFValidationError(detail=getattr(exc, "message_dict", exc.messages))
    elif isinstance(exc, Http404):
        from rest_framework.exceptions import NotFound
        exc = NotFound()
    elif isinstance(exc, PermissionDenied):
        from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
        exc = DRFPermissionDenied()

    response = drf_exception_handler(exc, context)
    view_name = context.get("view").__class__.__name__ if context.get("view") else "-"

    if response is None:
        if isinstance(exc, (IntegrityError, DatabaseError)):
            logger.exception(f"Database error in {view_name}: {exc}")
            return _build(
                status.HTTP_409_CONFLICT,
                "Ma'lumotlar bazasida ziddiyat yuz berdi. Qaytadan urinib ko'ring.",
            )

        logger.exception(f"Unhandled exception in {view_name}: {exc}")
        return _build(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Kutilmagan xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.",
        )

    detail = response.data
    message, details = _extract(detail)

    if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        logger.warning(f"Throttled: view={view_name} detail={message}")
    elif response.status_code in (401, 403):
        logging.getLogger("django.security").warning(
            f"Access denied: view={view_name} status={response.status_code} detail={message}"
        )

    response.data = _payload(response.status_code, message, details)
    return response


def _extract(detail):
    if isinstance(detail, dict):
        if "detail" in detail and len(detail) == 1:
            return str(detail["detail"]), None
        first_key = next(iter(detail))
        first_value = detail[first_key]
        if isinstance(first_value, (list, tuple)) and first_value:
            first_value = first_value[0]
        return str(first_value), detail
    if isinstance(detail, (list, tuple)) and detail:
        return str(detail[0]), {"errors": list(detail)}
    return str(detail), None


def _payload(status_code, message, details=None):
    payload = {
        "success": False,
        "error": {
            "code": ERROR_CODES.get(status_code, "error"),
            "message": message,
        },
        "request_id": get_request_id(),
    }
    if details:
        payload["error"]["details"] = details
    return payload


def _build(status_code, message, details=None):
    return Response(_payload(status_code, message, details), status=status_code)
