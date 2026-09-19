from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from common.cache import invalidate_business_cache

from .models import RestaurantMenuItem, VenueMenuItem


@receiver(post_save, sender=RestaurantMenuItem)
@receiver(post_delete, sender=RestaurantMenuItem)
@receiver(post_save, sender=VenueMenuItem)
@receiver(post_delete, sender=VenueMenuItem)
def invalidate_on_menu_change(sender, instance, **kwargs):
    invalidate_business_cache()
