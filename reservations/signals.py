from django.db.models.signals import post_delete, post_init, post_save
from django.dispatch import receiver

from businesses.models import Business

from .models import BLOCKING_STATUSES, Availability, Reservation


def sync_availability_booked_status(availability_id):
    if availability_id is None:
        return
    availability = (
        Availability.objects.filter(pk=availability_id, room__isnull=True)
        .select_related("business")
        .first()
    )
    if availability is None or availability.business.business_type != Business.TYPE_VENUE:
        return

    should_be_booked = availability.reservations.filter(status__in=BLOCKING_STATUSES).exists()
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
        sync_availability_booked_status(old_id)
    sync_availability_booked_status(instance.availability_id)
    instance._old_availability_id = instance.availability_id


@receiver(post_delete, sender=Reservation)
def sync_availability_on_delete(sender, instance, **kwargs):
    sync_availability_booked_status(instance.availability_id)
