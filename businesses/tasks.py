import logging

from celery import shared_task

logger = logging.getLogger("businesses")


@shared_task(name="businesses.tasks.notify_new_application_task")
def notify_new_application_task(application_id):
    from businesses.models import BusinessApplication
    from common.telegram import notify_new_application

    application = (
        BusinessApplication.objects.select_related("applicant")
        .filter(pk=application_id)
        .first()
    )
    if application is None:
        logger.warning(f"notify_new_application_task: ariza topilmadi id={application_id}")
        return False
    return notify_new_application(application)
