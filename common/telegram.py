import logging

import requests
from django.conf import settings

logger = logging.getLogger("common")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SECONDS = 5


def send_telegram_message(text, chat_id=None):
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_ADMIN_CHAT_ID

    if not token or not chat_id:
        logger.debug("Telegram sozlanmagan — xabar yuborilmadi.")
        return False

    try:
        response = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning(f"Telegram xabarini yuborib bo'lmadi: {exc}")
        return False


def notify_new_application(application):
    text = (
        "🆕 <b>Yangi biznes arizasi</b>\n\n"
        f"🏢 Nomi: <b>{application.business_name}</b>\n"
        f"📋 Turi: {application.get_business_type_display()}\n"
        f"👤 Arizachi: {application.applicant.full_name} (@{application.applicant.username})\n"
        f"📞 Telefon: {application.applicant.phone_number}\n"
        f"🕐 Sana: {application.created_at:%Y-%m-%d %H:%M}"
    )
    return send_telegram_message(text)


def notify_new_reservation(reservation):
    target = reservation.room.name if reservation.room_id else (
        reservation.hall.name if reservation.hall_id else "—"
    )
    date = reservation.availability.date if reservation.availability_id else "—"
    text = (
        "📅 <b>Yangi bron so'rovi</b>\n\n"
        f"🏢 {reservation.business.name} — {target}\n"
        f"👤 {reservation.user.full_name} ({reservation.user.phone_number})\n"
        f"📆 Sana: {date}\n"
        f"👥 Mehmonlar: {reservation.guests_count}\n"
        f"💰 Depozit: {reservation.deposit_amount:,.0f} so'm".replace(",", " ")
    )
    return send_telegram_message(text)
