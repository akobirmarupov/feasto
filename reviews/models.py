from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from businesses.models import Business
from common.models import BaseModel
from common.validators import validate_image_file
from reservations.models import Reservation


class Review(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="reviews")
    reservation = models.OneToOneField(Reservation, on_delete=models.CASCADE, related_name="review")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)

    class Meta:
        verbose_name = "Sharh"
        verbose_name_plural = "Sharhlar"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "-created_at"], name="idx_review_business_created"),
            models.Index(fields=["user", "-created_at"], name="idx_review_user_created"),
        ]

    def __str__(self):
        return f"{self.business} — {self.rating}★"

    def clean(self):
        if not self.reservation_id:
            return
        reservation = self.reservation
        errors = {}
        if self.user_id and reservation.user_id != self.user_id:
            errors["reservation"] = "Sharh faqat foydalanuvchining o'z broni bo'yicha yoziladi."
        elif self.business_id and reservation.business_id != self.business_id:
            errors["business"] = "Bron boshqa biznesga tegishli."
        elif reservation.status != "completed":
            errors["reservation"] = "Sharh faqat yakunlangan bron bo'yicha qoldiriladi."
        if errors:
            raise ValidationError(errors)


class ReviewPhoto(BaseModel):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="review_photos/", validators=[validate_image_file])

    class Meta:
        verbose_name = "Sharh rasmi"
        verbose_name_plural = "Sharh rasmlari"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["review"], name="idx_reviewphoto_review")]

    def __str__(self):
        return f"{self.review} — rasm"
