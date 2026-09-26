
import logging

from celery import shared_task

logger = logging.getLogger("subscriptions")


@shared_task(name="subscriptions.tasks.check_expired_subscriptions_task")
def check_expired_subscriptions_task():
    from subscriptions.services import check_expired_subscriptions

    count = check_expired_subscriptions()
    logger.info(f"check_expired_subscriptions_task: {count} ta obuna muddati tugadi")
    return count


@shared_task(name="subscriptions.tasks.notify_expiring_subscriptions_task")
def notify_expiring_subscriptions_task():
    from subscriptions.services import send_expiry_reminders

    reminded = send_expiry_reminders()
    logger.info(f"notify_expiring_subscriptions_task: {reminded} ta egaga eslatma")
    return reminded
