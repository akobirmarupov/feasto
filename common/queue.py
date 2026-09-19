import logging

logger = logging.getLogger("common")


def enqueue(task, *args, **kwargs):
    try:
        task.delay(*args, **kwargs)
        return True
    except Exception as exc:
        logger.warning(f"Vazifani navbatga qo'yib bo'lmadi ({task.name}): {exc}")
        return False
