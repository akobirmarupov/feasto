import json
import logging
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger("notifications")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT = 10


def is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_ADMIN_CHAT_ID)


def send_admin_message(text: str) -> bool:
    if not is_configured():
        return False

    payload = json.dumps({
        "chat_id": settings.TELEGRAM_ADMIN_CHAT_ID,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    request = Request(
        API_URL.format(token=settings.TELEGRAM_BOT_TOKEN),
        data=payload, headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            body = json.loads(response.read() or b"{}")
    except Exception as error:  # noqa: BLE001
        logger.warning(f"Telegram xabari yuborilmadi: {error}")
        return False

    if not body.get("ok"):
        logger.warning(f"Telegram javobi xato: {body}")
        return False
    return True


def notify_admins(text: str) -> bool:
    if not is_configured():
        return False
    try:
        from notifications.tasks import send_admin_telegram_task

        send_admin_telegram_task.delay(text)
        return True
    except Exception as error:  # noqa: BLE001
        logger.warning(f"Telegram vazifasi navbatga qo'yilmadi, to'g'ridan-to'g'ri yuboriladi: {error}")
        return send_admin_message(text)
