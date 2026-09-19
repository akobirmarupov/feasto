# Celery ilovasi Django bilan birga yuklanishi shart — aks holda
# `task.delay()` web jarayonida sozlanmagan default ilovaga tushadi.
from .celery import app as celery_app

__all__ = ("celery_app",)
