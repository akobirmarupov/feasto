from django.contrib.auth.models import AbstractUser
from django.db import models

from common.models import Role
from common.validators import validate_image_file

from .trust import TRUST_CANCEL_PENALTY, TRUST_MAX, TRUST_MIN, TRUST_START, clamp_bits, describe
from .validators import validate_phone_number, validate_username


class User(AbstractUser):
    username = models.CharField(max_length=30, unique=True, validators=[validate_username])
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(
        max_length=13, null=True, blank=True,
        validators=[validate_phone_number],
        verbose_name="Aloqa raqami",
        help_text="Bron yoki ariza berishda so'raladi. Takrorlanishi mumkin.",)
    google_sub = models.CharField(
        max_length=64, unique=True, null=True, blank=True, editable=False,
        verbose_name="Google ID",)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.USER, db_index=True)
    avatar = models.ImageField(
        upload_to="avatars/", null=True, blank=True, validators=[validate_image_file],
        verbose_name="Profil rasmi",)
    bio = models.CharField(max_length=200, blank=True, verbose_name="Qisqacha")
    birth_date = models.DateField(null=True, blank=True, verbose_name="Tug'ilgan sana")
    preferred_language = models.CharField(
        max_length=2, choices=(("uz", "O'zbekcha"), ("ru", "Русский"), ("en", "English")),
        default="uz", verbose_name="Interfeys tili",)
    is_phone_verified = models.BooleanField(default=False, verbose_name="Raqam kiritilgan")
    is_confirmed = models.BooleanField(default=False, verbose_name="Tasdiqlangan")
    has_used_trial = models.BooleanField(
        default=False, verbose_name="Bepul sinov ishlatilgan",
        help_text="Bir marta berilgach qaytarilmaydi.",)
    trust_bits = models.PositiveSmallIntegerField(
        default=TRUST_START, db_index=True,
        verbose_name="Ishonchlilik (Bit)",
        help_text=f"{TRUST_MIN}–{TRUST_MAX}. Yangi hisob {TRUST_START} Bit bilan boshlanadi.",)
    cancelled_reservations_count = models.PositiveIntegerField(
        default=0, verbose_name="Bekor qilgan bronlari",
        help_text="Faqat foydalanuvchining O'ZI bekor qilganlari. "
                  "Joy egasi rad etgan bronlar bu yerga qo'shilmaydi.",)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = "Foydalanuvchi"
        verbose_name_plural = "Foydalanuvchilar"
        indexes = [
            models.Index(fields=["role", "is_active"], name="idx_user_role_active"),
            models.Index(fields=["phone_number"], name="idx_user_phone"),
            models.Index(fields=["trust_bits"], name="idx_user_trust_bits"),
        ]

    def __str__(self):
        return self.username

    @property
    def avatar_url(self) -> str | None:
        return self.avatar.url if self.avatar else None

    @property
    def initials(self) -> str:
        parts = (self.full_name or self.username or "?").split()
        return "".join(word[0] for word in parts[:2]).upper()

    @property
    def is_platform_admin(self) -> bool:
        return self.is_staff or self.is_superuser

    @property
    def trust(self) -> dict:
        return describe(self.trust_bits)

    def penalize_trust(self, *, points: int = TRUST_CANCEL_PENALTY, reason: str = "") -> int:
        self.trust_bits = clamp_bits(self.trust_bits - points)
        self.cancelled_reservations_count += 1
        self.save(update_fields=["trust_bits", "cancelled_reservations_count", "updated_at"])

        if reason:
            import logging
            logging.getLogger("account").info(
                f"Trust penalty: user_id={self.id}, -{points} bit, "
                f"now={self.trust_bits}, reason={reason}"
            )
        return self.trust_bits
