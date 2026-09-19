import datetime
from calendar import monthrange

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from businesses.models import Business, Hall, Room
from common.models import BaseModel


class Availability(BaseModel):
    """Bitta xona (restoran) yoki bitta zal (to'yxona) uchun bir kunlik ish vaqti.

    Restoranda bir kunga bir nechta bron bo'lishi mumkin (vaqtlari kesishmasa),
    shuning uchun `is_booked` faqat to'yxona zallari uchun ishlatiladi.
    """

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="availabilities")
    room = models.ForeignKey(
        Room, on_delete=models.CASCADE, related_name="availabilities",
        null=True, blank=True,
        help_text="Faqat restoran uchun.",
    )
    hall = models.ForeignKey(
        Hall, on_delete=models.CASCADE, related_name="availabilities",
        null=True, blank=True,
        help_text="Faqat to'yxona uchun — har bir zal alohida band bo'ladi.",
    )
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_booked = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "Bo'sh vaqt"
        verbose_name_plural = "Bo'sh vaqtlar"
        ordering = ["date", "start_time"]
        indexes = [
            # Mijoz "shu kunga bo'sh joyi bor" deb qidirganda ishlaydigan indeks.
            models.Index(fields=["business", "date", "is_booked"], name="idx_avail_biz_date_booked"),
            models.Index(fields=["room", "date"], name="idx_avail_room_date"),
            models.Index(fields=["hall", "date"], name="idx_avail_hall_date"),
            models.Index(fields=["date", "is_booked"], name="idx_avail_date_booked"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["room", "date", "start_time"],
                condition=Q(room__isnull=False),
                name="uniq_room_date_start_time",
            ),
            models.UniqueConstraint(
                fields=["hall", "date", "start_time"],
                condition=Q(hall__isnull=False),
                name="uniq_hall_date_start_time",
            ),
            # Har bir yozuv yo xonaga, yo zalga tegishli — ikkalasiga ham, hech
            # qaysisiga ham emas.
            models.CheckConstraint(
                condition=(
                    Q(room__isnull=False, hall__isnull=True)
                    | Q(room__isnull=True, hall__isnull=False)
                ),
                name="chk_avail_room_xor_hall",
            ),
        ]

    def __str__(self):
        target = self.room or self.hall or self.business
        name = getattr(target, "name", str(target))
        return f"{name} — {self.date} ({self.start_time:%H:%M}-{self.end_time:%H:%M})"

    @property
    def ends_at_midnight(self) -> bool:
        return self.end_time == datetime.time(0, 0)

    def clean(self):
        if not self.business_id:
            return
        errors = check_target(
            self.business, room=self.room if self.room_id else None,
            hall=self.hall if self.hall_id else None,
        )
        if errors:
            raise ValidationError(errors)
        if self.start_time and self.end_time and not self.ends_at_midnight and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "Tugash vaqti boshlanishdan keyin bo'lishi kerak (00:00 — yarim tun)."})

    @classmethod
    def generate_for_months(
        cls, *, business: Business, start_time, end_time, months: list[datetime.date],
        room: Room | None = None, halls=None,
    ):
        """Tanlangan oylarning har bir kuni uchun bo'sh vaqt yaratadi.

        Restoranda bitta xona, to'yxonada tanlangan zallar (bo'sh bo'lsa —
        barcha zallar) uchun. Mavjud kunlar o'tkazib yuboriladi.
        """
        if business.business_type == Business.TYPE_RESTAURANT:
            errors = check_target(business, room=room, hall=None)
            if errors:
                raise ValidationError(errors)
            targets = [("room", room)]
        else:
            halls = list(halls) if halls else list(business.halls.all())
            if not halls:
                raise ValidationError("Bu to'yxonada hali zal yo'q — avval zal qo'shing.")
            for hall in halls:
                errors = check_target(business, room=None, hall=hall)
                if errors:
                    raise ValidationError(errors)
            targets = [("hall", hall) for hall in halls]

        all_dates = [
            datetime.date(month.year, month.month, day)
            for month in months
            for day in range(1, monthrange(month.year, month.month)[1] + 1)
        ]

        to_create = []
        for field, target in targets:
            existing = set(
                cls.objects.filter(**{field: target}, date__in=all_dates)
                .values_list("date", flat=True)
            )
            to_create.extend(
                cls(
                    business=business, date=day,
                    start_time=start_time, end_time=end_time,
                    is_booked=False, **{field: target},
                )
                for day in all_dates
                if day not in existing
            )

        created = cls.objects.bulk_create(to_create)
        skipped = len(all_dates) * len(targets) - len(created)
        return len(created), skipped


