from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'common'

    def ready(self):
        from django.db.models.signals import pre_save

        from common.images import shrink_uploaded_images

        pre_save.connect(shrink_uploaded_images, dispatch_uid="common.shrink_uploaded_images")
