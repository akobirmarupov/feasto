import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("subscriptions")


@shared_task(name="subscriptions.tasks.check_expired_subscriptions_task")
def check_expired_subscriptions_task():
    from subscriptions.services import check_expired_subscriptions

    count = check_expired_subscriptions()
    logger.info(f"check_expired_subscriptions_task: {count} ta obuna muddati tugadi")
    return count


@shared_task(name="subscriptions.tasks.notify_expiring_subscriptions_task")
def notify_expiring_subscriptions_task():
    from common.telegram import send_telegram_message
    from subscriptions.models import Subscription
    from subscriptions.services import send_expiry_reminders

    reminded = send_expiry_reminders()
    logger.info(f"notify_expiring_subscriptions_task: {reminded} ta egaga eslatma")

    now = timezone.now()
    deadline = now + timedelta(days=3)

    expiring = Subscription.objects.filter(
        status__in=["trial", "active"]
    ).select_related("business", "business__owner")

    notified = 0
    for subscription in expiring:
        ends_at = (
            subscription.subscription_ends_at
            if subscription.status == "active"
            else subscription.trial_ends_at
        )
        if ends_at is None or not (now < ends_at <= deadline):
            continue

        days_left = (ends_at - now).days
        send_telegram_message(
            f"⏳ <b>Obuna tugayapti</b>\n\n"
            f"🏢 {subscription.business.name}\n"
            f"👤 {subscription.business.owner.full_name} "
            f"({subscription.business.owner.phone_number})\n"
            f"📅 Qolgan: {days_left} kun"
        )
        notified += 1

    logger.info(f"notify_expiring_subscriptions_task: adminga {notified} ta xabar")
    return {"owners": reminded, "admin": notified}
