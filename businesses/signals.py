import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from common.cache import invalidate_business_cache

from .models import Business, BusinessPhoto, Hall, Room, VenuePricing

logger = logging.getLogger("businesses")


@receiver(post_save, sender=Business)
@receiver(post_delete, sender=Business)
@receiver(post_save, sender=Room)
@receiver(post_delete, sender=Room)
@receiver(post_save, sender=Hall)
@receiver(post_delete, sender=Hall)
@receiver(post_save, sender=BusinessPhoto)
@receiver(post_delete, sender=BusinessPhoto)
@receiver(post_save, sender=VenuePricing)
@receiver(post_delete, sender=VenuePricing)
def invalidate_on_business_change(sender, instance, **kwargs):
    invalidate_business_cache()


@receiver(post_save, sender=Business)
@receiver(post_delete, sender=Business)
def recalculate_ranks_when_the_list_changes(sender, instance, **kwargs):
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and "is_visible" not in update_fields:
        return

    from reviews.services import recalculate_ranks

    recalculate_ranks(instance.business_type)


@receiver(post_delete, sender=Business)
def reset_owner_role_when_business_is_gone(sender, instance, **kwargs):
    owner = instance.owner
    if owner.role != "business":
        return
    if Business.objects.filter(owner=owner).exists():
        return

    owner.role = "user"
    owner.save(update_fields=["role"])
    logger.info(f"Owner role reset to 'user': user_id={owner.pk} (biznesi qolmadi)")
