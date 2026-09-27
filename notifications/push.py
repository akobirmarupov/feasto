import json
import logging
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger("notifications")

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TIMEOUT = 10


def is_configured() -> bool:
    return bool(settings.FCM_SERVICE_ACCOUNT_FILE and settings.FCM_PROJECT_ID)


def _access_token() -> str:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_file(
        settings.FCM_SERVICE_ACCOUNT_FILE, scopes=[FCM_SCOPE],
    )
    credentials.refresh(google_requests.Request())
    return credentials.token


def send_to_token(token: str, *, title: str, body: str, data: dict | None = None) -> str:
    message = {
        "message": {
            "token": token,
            "notification": {"title": title[:160], "body": body[:400]},
            "data": {k: str(v) for k, v in (data or {}).items()},
        }
    }
    request = Request(
        FCM_URL.format(project=settings.FCM_PROJECT_ID),
        data=json.dumps(message).encode(),
        headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=TIMEOUT):
            return "sent"
    except HTTPError as error:
        detail = error.read().decode(errors="ignore")
        if error.code in (404, 410) or "UNREGISTERED" in detail or "INVALID_ARGUMENT" in detail:
            return "invalid_token"
        logger.warning(f"FCM xatosi ({error.code}): {detail[:200]}")
        return "error"
    except Exception as error:  # noqa: BLE001
        logger.warning(f"FCM ga ulanib bo'lmadi: {error}")
        return "error"


def send_to_user(user, *, title: str, body: str, data: dict | None = None) -> int:
    from notifications.models import Device

    if not is_configured():
        return 0

    sent = 0
    for device in Device.objects.filter(user=user, is_active=True):
        result = send_to_token(device.token, title=title, body=body, data=data)
        if result == "sent":
            sent += 1
        elif result == "invalid_token":
            device.is_active = False
            device.save(update_fields=["is_active", "updated_at"])
    return sent


def schedule_push(notification) -> bool:
    if not is_configured():
        return False
    try:
        from notifications.tasks import send_push_task

        send_push_task.delay(str(notification.pk))
        return True
    except Exception as error:  # noqa: BLE001
        logger.warning(f"Push vazifasi navbatga qo'yilmadi: {error}")
        return False
