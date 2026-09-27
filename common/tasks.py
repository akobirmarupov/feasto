import logging

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger("common")

HEARTBEAT_KEY = "celery:heartbeat"
HEARTBEAT_TTL = 300


@shared_task(name="common.tasks.heartbeat_task")
def heartbeat_task():
    now = timezone.now().isoformat()
    cache.set(HEARTBEAT_KEY, now, HEARTBEAT_TTL)
    return now
