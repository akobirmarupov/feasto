from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from unfold.admin import ModelAdmin

from businesses.models import Business

from .forms import MONTH_CHOICES, GenerateAvailabilityForm
from .models import Availability, Reservation


def build_businesses_data():
    """Sahifadagi JS uchun: har bir biznesning turi, xonalari va zallari."""
    businesses = Business.objects.prefetch_related("rooms", "halls").order_by("name")
    return {
        str(business.id): {
            "type": business.business_type,
            "rooms": [
                {"id": str(room.id), "name": room.name}
                for room in sorted(business.rooms.all(), key=lambda item: item.name)
            ],
            "halls": [
                {"id": str(hall.id), "name": f"{hall.name} ({hall.people} kishilik)"}
                for hall in sorted(business.halls.all(), key=lambda item: item.name)
            ],
        }
        for business in businesses
    }


@admin.register(Availability)
class AvailabilityAdmin(ModelAdmin):
    list_display = ("business", "room", "hall", "date", "start_time", "end_time", "is_booked")
    list_filter = ("is_booked", "business__business_type", "business", "date")
    list_filter_submit = True
    list_select_related = ("business", "room", "hall")
    search_fields = ("business__name", "room__name", "hall__name")
    autocomplete_fields = ("business", "room", "hall")
    date_hierarchy = "date"

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)

        # Bron formasidagi avtomatik to'ldirish: faqat tanlangan joyning bo'sh kunlari.
        filters = {
            "business_id": request.GET.get("business_id"),
            "room_id": request.GET.get("room_id"),
            "hall_id": request.GET.get("hall_id"),
        }
        filters = {key: value for key, value in filters.items() if value}
        if filters:
            try:
                queryset = queryset.filter(**filters, is_booked=False)
            except ValidationError:
                queryset = queryset.none()

        return queryset, may_have_duplicates

    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        if request.method == "POST":
            form = GenerateAvailabilityForm(request.POST)
            if form.is_valid():
                try:
                    created, skipped = Availability.generate_for_months(
                        business=form.cleaned_data["business"],
                        room=form.cleaned_data.get("room"),
                        halls=form.cleaned_data.get("halls"),
                        start_time=form.cleaned_data["start_time"],
                        end_time=form.cleaned_data["end_time"],
                        months=form.get_month_dates(),
                    )
                except ValidationError as error:
                    form.add_error(None, error)
                else:
                    if created:
                        self.message_user(
                            request,
                            f"{created} ta kun uchun bo'sh vaqt yaratildi. "
                            f"{skipped} ta kun allaqachon mavjud bo'lgani uchun o'tkazib yuborildi.",
                            level=messages.SUCCESS,
                        )
                    else:
                        self.message_user(
                            request,
                            "Yangi yozuv yaratilmadi — tanlangan oylarning barcha kunlari uchun "
                            "bo'sh vaqt allaqachon mavjud.",
                            level=messages.WARNING,
                        )

                    if "_addanother" in request.POST:
                        return HttpResponseRedirect(request.path)
                    return HttpResponseRedirect(
                        reverse("admin:reservations_availability_changelist")
                    )
        else:
            form = GenerateAvailabilityForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Bo'sh vaqt qo'shish — oylar bo'yicha",
            "form": form,
            "month_choices": MONTH_CHOICES,
            "businesses_data": build_businesses_data(),
            "selected_room": form.data.get("room", "") if form.is_bound else "",
            "selected_halls": form.data.getlist("halls") if form.is_bound else [],
            "type_restaurant": Business.TYPE_RESTAURANT,
            "type_venue": Business.TYPE_VENUE,
        }
        if extra_context:
            context.update(extra_context)

        request.current_app = self.admin_site.name
        return TemplateResponse(
            request,
            "admin/reservations/availability/generate_form.html",
            context,
        )


# Qaysi holatdan qaysi holatga o'tish mumkin.
ALLOWED_TRANSITIONS = {
    "confirmed": ("pending",),
    "cancelled": ("pending", "confirmed"),
    "completed": ("confirmed",),
}


@admin.register(Reservation)
class ReservationAdmin(ModelAdmin):
    list_display = ("user", "business", "room", "hall", "event_date", "guests_count", "deposit_amount", "status", "created_at")
    list_filter = ("status", "business__business_type", "business")
    list_filter_submit = True
    search_fields = ("user__username", "user__phone_number", "business__name", "room__name", "hall__name")
    autocomplete_fields = ("user", "business", "room", "hall", "availability")
    list_select_related = ("user", "business", "room", "hall", "availability")
    readonly_fields = ("confirmed_at",)

    actions = ["mark_confirmed", "mark_cancelled", "mark_completed"]

    class Media:
        js = ("admin/reservations/dependent_fields.js",)

    @admin.display(description="Sana", ordering="availability__date")
    def event_date(self, obj):
        return obj.availability.date if obj.availability_id else "—"

    def save_model(self, request, obj, form, change):
        # Depozit kiritilmagan bo'lsa — xona/zal narxidan olinadi.
        if not obj.deposit_amount:
            obj.deposit_amount = obj.resolve_deposit_amount()
        super().save_model(request, obj, form, change)

    def _change_status(self, request, queryset, new_status, done_text):
        allowed = ALLOWED_TRANSITIONS[new_status]
        changed, skipped = 0, 0
        for reservation in queryset.select_related("business", "room", "hall", "availability"):
            if reservation.status not in allowed:
                skipped += 1
                continue
            reservation.status = new_status
            try:
                reservation.full_clean()
                with transaction.atomic():
                    reservation.save(update_fields=["status", "updated_at"])
            except (ValidationError, IntegrityError) as error:
                messages_list = getattr(error, "messages", [str(error)])
                self.message_user(
                    request, f"{reservation}: {' '.join(messages_list)}", level=messages.ERROR,
                )
                skipped += 1
                continue
            changed += 1

        self.message_user(request, f"{changed} ta bron {done_text}.")
        if skipped:
            self.message_user(
                request,
                f"{skipped} ta bron o'tkazib yuborildi (holati mos emas yoki joy band).",
                level=messages.WARNING,
            )

    @admin.action(description="Tanlanganlarni tasdiqlash (confirmed)")
    def mark_confirmed(self, request, queryset):
        self._change_status(request, queryset, "confirmed", "tasdiqlandi")

    @admin.action(description="Tanlanganlarni bekor qilish (cancelled)")
    def mark_cancelled(self, request, queryset):
        self._change_status(request, queryset, "cancelled", "bekor qilindi")

    @admin.action(description="Tanlanganlarni yakunlash (completed)")
    def mark_completed(self, request, queryset):
        self._change_status(request, queryset, "completed", "yakunlandi")
