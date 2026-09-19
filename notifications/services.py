import logging

from notifications.models import Notification

logger = logging.getLogger("notifications")


def notify(user, *, title, kind=Notification.KIND_SYSTEM, body="", link_url="",
           level=Notification.LEVEL_INFO):
    if user is None:
        return None
    notification = Notification.objects.create(
        user=user, kind=kind, level=level,
        title=title[:160], body=body[:400], link_url=link_url[:300],
    )
    logger.info(f"Notification created: user_id={user.pk}, kind={kind}")
    return notification


def notify_many(users, **kwargs):
    users = [user for user in users if user is not None]
    if not users:
        return []

    rows = [
        Notification(
            user=user,
            kind=kwargs.get("kind", Notification.KIND_SYSTEM),
            level=kwargs.get("level", Notification.LEVEL_INFO),
            title=kwargs["title"][:160],
            body=kwargs.get("body", "")[:400],
            link_url=kwargs.get("link_url", "")[:300],
        )
        for user in users
    ]
    return Notification.objects.bulk_create(rows)
