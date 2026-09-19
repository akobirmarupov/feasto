from django.apps import AppConfig


class ContentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "content"
    verbose_name = "Kontent — banner va yangiliklar"

    def ready(self):
        from . import signals  # noqa: F401
