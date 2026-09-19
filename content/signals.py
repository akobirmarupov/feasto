from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from common.cache import invalidate_business_cache

from .models import Banner, News


@receiver(post_save, sender=Banner)
@receiver(post_delete, sender=Banner)
@receiver(post_save, sender=News)
@receiver(post_delete, sender=News)
def invalidate_on_content_change(sender, instance, **kwargs):
    invalidate_business_cache()
