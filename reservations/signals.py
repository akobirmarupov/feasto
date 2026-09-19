from django.db.models.signals import post_delete, post_init, post_save
from django.dispatch import receiver

from .models import ACTIVE_STATUSES, Availability, Reservation


def sync_hall_availability(availability_id):
    """Zal kuni faqat unda faol bron bo'lsa band hisoblanadi.

    Restoran xonalarida bir kunda bir nechta bron bo'ladi, shuning uchun
    ularning `is_booked` belgisi o'zgarmaydi.
    """
    if availability_id is None:
        return
    availability = Availability.objects.filter(pk=availability_id, hall__isnull=False).first()
    if availability is None:
        return
    should_be_booked = availability.reservations.filter(status__in=ACTIVE_STATUSES).exists()
    if availability.is_booked != should_be_booked:
        availability.is_booked = should_be_booked
        availability.save(update_fields=["is_booked", "updated_at"])


@receiver(post_init, sender=Reservation)
def remember_availability(sender, instance, **kwargs):
    instance._old_availability_id = instance.availability_id


@receiver(post_save, sender=Reservation)
def sync_availability_on_save(sender, instance, **kwargs):
    old_id = getattr(instance, "_old_availability_id", None)
    if old_id != instance.availability_id:
        sync_hall_availability(old_id)
    sync_hall_availability(instance.availability_id)
    instance._old_availability_id = instance.availability_id


@receiver(post_delete, sender=Reservation)
def sync_availability_on_delete(sender, instance, **kwargs):
    sync_hall_availability(instance.availability_id)
