import logging

from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from businesses.models import BusinessApplication
from notifications import links
from notifications.models import Notification
from notifications.services import notify, notify_many
from notifications.telegram import notify_admins
from reservations.models import Reservation
from reviews.models import Review

logger = logging.getLogger("notifications")

STATUS_TEXT = {
    "pending": "kutilmoqda",
    "confirmed": "tasdiqlandi",
    "cancelled": "bekor qilindi",
    "completed": "yakunlandi",
}
STATUS_LEVEL = {
    "confirmed": Notification.LEVEL_SUCCESS,
    "completed": Notification.LEVEL_SUCCESS,
    "cancelled": Notification.LEVEL_WARNING,
}


def _when(reservation):
    availability = reservation.availability
    return availability.date if availability else "—"


def _safe(handler):
    def wrapper(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except Exception:
            logger.exception("Bildirishnoma yaratishda xatolik")
            return None
    wrapper.__name__ = handler.__name__
    return wrapper


@receiver(post_init, sender=Reservation)
def remember_reservation_status(sender, instance, **kwargs):
    instance._old_status = instance.status


@receiver(post_save, sender=Reservation)
@_safe
def notify_on_reservation(sender, instance, created, **kwargs):
    if created:
        notify(
            instance.business.owner,
            kind=Notification.KIND_RESERVATION,
            title="Yangi bron so'rovi",
            body=f"{instance.user.full_name or instance.user.username} — "
                 f"{_when(instance)} · {instance.guests_count} kishi",
            link_url=links.OWNER_RESERVATIONS,
            level=Notification.LEVEL_INFO,
        )
        notify_admins(
            f"<b>Yangi bron</b>\n{instance.business.name} · {_when(instance)} · "
            f"{instance.guests_count} kishi\nMijoz: {instance.user.full_name or instance.user.username} "
            f"{instance.user.phone_number or ''}\nDepozit: {instance.deposit_amount} so'm"
        )
        return

    old = getattr(instance, "_old_status", None)
    if old is None or old == instance.status:
        return

    notify(
        instance.user,
        kind=Notification.KIND_RESERVATION,
        title=f"Broningiz {STATUS_TEXT.get(instance.status, instance.status)}",
        body=f"{instance.business.name} · {_when(instance)}",
        link_url=links.CUSTOMER_RESERVATIONS,
        level=STATUS_LEVEL.get(instance.status, Notification.LEVEL_INFO),
    )
    instance._old_status = instance.status


@receiver(post_init, sender=BusinessApplication)
def remember_application_status(sender, instance, **kwargs):
    instance._old_status = instance.status


@receiver(post_save, sender=BusinessApplication)
@_safe
def notify_on_application(sender, instance, created, **kwargs):
    from django.contrib.auth import get_user_model

    if created:
        notify(
            instance.applicant,
            kind=Notification.KIND_APPLICATION,
            title="Arizangiz qabul qilindi",
            body=f"{instance.business_name} — administrator ko'rib chiqmoqda.",
            link_url=links.CUSTOMER_APPLICATION,
        )
        staff = get_user_model().objects.filter(is_staff=True, is_active=True)
        notify_many(
            list(staff),
            kind=Notification.KIND_APPLICATION,
            title="Yangi biznes arizasi",
            body=f"{instance.business_name} ({instance.get_business_type_display()})",
            link_url=links.ADMIN_APPLICATIONS,
        )
        applicant = instance.applicant
        plan = f"{instance.plan.duration_label}, {instance.plan.price:,.0f} so'm".replace(",", " ") if instance.plan_id else "bepul sinov"
        notify_admins(
            f"<b>Yangi biznes arizasi</b>\n{instance.business_name} ({instance.get_business_type_display()})\n"
            f"Tarif: {plan}\nArizachi: {applicant.full_name} @{applicant.username} {applicant.phone_number or ''}"
        )
        return

    old = getattr(instance, "_old_status", None)
    if old is None or old == instance.status:
        return

    if instance.status == BusinessApplication.STATUS_APPROVED:
        notify(
            instance.applicant,
            kind=Notification.KIND_APPLICATION,
            title="Arizangiz tasdiqlandi 🎉",
            body=f"{instance.business_name} endi platformada. Panelga o'ting.",
            link_url=links.OWNER_HOME,
            level=Notification.LEVEL_SUCCESS,
        )
    elif instance.status == BusinessApplication.STATUS_REJECTED:
        notify(
            instance.applicant,
            kind=Notification.KIND_APPLICATION,
            title="Ariza rad etildi",
            body=f"{instance.business_name} — batafsil ma'lumot uchun administratorga yozing.",
            link_url=links.CUSTOMER_APPLICATION,
            level=Notification.LEVEL_WARNING,
        )
    instance._old_status = instance.status


@receiver(post_save, sender=Review)
@_safe
def notify_on_review(sender, instance, created, **kwargs):
    if not created:
        return
    notify(
        instance.business.owner,
        kind=Notification.KIND_REVIEW,
        title=f"Yangi sharh — {instance.rating}★",
        body=(instance.comment or "")[:180] or "Mijoz baho qoldirdi.",
        link_url=links.OWNER_REVIEWS,
        level=Notification.LEVEL_SUCCESS if instance.rating >= 4 else Notification.LEVEL_WARNING,
    )
