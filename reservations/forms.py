import datetime

from django import forms
from django.utils import timezone
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTimeWidget,
)

from businesses.models import Business, Room

MONTH_CHOICES = [
    (1, "Yanvar"), (2, "Fevral"), (3, "Mart"), (4, "Aprel"),
    (5, "May"), (6, "Iyun"), (7, "Iyul"), (8, "Avgust"),
    (9, "Sentyabr"), (10, "Oktyabr"), (11, "Noyabr"), (12, "Dekabr"),
]


class GenerateAvailabilityForm(forms.Form):

    business = forms.ModelChoiceField(
        queryset=Business.objects.all().order_by("name"), label="Biznes",
        widget=UnfoldAdminSelectWidget(attrs={"id": "id_business"}),
    )
    room = forms.ModelChoiceField(
        queryset=Room.objects.all(), required=False, label="Xona",
        widget=UnfoldAdminSelectWidget(attrs={"id": "id_room"}),
        help_text="Faqat restoran uchun. To'yxona uchun bo'sh qoldiriladi — "
                  "to'yxonada bir kunda bitta to'y bo'ladi, shuning uchun bo'sh "
                  "vaqt butun biznes darajasida hisoblanadi.",
    )
    start_time = forms.TimeField(
        label="Boshlanish vaqti",
        widget=UnfoldAdminTimeWidget(attrs={"id": "id_start_time"}),
    )
    end_time = forms.TimeField(
        label="Tugash vaqti",
        widget=UnfoldAdminTimeWidget(attrs={"id": "id_end_time"}),
        help_text="To'yxona uchun 00:00 — yarim tungacha (kunning oxirigacha) degani.",
    )
    year = forms.TypedChoiceField(
        label="Yil", choices=[], coerce=int,
        widget=UnfoldAdminSelectWidget(attrs={"id": "id_year"}),
    )
    months = forms.MultipleChoiceField(
        label="Oylar", choices=MONTH_CHOICES,
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        error_messages={"required": "Kamida bitta oy tanlanishi kerak."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        current_year = timezone.localdate().year
        self.fields["year"].choices = [(y, str(y)) for y in range(current_year, current_year + 3)]
        self.fields["year"].initial = current_year

        business_id = self.data.get("business") or self.initial.get("business")
        rooms = Room.objects.none()
        if business_id:
            try:
                rooms = Room.objects.filter(business_id=business_id).order_by("name")
                list(rooms[:1])
            except (TypeError, ValueError, forms.ValidationError):
                rooms = Room.objects.none()
        self.fields["room"].queryset = rooms

    def clean(self):
        cleaned_data = super().clean()
        business = cleaned_data.get("business")
        room = cleaned_data.get("room")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if not business:
            return cleaned_data

        is_restaurant = business.business_type == Business.TYPE_RESTAURANT
        if is_restaurant and not room:
            self.add_error("room", "Restoran uchun xona tanlanishi shart.")
        elif not is_restaurant and room:
            self.add_error("room", "To'yxona uchun xona tanlanmaydi — bo'sh qoldiring.")

        if start_time and end_time:
            is_midnight_end = (not is_restaurant) and end_time == datetime.time(0, 0)
            if not is_midnight_end and end_time <= start_time:
                self.add_error("end_time", "Tugash vaqti boshlanishdan keyin bo'lishi kerak.")

        return cleaned_data

    def get_month_dates(self):
        year = self.cleaned_data["year"]
        months = sorted(int(m) for m in self.cleaned_data["months"])
        return [datetime.date(year, m, 1) for m in months]
