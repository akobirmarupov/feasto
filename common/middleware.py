import logging
import time
import uuid
from contextvars import ContextVar

logger = logging.getLogger("common")


_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


class RequestIDFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        return True


class RequestIDMiddleware:

    SLOW_REQUEST_MS = 1000

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get("X-Request-ID", "")
        request_id = "".join(c for c in incoming if c.isalnum() or c in "-_")[:64] or uuid.uuid4().hex[:16]

        token = _request_id.set(request_id)
        request.request_id = request_id

        from common.models import _solo_memo
        memo_token = _solo_memo.set(None)

        started = time.monotonic()
        try:
            response = self.get_response(request)
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            _solo_memo.reset(memo_token)
            _request_id.reset(token)

        response["X-Request-ID"] = request_id

        if elapsed_ms > self.SLOW_REQUEST_MS:
            logger.warning(
                f"Slow request: {request.method} {request.path} "
                f"took {elapsed_ms:.0f}ms status={response.status_code}"
            )
        return response
