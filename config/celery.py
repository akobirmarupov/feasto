import os

from celery import Celery, signals
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("feasto")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "check-expired-subscriptions": {
        "task": "subscriptions.tasks.check_expired_subscriptions_task",
        "schedule": crontab(hour=3, minute=0),
    },
    "notify-expiring-subscriptions": {
        "task": "subscriptions.tasks.notify_expiring_subscriptions_task",
        "schedule": crontab(hour=9, minute=0),
    },
    "complete-past-reservations": {
        "task": "reservations.tasks.complete_past_reservations_task",
        "schedule": crontab(minute="*/15"),
    },
}


@signals.task_prerun.connect
def reset_platform_settings_memo(**kwargs):
    from common.models import _solo_memo

    _solo_memo.set(None)
