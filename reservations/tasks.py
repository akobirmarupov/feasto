
import logging

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger("reservations")

BATCH_SIZE = 500


@shared_task(name="reservations.tasks.complete_past_reservations_task")
def complete_past_reservations_task():
    from notifications.models import Notification
    from notifications.services import notify
    from reservations.models import Reservation

    now = timezone.localtime()
    today = now.date()

    ended = (
        Q(availability__date__lt=today)
        | Q(availability__date=today, end_time__lte=now.time())
        | Q(
            availability__date=today,
            end_time__isnull=True,
            availability__end_time__lte=now.time(),
        )
    )

    queryset = (
        Reservation.objects.filter(status="confirmed")
        .filter(ended)
        .select_related("business", "user", "availability")
        .order_by("availability__date")[:BATCH_SIZE]
    )

    finished = []
    with transaction.atomic():
        for reservation in queryset.select_for_update(skip_locked=True):
            ends_at = reservation.event_ends_at()
            if ends_at is not None and ends_at > timezone.now():
                continue

            reservation.status = "completed"
            reservation.save(update_fields=["status"])
            finished.append(reservation)

    for reservation in finished:
        _ask_for_review(reservation, notify, Notification)

    logger.info(f"complete_past_reservations_task: {len(finished)} ta bron yakunlandi")
    return len(finished)


def _ask_for_review(reservation, notify, Notification):
    try:
        notify(
            reservation.user,
            kind=Notification.KIND_REVIEW,
            title="Tashrifingiz qanday o'tdi?",
            body=f"{reservation.business.name} — bahoingizni qoldiring, "
                 f"bu boshqa mijozlarga tanlashda yordam beradi.",
            link_url="/bronlarim/",
        )
    except Exception as error:  # noqa: BLE001
        logger.warning(f"Sharh so'rovi yuborilmadi (bron {reservation.pk}): {error}")
