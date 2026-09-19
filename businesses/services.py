import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from businesses.models import Business, BusinessApplication
from businesses.tasks import notify_new_application_task
from common.queue import enqueue

logger = logging.getLogger(__name__)


class TrialNotAvailable(Exception):
    """Bepul sinov allaqachon ishlatilgan — ariza qabul qilinmaydi."""


class BusinessLimitReached(Exception):
    """Bitta hisobda bitta biznes — ikkinchisiga ariza qabul qilinmaydi."""


@transaction.atomic
def submit_application(*, applicant, business_type, business_name, plan=None):
    existing = applicant.businesses.select_related("application").first()
    reapply_to = None
    if existing is not None:
        prior = getattr(existing, "application", None)
        if prior is not None and prior.status == BusinessApplication.STATUS_REJECTED:
            reapply_to = existing
        else:
            raise BusinessLimitReached(
                f"Sizda allaqachon biznes bor — «{existing.name}». "
                "Bitta hisobda faqat bitta joy ochish mumkin. Ikkinchi joy uchun "
                "alohida hisob oching."
            )

    if plan is None and applicant.has_used_trial:
        raise TrialNotAvailable(
            "Bepul sinovdan allaqachon foydalangansiz. Davom ettirish uchun "
            "pullik tariflardan birini tanlang."
        )
    if plan is not None and plan.business_type != business_type:
        raise ValueError("Tanlangan tarif biznes turiga mos emas.")
    application = BusinessApplication.objects.create(
        applicant=applicant,
        business_type=business_type,
        business_name=business_name,
        plan=plan,
        status="pending_payment",
    )

    if reapply_to is not None:
        business = reapply_to
        business.application = application
        business.name = business_name
        business.business_type = business_type
        business.is_visible = False
        business.save(
            update_fields=["application", "name", "business_type", "is_visible"]
        )
    else:
        business = Business.objects.create(
            owner=applicant,
            application=application,
            name=business_name,
            business_type=business_type,
            address="",
            latitude=0,
            longitude=0,
            telegram_username="",
            is_visible=False,
        )
    subscription = None

    logger.info(
        f"Business application submitted: application_id={application.id}, "
        f"business_id={business.id}, user_id={applicant.id}, type={business_type} "
        f"(sinov hali boshlanmadi — admin tasdig'i kutilmoqda)"
    )
    transaction.on_commit(
        lambda: enqueue(notify_new_application_task, str(application.id))
    )
    return application, business, subscription


@transaction.atomic
def approve_application(*, application, approved_by):
    from subscriptions.services import start_paid, start_trial

    applicant = application.applicant
    business = getattr(application, "business", None)
    subscription = getattr(business, "subscription", None) if business is not None else None
    already_subscribed = subscription is not None and subscription.status in ("trial", "active")

    # Tarifsiz (bepul sinov) ariza, lekin sinov ishlatib bo'lingan — tasdiqlasak
    # joy ommaga chiqadi-yu, obunasiz qoladi. Shuning uchun hech narsani
    # o'zgartirmasdan to'xtatamiz: admin arizaga tarif biriktirishi kerak.
    if application.plan is None and applicant.has_used_trial and not already_subscribed:
        raise TrialNotAvailable(
            "Bu foydalanuvchi bepul sinovni ishlatib bo'lgan. Arizaga pullik "
            "tarif biriktiring, so'ng qayta tasdiqlang."
        )

    application.status = "approved"
    application.approved_at = timezone.now()
    application.approved_by = approved_by
    application.save(update_fields=["status", "approved_at", "approved_by"])

    if not applicant.is_staff and applicant.role != "business":
        applicant.role = "business"
        applicant.save(update_fields=["role"])

    if business is not None:
        business.is_visible = True
        business.save(update_fields=["is_visible"])

        if application.plan is not None:
            start_paid(
                business=business, plan=application.plan, approved_by=approved_by,
            )
        elif not already_subscribed:
            start_trial(business=business)

    logger.info(
        f"Application approved: application_id={application.id}, by={approved_by.id}"
    )
    return application


@transaction.atomic
def reject_application(*, application, rejected_by):
    application.status = "rejected"
    application.approved_by = rejected_by
    application.save(update_fields=["status", "approved_by"])

    _demote_if_no_approved_business(application.applicant)

    business = getattr(application, "business", None)
    if business is not None:
        business.is_visible = False
        business.save(update_fields=["is_visible"])

    logger.info(
        f"Application rejected: application_id={application.id}, by={rejected_by.id}"
    )
    return application


def _demote_if_no_approved_business(user):
    if user.is_staff or user.role != "business":
        return
    if BusinessApplication.objects.filter(
        applicant=user, status=BusinessApplication.STATUS_APPROVED
    ).exists():
        return

    user.role = "user"
    user.save(update_fields=["role"])
    logger.info(f"Owner role reset to 'user': user_id={user.pk} (ariza rad etildi)")


def trial_end_date():
    from common.models import PlatformSettings

    return timezone.now() + timedelta(days=PlatformSettings.get_solo().trial_days)
