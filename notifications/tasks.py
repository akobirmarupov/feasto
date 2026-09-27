import logging

from celery import shared_task

logger = logging.getLogger("notifications")


@shared_task(name="notifications.tasks.send_admin_telegram_task", bind=True, max_retries=3, default_retry_delay=30)
def send_admin_telegram_task(self, text):
    from notifications.telegram import send_admin_message

    if not send_admin_message(text):
        raise self.retry()
    return True


@shared_task(name="notifications.tasks.send_push_task")
def send_push_task(notification_id):
    from notifications.models import Notification
    from notifications.push import send_to_user

    notification = Notification.objects.select_related("user").filter(pk=notification_id).first()
    if notification is None:
        return 0
    return send_to_user(
        notification.user,
        title=notification.title, body=notification.body,
        data={"kind": notification.kind, "link_url": notification.link_url, "id": str(notification.pk)},
    )