def check_target(business, *, room, hall) -> dict:
    """Restoran — faqat xona, to'yxona — faqat zal, ikkalasi ham shu biznesniki."""
    if business.business_type == Business.TYPE_RESTAURANT:
        if room is None:
            return {"room": "Restoran uchun xona tanlanishi shart."}
        if hall is not None:
            return {"hall": "Restoran uchun zal tanlanmaydi — bo'sh qoldiring."}
    else:
        if hall is None:
            return {"hall": "To'yxona uchun zal tanlanishi shart."}
        if room is not None:
            return {"room": "To'yxona uchun xona tanlanmaydi — bo'sh qoldiring."}
    if room is not None and room.business_id != business.id:
        return {"room": "Bu xona ushbu biznesga tegishli emas."}
    if hall is not None and hall.business_id != business.id:
        return {"hall": "Bu zal ushbu biznesga tegishli emas."}
    return {}


CANCEL_FALLBACK_WINDOW = datetime.timedelta(hours=1)


ACTIVE_STATUSES = ("pending", "confirmed")


class Reservation(BaseModel):
    STATUS_CHOICES = (
        ("pending", "Kutilmoqda"),
        ("confirmed", "Tasdiqlangan"),
        ("cancelled", "Bekor qilingan"),
        ("completed", "Yakunlangan"),
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reservations")
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="reservations")
    room = models.ForeignKey(
        Room, on_delete=models.CASCADE, related_name="reservations", null=True, blank=True,
        help_text="Faqat restoran broni uchun.",
    )
    hall = models.ForeignKey(
        Hall, on_delete=models.CASCADE, related_name="reservations", null=True, blank=True,
        help_text="Faqat to'yxona broni uchun — qaysi zal band qilingani.",
    )
    availability = models.ForeignKey(
        Availability, on_delete=models.CASCADE, related_name="reservations",
        null=True, blank=True,
        help_text="Bron tegishli bo'lgan kunlik bo'sh vaqt yozuvi.",
    )

    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    guests_count = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    special_request = models.TextField(blank=True)

    dish_count = models.PositiveSmallIntegerField(null=True, blank=True)
    price_per_person = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    day_rent_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        verbose_name="Bir kunlik ijara",
    )
    total_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)

    selected_menu = models.JSONField(default=list, blank=True)

    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="pending", db_index=True)

    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name="Tasdiqlangan vaqti")

    class Meta:
        verbose_name = "Bron"
        verbose_name_plural = "Bronlar"
        ordering = ["-created_at"]
        constraints = [
            # Bitta zalning bitta kuniga faqat bitta faol (bekor qilinmagan) bron.
            # Ikki so'rov bir vaqtda kelsa ham baza ikkinchisini qabul qilmaydi.
            models.UniqueConstraint(
                fields=["availability"],
                condition=Q(hall__isnull=False) & ~Q(status="cancelled"),
                name="uniq_active_hall_reservation",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "status", "-created_at"], name="idx_resv_biz_status_created"),
            models.Index(fields=["user", "-created_at"], name="idx_resv_user_created"),
            models.Index(fields=["room", "availability", "status"], name="idx_resv_room_avail_status"),
            models.Index(fields=["hall", "availability", "status"], name="idx_resv_hall_avail_status"),
        ]

    def __str__(self):
        return f"{self.user} — {self.business} ({self.status})"

    def save(self, *args, **kwargs):
        if self.status == "confirmed" and self.confirmed_at is None:
            self.confirmed_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"confirmed_at"}
        super().save(*args, **kwargs)

    def event_starts_at(self):
        availability = self.availability
        if availability is None:
            return None

        start = self.start_time or availability.start_time or datetime.time(0, 0)
        naive = datetime.datetime.combine(availability.date, start)
        if timezone.is_naive(naive):
            return timezone.make_aware(naive, timezone.get_current_timezone())
        return naive

    def cancel_deadline(self):
        event_at = self.event_starts_at()
        if event_at is None:
            return self.created_at + CANCEL_FALLBACK_WINDOW if self.created_at else None

        if self.created_at is None:
            return event_at

        return self.created_at + (event_at - self.created_at) / 2

    def cancel_check(self):
        if self.status in ("cancelled", "completed"):
            return False, f'Bu bron allaqachon "{self.get_status_display()}" holatida.'

        deadline = self.cancel_deadline()
        if deadline is None:
            return True, ""

        if timezone.now() > deadline:
            return False, (
                "Bekor qilish muddati tugagan — u "
                f"{timezone.localtime(deadline).strftime('%d.%m.%Y %H:%M')} da yopilgan. "
                "Bekor qilish uchun bron qilingan payt bilan tadbir orasidagi "
                "vaqtning teng yarmi beriladi. Endi joy egasi bilan bevosita "
                "bog'laning."
            )
        return True, ""

    def clean(self):
        if not self.business_id:
            return
        room = self.room if self.room_id else None
        hall = self.hall if self.hall_id else None

        errors = check_target(self.business, room=room, hall=hall)
        if errors:
            raise ValidationError(errors)

        place = room or hall
        capacity = room.capacity if room else hall.people
        if self.guests_count and self.guests_count > capacity:
            raise ValidationError({
                "guests_count": f"«{place.name}» ko'pi bilan {capacity} kishiga mo'ljallangan."
            })

        if (self.start_time is None) != (self.end_time is None):
            raise ValidationError({"end_time": "Boshlanish va tugash vaqti birga kiritiladi."})

        if self.availability_id:
            self._clean_availability()

    def _clean_availability(self):
        availability = self.availability
        if availability.business_id != self.business_id:
            raise ValidationError({"availability": "Bu bo'sh vaqt ushbu biznesga tegishli emas."})
        if availability.room_id != self.room_id or availability.hall_id != self.hall_id:
            raise ValidationError({"availability": "Bu bo'sh vaqt tanlangan xona/zalga tegishli emas."})

        if self.status not in ACTIVE_STATUSES:
            return

        others = Reservation.objects.filter(
            availability=availability, status__in=ACTIVE_STATUSES
        ).exclude(pk=self.pk)

        if self.hall_id:
            if others.exists():
                raise ValidationError({"availability": "Bu zal shu kunga allaqachon band qilingan."})
            return

        # Restoran: bir kunda bir nechta bron bo'lishi mumkin, faqat vaqtlari
        # kesishmasin va ish vaqti ichida bo'lsin.
        start, end = self._time_window()
        day_end = datetime.time.max if availability.ends_at_midnight else availability.end_time
        if start < availability.start_time or end > day_end:
            raise ValidationError({
                "start_time": f"Bron vaqti ish vaqti ichida bo'lishi kerak "
                              f"({availability.start_time:%H:%M}–{availability.end_time:%H:%M})."
            })
        if end <= start:
            raise ValidationError({"end_time": "Tugash vaqti boshlanishdan keyin bo'lishi kerak."})

        for other in others:
            other_start, other_end = other._time_window()
            if start < other_end and other_start < end:
                raise ValidationError({
                    "start_time": f"Bu xona {other_start:%H:%M}–{other_end:%H:%M} oralig'ida band."
                })

    def _time_window(self):
        """Bron vaqti; kiritilmagan bo'lsa — butun ish kuni."""
        availability = self.availability
        start = self.start_time or availability.start_time
        end = self.end_time or availability.end_time
        if end == datetime.time(0, 0):
            end = datetime.time.max
        return start, end

    def resolve_deposit_amount(self):
        if self.room_id:
            return self.room.deposit_amount
        if self.hall_id:
            return self.hall.deposit_amount
        return 0