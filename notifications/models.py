from django.conf import settings
from django.db import models
from django.utils import timezone

from common.models import BaseModel


class NotificationQuerySet(models.QuerySet):
    def unread(self):
        return self.filter(is_read=False)

    def for_user(self, user):
        return self.filter(user=user)


class Notification(BaseModel):
    KIND_RESERVATION = "reservation"
    KIND_APPLICATION = "application"
    KIND_SUBSCRIPTION = "subscription"
    KIND_REVIEW = "review"
    KIND_SYSTEM = "system"
    KIND_CHOICES = (
        (KIND_RESERVATION, "Bron"),
        (KIND_APPLICATION, "Ariza"),
        (KIND_SUBSCRIPTION, "Obuna"),
        (KIND_REVIEW, "Sharh"),
        (KIND_SYSTEM, "Tizim"),
    )

    LEVEL_INFO = "info"
    LEVEL_SUCCESS = "success"
    LEVEL_WARNING = "warning"
    LEVEL_CHOICES = (
        (LEVEL_INFO, "Ma'lumot"),
        (LEVEL_SUCCESS, "Muvaffaqiyat"),
        (LEVEL_WARNING, "Ogohlantirish"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default=KIND_SYSTEM, db_index=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_INFO)

    title = models.CharField(max_length=160)
    body = models.CharField(max_length=400, blank=True)
    link_url = models.CharField(
        max_length=300, blank=True,
        help_text="Bosilganda ochiladigan sahifa, masalan /bronlarim/",
    )

    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Bildirishnoma"
        verbose_name_plural = "Bildirishnomalar"
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"], name="idx_notif_user_read"),
        ]

    def __str__(self):
        return f"{self.user_id} — {self.title}"

    def mark_read(self):
        if self.is_read:
            return
        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=["is_read", "read_at"])
