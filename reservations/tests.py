import datetime
import itertools
import threading
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from businesses.models import Hall, Room, VenuePricing
from businesses.services import approve_application, submit_application
from catalog.models import RestaurantMenuItem, VenueMenuItem
from notifications.models import Notification
from reservations.models import Availability, Reservation
from reservations.tasks import complete_past_reservations_task

User = get_user_model()

_seq = itertools.count(1)

T10 = datetime.time(10, 0)
T23 = datetime.time(23, 0)


# --------------------------------------------------------------------------
# Yordamchilar
# --------------------------------------------------------------------------

def make_user(username=None, *, phone="+998901234567", **extra):
    n = next(_seq)
    username = username or f"user{n}"
    return User.objects.create_user(
        username=username, password="StrongPass123!",
        full_name=extra.pop("full_name", f"User {n}"),
        phone_number=phone, **extra,
    )


def make_admin():
    n = next(_seq)
    return User.objects.create_user(
        username=f"admin{n}", password="StrongPass123!", full_name="Admin",
        phone_number="+998900000000", is_staff=True, is_superuser=True,
    )


def make_business(owner, business_type="restaurant", name=None, admin=None):
    admin = admin or make_admin()
    application, business, _ = submit_application(
        applicant=owner, business_type=business_type,
        business_name=name or f"Biznes {next(_seq)}",
    )
    approve_application(application=application, approved_by=admin)
    business.refresh_from_db()
    owner.refresh_from_db()
    return business


def make_room(business, capacity=10, deposit_tier="pro", name="VIP 1"):
    return Room.objects.create(
        business=business, name=name, room_type="vip", capacity=capacity, deposit_tier=deposit_tier,
    )


def make_hall(business, people=300, all_price=None, name="Katta zal"):
    return Hall.objects.create(business=business, name=name, people=people, all_price=all_price)


def open_day(business, date, room=None, start=T10, end=T23):
    return Availability.objects.create(
        business=business, room=room, date=date, start_time=start, end_time=end,
    )


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def today():
    return timezone.localdate()


def tomorrow():
    return today() + datetime.timedelta(days=1)


def restaurant_payload(room, date=None, start="18:00", end="20:00", guests=4, **extra):
    payload = {
        "room": str(room.id), "date": str(date or tomorrow()),
        "start_time": start, "end_time": end, "guests_count": guests,
    }
    payload.update(extra)
    return payload


class RestaurantMixin:
    """Restoran + xona + ertangi kun jadvali + mijoz."""

    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.owner = make_user("owner_r", phone="+998901111111")
        self.business = make_business(self.owner, "restaurant", "Shoxona", admin=self.admin)
        self.room = make_room(self.business, capacity=10)
        self.availability = open_day(self.business, tomorrow(), room=self.room)
        self.customer = make_user("mijoz", phone="+998902222222")
        self.client = client_for(self.customer)
        self.owner_client = client_for(self.owner)

    def book(self, client=None, **kwargs):
        client = client or self.client
        return client.post("/api/reservations/", restaurant_payload(self.room, **kwargs), format="json")


class VenueMixin:
    """To'yxona + zal + ertangi kun jadvali + mijoz."""

    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.owner = make_user("owner_v", phone="+998903333333")
        self.business = make_business(self.owner, "venue", "Navro'z saroyi", admin=self.admin)
        self.hall = make_hall(self.business, people=300, all_price=Decimal("15000000"))
        self.availability = open_day(self.business, tomorrow(), start=T10, end=datetime.time(0, 0))
        self.customer = make_user("kelin", phone="+998904444444")
        self.client = client_for(self.customer)
        self.owner_client = client_for(self.owner)

    def book(self, client=None, **extra):
        payload = {"hall": str(self.hall.id), "date": str(tomorrow()), "guests_count": 200}
        payload.update(extra)
        return (client or self.client).post("/api/reservations/", payload, format="json")


# --------------------------------------------------------------------------
# Availability — model
# --------------------------------------------------------------------------

class AvailabilityModelTests(TestCase):
    def setUp(self):
        self.r_owner = make_user()
        self.restaurant = make_business(self.r_owner, "restaurant")
        self.v_owner = make_user()
        self.venue = make_business(self.v_owner, "venue")
        self.room = make_room(self.restaurant)

    def test_restaurant_requires_room(self):
        a = Availability(business=self.restaurant, date=tomorrow(), start_time=T10, end_time=T23)
        with self.assertRaises(ValidationError) as ctx:
            a.full_clean()
        self.assertIn("room", ctx.exception.message_dict)

    def test_venue_rejects_room(self):
        a = Availability(business=self.venue, room=self.room, date=tomorrow(), start_time=T10, end_time=T23)
        with self.assertRaises(ValidationError) as ctx:
            a.full_clean()
        self.assertIn("room", ctx.exception.message_dict)

    def test_restaurant_end_before_start_invalid(self):
        a = Availability(business=self.restaurant, room=self.room, date=tomorrow(), start_time=T23, end_time=T10)
        with self.assertRaises(ValidationError) as ctx:
            a.full_clean()
        self.assertIn("end_time", ctx.exception.message_dict)

    def test_venue_midnight_end_allowed(self):
        a = Availability(business=self.venue, date=tomorrow(), start_time=T10, end_time=datetime.time(0, 0))
        a.full_clean()  # xato bo'lmasligi kerak
        self.assertTrue(a.ends_at_midnight)

    def test_restaurant_midnight_end_not_allowed(self):
        a = Availability(business=self.restaurant, room=self.room, date=tomorrow(),
                         start_time=T10, end_time=datetime.time(0, 0))
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_generate_for_months_skips_existing(self):
        months = [datetime.date(2030, 2, 1)]
        created, skipped = Availability.generate_for_months(
            business=self.restaurant, room=self.room, start_time=T10, end_time=T23, months=months,
        )
        self.assertEqual((created, skipped), (28, 0))
        created, skipped = Availability.generate_for_months(
            business=self.restaurant, room=self.room, start_time=T10, end_time=T23, months=months,
        )
        self.assertEqual((created, skipped), (0, 28))

    def test_generate_for_months_restaurant_without_room_raises(self):
        with self.assertRaises(ValidationError):
            Availability.generate_for_months(
                business=self.restaurant, start_time=T10, end_time=T23, months=[datetime.date(2030, 3, 1)],
            )

    def test_generate_for_months_foreign_room_raises(self):
        other = make_business(make_user(), "restaurant")
        foreign_room = make_room(other)
        with self.assertRaises(ValidationError):
            Availability.generate_for_months(
                business=self.restaurant, room=foreign_room, start_time=T10, end_time=T23,
                months=[datetime.date(2030, 3, 1)],
            )


# --------------------------------------------------------------------------
# Owner availability API
# --------------------------------------------------------------------------

class OwnerAvailabilityGenerateTests(TestCase):
    url = "/api/owner/availability/generate/"

    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.owner = make_user("owner_gen")
        self.business = make_business(self.owner, "restaurant", admin=self.admin)
        self.room = make_room(self.business)
        self.client = client_for(self.owner)

    def payload(self, **extra):
        data = {"room": str(self.room.id), "start_time": "10:00", "end_time": "23:00", "year": 2030, "months": [1]}
        data.update(extra)
        return data

    def test_anonymous_401(self):
        r = APIClient().post(self.url, self.payload(), format="json")
        self.assertEqual(r.status_code, 401, r.data)
        self.assertEqual(r.data["error"]["code"], "unauthenticated")

    def test_plain_user_403(self):
        r = client_for(make_user()).post(self.url, self.payload(), format="json")
        self.assertEqual(r.status_code, 403, r.data)

    def test_restaurant_generate_creates_all_days(self):
        r = self.client.post(self.url, self.payload(), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["created"], 31)
        self.assertEqual(r.data["skipped"], 0)
        self.assertEqual(Availability.objects.filter(room=self.room).count(), 31)

    def test_generate_second_time_all_skipped(self):
        self.client.post(self.url, self.payload(), format="json")
        r = self.client.post(self.url, self.payload(months=[1, 2]), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["skipped"], 31)
        self.assertEqual(r.data["created"], 28)

    def test_restaurant_without_room_400(self):
        r = self.client.post(self.url, self.payload(room=None), format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("room", r.data["error"]["details"])

    def test_restaurant_end_before_start_400(self):
        r = self.client.post(self.url, self.payload(start_time="20:00", end_time="10:00"), format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("end_time", r.data["error"]["details"])

    def test_restaurant_midnight_end_400(self):
        r = self.client.post(self.url, self.payload(end_time="00:00"), format="json")
        self.assertEqual(r.status_code, 400, r.data)

    def test_foreign_room_400(self):
        other = make_business(make_user(), "restaurant")
        foreign_room = make_room(other)
        r = self.client.post(self.url, self.payload(room=str(foreign_room.id)), format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("room", r.data["error"]["details"])

    def test_invalid_month_400(self):
        r = self.client.post(self.url, self.payload(months=[13]), format="json")
        self.assertEqual(r.status_code, 400, r.data)

    def test_venue_generate_without_room_midnight_ok(self):
        v_owner = make_user("owner_v2")
        venue = make_business(v_owner, "venue", admin=self.admin)
        r = client_for(v_owner).post(
            self.url, {"start_time": "10:00", "end_time": "00:00", "year": 2030, "months": [4]}, format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["created"], 30)
        self.assertEqual(Availability.objects.filter(business=venue, room__isnull=True).count(), 30)

    def test_venue_generate_with_room_400(self):
        v_owner = make_user("owner_v3")
        make_business(v_owner, "venue", admin=self.admin)
        r = client_for(v_owner).post(self.url, self.payload(), format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("room", r.data["error"]["details"])


class OwnerAvailabilityListDetailTests(RestaurantMixin, TestCase):
    list_url = "/api/owner/availability/"

    def detail_url(self, pk):
        return f"/api/owner/availability/{pk}/"

    def test_list_only_own_business_and_filters(self):
        room2 = make_room(self.business, name="Zal 2")
        open_day(self.business, tomorrow(), room=room2)
        open_day(self.business, tomorrow() + datetime.timedelta(days=1), room=self.room)
        other = make_business(make_user(), "restaurant")
        open_day(other, tomorrow(), room=make_room(other))

        r = self.owner_client.get(self.list_url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 3)

        r = self.owner_client.get(self.list_url, {"date": str(tomorrow())})
        self.assertEqual(r.data["count"], 2)

        r = self.owner_client.get(self.list_url, {"room": str(room2.id)})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["room_name"], "Zal 2")

        r = self.owner_client.get(self.list_url, {"date_from": str(tomorrow() + datetime.timedelta(days=1))})
        self.assertEqual(r.data["count"], 1)

    def test_list_plain_user_403(self):
        r = self.client.get(self.list_url)
        self.assertEqual(r.status_code, 403, r.data)

    def test_detail_get(self):
        r = self.owner_client.get(self.detail_url(self.availability.id))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["start_time"], "10:00:00")
        self.assertFalse(r.data["is_booked"])

    def test_detail_foreign_owner_404(self):
        other_owner = make_user()
        make_business(other_owner, "restaurant")
        r = client_for(other_owner).get(self.detail_url(self.availability.id))
        self.assertEqual(r.status_code, 404, r.data)

    def test_patch_end_time_ok(self):
        r = self.owner_client.patch(self.detail_url(self.availability.id), {"end_time": "22:00"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["end_time"], "22:00:00")
        self.availability.refresh_from_db()
        self.assertEqual(self.availability.end_time, datetime.time(22, 0))

    def test_patch_end_before_start_400(self):
        r = self.owner_client.patch(self.detail_url(self.availability.id), {"end_time": "09:00"}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("end_time", r.data["error"]["details"])

    def test_patch_cannot_change_is_booked(self):
        r = self.owner_client.patch(self.detail_url(self.availability.id), {"is_booked": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.availability.refresh_from_db()
        self.assertFalse(self.availability.is_booked)

    def test_delete_with_active_reservation_400(self):
        self.assertEqual(self.book().status_code, 201)
        r = self.owner_client.delete(self.detail_url(self.availability.id))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertTrue(Availability.objects.filter(pk=self.availability.pk).exists())

    def test_delete_with_cancelled_reservation_ok(self):
        res = self.book().data
        Reservation.objects.filter(pk=res["id"]).update(status="cancelled")
        r = self.owner_client.delete(self.detail_url(self.availability.id))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Availability.objects.filter(pk=self.availability.pk).exists())

    def test_delete_empty_ok(self):
        r = self.owner_client.delete(self.detail_url(self.availability.id))
        self.assertEqual(r.status_code, 204)


# --------------------------------------------------------------------------
# Ommaviy availability / busy-hours / busy-dates
# --------------------------------------------------------------------------

class PublicAvailabilityTests(RestaurantMixin, TestCase):
    def url(self, business=None):
        return f"/api/businesses/{(business or self.business).id}/availability/"

    def test_anonymous_can_list(self):
        r = APIClient().get(self.url())
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["id"], str(self.availability.id))

    def test_hidden_business_404(self):
        self.business.is_visible = False
        self.business.save(update_fields=["is_visible"])
        r = APIClient().get(self.url())
        self.assertEqual(r.status_code, 404, r.data)
        self.assertEqual(r.data["error"]["code"], "not_found")

    def test_unknown_business_404(self):
        import uuid
        r = APIClient().get(f"/api/businesses/{uuid.uuid4()}/availability/")
        self.assertEqual(r.status_code, 404, r.data)

    def test_past_days_hidden_without_date_filter(self):
        yesterday = today() - datetime.timedelta(days=1)
        open_day(self.business, yesterday, room=self.room)
        r = APIClient().get(self.url())
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["date"], str(tomorrow()))

        r = APIClient().get(self.url(), {"date": str(yesterday)})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["date"], str(yesterday))

        r = APIClient().get(self.url(), {"date_from": str(yesterday)})
        self.assertEqual(r.data["count"], 2)

    def test_room_filter(self):
        room2 = make_room(self.business, name="Zal 2")
        open_day(self.business, tomorrow(), room=room2)
        r = APIClient().get(self.url(), {"room": str(room2.id)})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(str(r.data["results"][0]["room"]), str(room2.id))


class RoomBusyHoursTests(RestaurantMixin, TestCase):
    def url(self, room=None):
        return f"/api/rooms/{(room or self.room).id}/busy-hours/"

    def test_date_required_400(self):
        r = APIClient().get(self.url())
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["error"]["code"], "bad_request")

    def test_bad_date_format_400(self):
        r = APIClient().get(self.url(), {"date": "2025-13-40"})
        self.assertEqual(r.status_code, 400, r.data)

    def test_unknown_room_404(self):
        import uuid
        r = APIClient().get(f"/api/rooms/{uuid.uuid4()}/busy-hours/", {"date": str(tomorrow())})
        self.assertEqual(r.status_code, 404, r.data)

    def test_closed_day(self):
        r = APIClient().get(self.url(), {"date": str(tomorrow() + datetime.timedelta(days=3))})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data["is_open"])
        self.assertIsNone(r.data["open_time"])
        self.assertEqual(r.data["busy_ranges"], [])

    def test_open_day_with_busy_ranges(self):
        self.assertEqual(self.book(start="18:00", end="20:00").status_code, 201)
        self.assertEqual(self.book(start="12:00", end="13:00").status_code, 201)
        cancelled = self.book(start="14:00", end="15:00").data
        Reservation.objects.filter(pk=cancelled["id"]).update(status="cancelled")

        r = APIClient().get(self.url(), {"date": str(tomorrow())})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["is_open"])
        self.assertEqual(r.data["open_time"], "10:00:00")
        self.assertEqual(r.data["close_time"], "23:00:00")
        self.assertEqual(r.data["capacity"], 10)
        self.assertEqual(Decimal(r.data["deposit_amount"]), Decimal("49000"))
        self.assertEqual(
            [(b["start_time"], b["end_time"]) for b in r.data["busy_ranges"]],
            [("12:00:00", "13:00:00"), ("18:00:00", "20:00:00")],
        )


class HallBusyDatesTests(VenueMixin, TestCase):
    def url(self):
        return f"/api/halls/{self.hall.id}/busy-dates/"

    def test_unknown_hall_404(self):
        import uuid
        r = APIClient().get(f"/api/halls/{uuid.uuid4()}/busy-dates/")
        self.assertEqual(r.status_code, 404, r.data)

    def test_busy_dates_listed(self):
        day2 = tomorrow() + datetime.timedelta(days=5)
        open_day(self.business, day2, end=datetime.time(0, 0))
        self.assertEqual(self.book().status_code, 201)
        second = self.book(date=str(day2))
        self.assertEqual(second.status_code, 201, second.data)
        Reservation.objects.filter(pk=second.data["id"]).update(status="cancelled")

        r = APIClient().get(self.url())
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["busy_dates"], [str(tomorrow())])
        self.assertEqual(r.data["hall_name"], "Katta zal")
        self.assertEqual(Decimal(r.data["all_price"]), Decimal("15000000"))

    def test_date_range_filter(self):
        self.assertEqual(self.book().status_code, 201)
        after = tomorrow() + datetime.timedelta(days=1)
        r = APIClient().get(self.url(), {"date_from": str(after)})
        self.assertEqual(r.data["busy_dates"], [])
        r = APIClient().get(self.url(), {"date_to": str(tomorrow())})
        self.assertEqual(r.data["busy_dates"], [str(tomorrow())])

    def test_bad_date_400(self):
        r = APIClient().get(self.url(), {"date_from": "kecha"})
        self.assertEqual(r.status_code, 400, r.data)


# --------------------------------------------------------------------------
# Restoran broni
# --------------------------------------------------------------------------

class RestaurantReservationCreateTests(RestaurantMixin, TestCase):
    def test_anonymous_401(self):
        r = self.book(client=APIClient())
        self.assertEqual(r.status_code, 401, r.data)

    def test_without_phone_403_phone_required(self):
        no_phone = make_user("nophone", phone=None)
        r = self.book(client=client_for(no_phone))
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data["error"]["code"], "phone_required")

    def test_staff_403(self):
        r = self.book(client=client_for(self.admin))
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(r.data["error"]["code"], "permission_denied")

    def test_success_201(self):
        self.business.telegram_username = "shoxona_admin"
        self.business.save(update_fields=["telegram_username"])
        r = self.book()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "pending")
        self.assertEqual(r.data["date"], str(tomorrow()))
        self.assertEqual(r.data["start_time"], "18:00:00")
        self.assertEqual(r.data["room_name"], "VIP 1")
        self.assertEqual(r.data["business_type"], "restaurant")
        self.assertEqual(Decimal(r.data["deposit_amount"]), Decimal("49000"))  # pro tarif
        self.assertEqual(r.data["admin_telegram"], "@shoxona_admin")
        self.assertIn("@shoxona_admin", r.data["message"])
        self.assertIn("49000", r.data["message"])
        self.assertTrue(r.data["can_cancel"])
        self.assertIsNone(r.data["confirmed_at"])

        reservation = Reservation.objects.get(pk=r.data["id"])
        self.assertEqual(reservation.availability_id, self.availability.id)
        self.assertEqual(reservation.user, self.customer)
        self.assertEqual(reservation.business, self.business)

    def test_premium_room_deposit(self):
        room = make_room(self.business, deposit_tier="premium", name="Premium")
        open_day(self.business, tomorrow(), room=room)
        r = self.client.post("/api/reservations/", restaurant_payload(room), format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Decimal(r.data["deposit_amount"]), Decimal("99000"))

    def test_platform_admin_telegram_fallback(self):
        r = self.book()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["admin_telegram"], "@akobir_marupov")

    def test_notification_to_owner_on_create(self):
        before = Notification.objects.filter(user=self.owner, kind=Notification.KIND_RESERVATION).count()
        self.assertEqual(self.book().status_code, 201)
        notes = Notification.objects.filter(user=self.owner, kind=Notification.KIND_RESERVATION)
        self.assertEqual(notes.count(), before + 1)
        self.assertEqual(notes.first().title, "Yangi bron so'rovi")
        self.assertIn(str(tomorrow()), notes.first().body)

    def test_over_capacity_400(self):
        r = self.book(guests=11)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("guests_count", r.data["error"]["details"])

    def test_zero_guests_400(self):
        r = self.book(guests=0)
        self.assertEqual(r.status_code, 400, r.data)

    def test_past_date_400(self):
        r = self.book(date=today() - datetime.timedelta(days=1))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("date", r.data["error"]["details"])

    def test_today_past_hour_400(self):
        open_day(self.business, today(), room=self.room)
        r = self.book(date=today(), start="00:00", end="00:01")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("start_time", r.data["error"]["details"])

    def test_more_than_365_days_400(self):
        r = self.book(date=today() + datetime.timedelta(days=366))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("date", r.data["error"]["details"])

    def test_end_before_start_400(self):
        r = self.book(start="20:00", end="18:00")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("end_time", r.data["error"]["details"])

    def test_unknown_room_400(self):
        import uuid
        payload = restaurant_payload(self.room)
        payload["room"] = str(uuid.uuid4())
        r = self.client.post("/api/reservations/", payload, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("room", r.data["error"]["details"])

    def test_hidden_business_400(self):
        self.business.is_visible = False
        self.business.save(update_fields=["is_visible"])
        r = self.book()
        self.assertEqual(r.status_code, 400, r.data)

    def test_outside_working_hours_409(self):
        r = self.book(start="23:00", end="23:30")
        self.assertEqual(r.status_code, 409, r.data)
        self.assertEqual(r.data["error"]["code"], "conflict")
        r = self.book(start="09:00", end="11:00")
        self.assertEqual(r.status_code, 409, r.data)
        r = self.book(start="22:00", end="23:30")
        self.assertEqual(r.status_code, 409, r.data)

    def test_day_not_opened_409(self):
        r = self.book(date=tomorrow() + datetime.timedelta(days=2))
        self.assertEqual(r.status_code, 409, r.data)
        self.assertIn("jadvali ochilmagan", r.data["error"]["message"])

    def test_overlap_409_and_adjacent_201(self):
        self.assertEqual(self.book(start="18:00", end="20:00").status_code, 201)
        r = self.book(start="19:00", end="21:00")
        self.assertEqual(r.status_code, 409, r.data)
        r = self.book(start="17:00", end="18:30")
        self.assertEqual(r.status_code, 409, r.data)
        r = self.book(start="18:30", end="19:00")
        self.assertEqual(r.status_code, 409, r.data)
        # yonma-yon vaqtlar to'qnashmaydi
        self.assertEqual(self.book(start="20:00", end="21:00").status_code, 201)
        self.assertEqual(self.book(start="17:00", end="18:00").status_code, 201)
        self.assertEqual(Reservation.objects.filter(room=self.room).count(), 3)

    def test_cancelled_reservation_does_not_block(self):
        first = self.book(start="18:00", end="20:00").data
        Reservation.objects.filter(pk=first["id"]).update(status="cancelled")
        r = self.book(start="18:00", end="20:00")
        self.assertEqual(r.status_code, 201, r.data)

    def test_other_room_same_time_ok(self):
        room2 = make_room(self.business, name="Zal 2")
        open_day(self.business, tomorrow(), room=room2)
        self.assertEqual(self.book().status_code, 201)
        r = self.client.post("/api/reservations/", restaurant_payload(room2), format="json")
        self.assertEqual(r.status_code, 201, r.data)

    def test_menu_snapshot(self):
        item = RestaurantMenuItem.objects.create(business=self.business, name="Osh", price=Decimal("45000"))
        item2 = RestaurantMenuItem.objects.create(business=self.business, name="Shashlik", price=Decimal("30000"))
        r = self.book(menu_items=[str(item.id), str(item2.id), str(item.id)])
        self.assertEqual(r.status_code, 201, r.data)
        snapshot = r.data["selected_menu"]
        self.assertEqual(len(snapshot), 2)
        self.assertEqual({s["name"] for s in snapshot}, {"Osh", "Shashlik"})
        self.assertEqual({s["price"] for s in snapshot}, {"45000.00", "30000.00"})

    def test_missing_or_unavailable_menu_item_400(self):
        import uuid
        r = self.book(menu_items=[str(uuid.uuid4())])
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("menu_items", r.data["error"]["details"])

        hidden = RestaurantMenuItem.objects.create(
            business=self.business, name="Yashirin", price=Decimal("1000"), is_available=False,
        )
        r = self.book(menu_items=[str(hidden.id)])
        self.assertEqual(r.status_code, 400, r.data)

        other = make_business(make_user(), "restaurant")
        foreign = RestaurantMenuItem.objects.create(business=other, name="Begona", price=Decimal("1000"))
        r = self.book(menu_items=[str(foreign.id)])
        self.assertEqual(r.status_code, 400, r.data)

    def test_special_request_saved(self):
        r = self.book(special_request="Tug'ilgan kun tort")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["special_request"], "Tug'ilgan kun tort")


# --------------------------------------------------------------------------
# To'yxona broni
# --------------------------------------------------------------------------

class VenueReservationCreateTests(VenueMixin, TestCase):
    def test_success_rent_only(self):
        r = self.book()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["hall_name"], "Katta zal")
        self.assertEqual(r.data["business_type"], "venue")
        self.assertIsNone(r.data["dish_count"])
        self.assertIsNone(r.data["price_per_person"])
        self.assertEqual(Decimal(r.data["day_rent_price"]), Decimal("15000000"))
        self.assertEqual(Decimal(r.data["total_price"]), Decimal("15000000"))
        self.assertEqual(Decimal(r.data["deposit_amount"]), Decimal("599000"))
        self.assertIsNone(r.data["start_time"])

    def test_one_per_day_409(self):
        self.assertEqual(self.book().status_code, 201)
        other = make_user("kuyov", phone="+998905555555")
        r = self.book(client=client_for(other))
        self.assertEqual(r.status_code, 409, r.data)
        self.assertEqual(r.data["error"]["code"], "conflict")

    def test_second_hall_same_day_also_blocked(self):
        hall2 = make_hall(self.business, name="Kichik zal", people=100)
        self.assertEqual(self.book().status_code, 201)
        r = self.book(hall=str(hall2.id), guests_count=50)
        self.assertEqual(r.status_code, 409, r.data)

    def test_is_booked_synced(self):
        r = self.book()
        self.assertEqual(r.status_code, 201, r.data)
        self.availability.refresh_from_db()
        self.assertTrue(self.availability.is_booked)

        cancel = self.owner_client.patch(
            f"/api/owner/reservations/{r.data['id']}/status/", {"status": "cancelled"}, format="json",
        )
        self.assertEqual(cancel.status_code, 200, cancel.data)
        self.availability.refresh_from_db()
        self.assertFalse(self.availability.is_booked)

        # bekor qilingan bron kunni band qilmaydi
        again = self.book()
        self.assertEqual(again.status_code, 201, again.data)
        self.availability.refresh_from_db()
        self.assertTrue(self.availability.is_booked)

    def test_is_booked_stays_true_after_completed(self):
        r = self.book()
        reservation = Reservation.objects.get(pk=r.data["id"])
        reservation.status = "completed"
        reservation.save(update_fields=["status"])
        self.availability.refresh_from_db()
        self.assertTrue(self.availability.is_booked)

    def test_day_not_opened_409(self):
        r = self.book(date=str(tomorrow() + datetime.timedelta(days=3)))
        self.assertEqual(r.status_code, 409, r.data)

    def test_over_capacity_400(self):
        r = self.book(guests_count=301)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("guests_count", r.data["error"]["details"])

    def test_past_date_400(self):
        r = self.book(date=str(today() - datetime.timedelta(days=1)))
        self.assertEqual(r.status_code, 400, r.data)

    def test_total_rent_plus_per_person(self):
        VenuePricing.objects.create(business=self.business, dish_count=2, price_per_person=Decimal("80000"))
        osh = VenueMenuItem.objects.create(business=self.business, name="Osh")
        somsa = VenueMenuItem.objects.create(business=self.business, name="Somsa")
        r = self.book(guests_count=200, dish_count=2, menu_items=[str(osh.id), str(somsa.id)])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["dish_count"], 2)
        self.assertEqual(Decimal(r.data["price_per_person"]), Decimal("80000"))
        self.assertEqual(Decimal(r.data["day_rent_price"]), Decimal("15000000"))
        self.assertEqual(Decimal(r.data["total_price"]), Decimal("15000000") + Decimal("80000") * 200)
        self.assertEqual({m["name"] for m in r.data["selected_menu"]}, {"Osh", "Somsa"})

    def test_dish_count_inferred_from_menu_items(self):
        VenuePricing.objects.create(business=self.business, dish_count=1, price_per_person=Decimal("50000"))
        osh = VenueMenuItem.objects.create(business=self.business, name="Osh")
        r = self.book(guests_count=100, menu_items=[str(osh.id)])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["dish_count"], 1)
        self.assertEqual(Decimal(r.data["total_price"]), Decimal("15000000") + Decimal("50000") * 100)

    def test_per_person_only_without_rent(self):
        self.hall.all_price = None
        self.hall.save(update_fields=["all_price"])
        VenuePricing.objects.create(business=self.business, dish_count=1, price_per_person=Decimal("50000"))
        osh = VenueMenuItem.objects.create(business=self.business, name="Osh")
        r = self.book(guests_count=100, dish_count=1, menu_items=[str(osh.id)])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(r.data["day_rent_price"])
        self.assertEqual(Decimal(r.data["total_price"]), Decimal("5000000"))

    def test_no_price_total_none(self):
        self.hall.all_price = None
        self.hall.save(update_fields=["all_price"])
        r = self.book()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(r.data["total_price"])
        self.assertIsNone(r.data["day_rent_price"])
        self.assertIsNone(r.data["price_per_person"])

    def test_menu_items_count_mismatch_400(self):
        VenuePricing.objects.create(business=self.business, dish_count=2, price_per_person=Decimal("80000"))
        osh = VenueMenuItem.objects.create(business=self.business, name="Osh")
        r = self.book(dish_count=2, menu_items=[str(osh.id)])
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("menu_items", r.data["error"]["details"])

    def test_package_not_available_400(self):
        VenuePricing.objects.create(business=self.business, dish_count=1, price_per_person=Decimal("50000"))
        r = self.book(dish_count=3)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("dish_count", r.data["error"]["details"])
        self.assertIn("Mavjud paketlar: 1", r.data["error"]["details"]["dish_count"][0])

    def test_unknown_menu_item_400(self):
        import uuid
        r = self.book(menu_items=[str(uuid.uuid4())])
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("menu_items", r.data["error"]["details"])

    def test_dish_count_out_of_range_400(self):
        r = self.book(dish_count=4)
        self.assertEqual(r.status_code, 400, r.data)

    def test_notification_to_owner(self):
        self.assertEqual(self.book().status_code, 201)
        self.assertTrue(
            Notification.objects.filter(user=self.owner, kind="reservation", title="Yangi bron so'rovi").exists()
        )


# --------------------------------------------------------------------------
# Reservation model
# --------------------------------------------------------------------------

class ReservationModelTests(RestaurantMixin, TestCase):
    def make(self, **kwargs):
        data = dict(
            user=self.customer, business=self.business, room=self.room, availability=self.availability,
            start_time=datetime.time(18), end_time=datetime.time(20), guests_count=4,
        )
        data.update(kwargs)
        return Reservation(**data)

    def test_clean_ok(self):
        self.make().full_clean()

    def test_restaurant_requires_room(self):
        with self.assertRaises(ValidationError) as ctx:
            self.make(room=None, availability=None).full_clean()
        self.assertIn("room", ctx.exception.message_dict)

    def test_over_capacity(self):
        with self.assertRaises(ValidationError) as ctx:
            self.make(guests_count=50).full_clean()
        self.assertIn("guests_count", ctx.exception.message_dict)

    def test_start_without_end(self):
        with self.assertRaises(ValidationError) as ctx:
            self.make(end_time=None).full_clean()
        self.assertIn("end_time", ctx.exception.message_dict)

    def test_foreign_room(self):
        other = make_business(make_user(), "restaurant")
        with self.assertRaises(ValidationError) as ctx:
            self.make(room=make_room(other), availability=None).full_clean()
        self.assertIn("room", ctx.exception.message_dict)

    def test_availability_of_other_room(self):
        room2 = make_room(self.business, name="Zal 2")
        with self.assertRaises(ValidationError) as ctx:
            self.make(room=room2).full_clean()
        self.assertIn("availability", ctx.exception.message_dict)

    def test_outside_working_hours(self):
        with self.assertRaises(ValidationError) as ctx:
            self.make(start_time=datetime.time(9), end_time=datetime.time(11)).full_clean()
        self.assertIn("start_time", ctx.exception.message_dict)

    def test_overlap_detected(self):
        self.make().save()
        with self.assertRaises(ValidationError) as ctx:
            self.make(start_time=datetime.time(19), end_time=datetime.time(21)).full_clean()
        self.assertIn("start_time", ctx.exception.message_dict)
        # cancelled bron tekshirilmaydi
        self.make(start_time=datetime.time(19), end_time=datetime.time(21), status="cancelled").full_clean()

    def test_venue_requires_hall(self):
        venue = make_business(make_user(), "venue")
        r = Reservation(user=self.customer, business=venue, guests_count=10)
        with self.assertRaises(ValidationError) as ctx:
            r.full_clean()
        self.assertIn("hall", ctx.exception.message_dict)

    def test_venue_day_taken(self):
        venue = make_business(make_user(), "venue")
        hall = make_hall(venue)
        avail = open_day(venue, tomorrow(), end=datetime.time(0, 0))
        Reservation.objects.create(user=self.customer, business=venue, hall=hall, availability=avail, guests_count=10)
        other = Reservation(user=make_user(), business=venue, hall=hall, availability=avail, guests_count=10)
        with self.assertRaises(ValidationError) as ctx:
            other.full_clean()
        self.assertIn("availability", ctx.exception.message_dict)

    def test_event_times_and_deadline(self):
        r = self.make()
        r.save()
        tz = timezone.get_current_timezone()
        self.assertEqual(r.event_starts_at(), datetime.datetime.combine(tomorrow(), datetime.time(18), tzinfo=tz))
        self.assertEqual(r.event_ends_at(), datetime.datetime.combine(tomorrow(), datetime.time(20), tzinfo=tz))
        deadline = r.cancel_deadline()
        self.assertLess(r.created_at, deadline)
        self.assertLess(deadline, r.event_starts_at())
        self.assertAlmostEqual(
            (deadline - r.created_at).total_seconds(),
            (r.event_starts_at() - r.created_at).total_seconds() / 2, delta=1,
        )
        self.assertEqual(r.cancel_check(), (True, ""))

    def test_event_ends_next_day_when_end_before_start(self):
        avail = open_day(self.business, tomorrow(), room=make_room(self.business, name="Tungi"),
                         start=datetime.time(18), end=datetime.time(23))
        r = Reservation(user=self.customer, business=self.business, room=avail.room, availability=avail,
                        start_time=datetime.time(22), end_time=datetime.time(2), guests_count=2)
        self.assertEqual(r.event_ends_at().date(), tomorrow() + datetime.timedelta(days=1))
        self.assertEqual(timezone.localtime(r.event_ends_at()).time(), datetime.time(2))

    def test_event_times_none_without_availability(self):
        r = self.make(availability=None)
        self.assertIsNone(r.event_starts_at())
        self.assertIsNone(r.event_ends_at())

    def test_cancel_check_closed_states(self):
        r = self.make(status="completed")
        allowed, reason = r.cancel_check()
        self.assertFalse(allowed)
        self.assertIn("Yakunlangan", reason)

    def test_confirmed_at_set_on_save(self):
        r = self.make()
        r.save()
        self.assertIsNone(r.confirmed_at)
        r.status = "confirmed"
        r.save(update_fields=["status"])
        r.refresh_from_db()
        self.assertIsNotNone(r.confirmed_at)


# --------------------------------------------------------------------------
# Ro'yxat / detail / bekor qilish
# --------------------------------------------------------------------------

class MyReservationListTests(RestaurantMixin, TestCase):
    def test_anonymous_401(self):
        self.assertEqual(APIClient().get("/api/reservations/my/").status_code, 401)

    def test_only_own_and_filters(self):
        self.assertEqual(self.book(start="18:00", end="20:00").status_code, 201)
        second = self.book(start="20:00", end="21:00").data
        Reservation.objects.filter(pk=second["id"]).update(status="cancelled")

        stranger = make_user("boshqa", phone="+998906666666")
        self.assertEqual(self.book(client=client_for(stranger), start="12:00", end="13:00").status_code, 201)

        r = self.client.get("/api/reservations/my/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 2)
        self.assertEqual(set(r.data.keys()), {"count", "total_pages", "current_page", "next", "previous", "results"})

        r = self.client.get("/api/reservations/my/", {"status": "cancelled"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["id"], second["id"])

        r = self.client.get("/api/reservations/my/", {"business_type": "venue"})
        self.assertEqual(r.data["count"], 0)

        r = self.client.get("/api/reservations/my/", {"date": str(tomorrow())})
        self.assertEqual(r.data["count"], 2)
        r = self.client.get("/api/reservations/my/", {"date_from": str(tomorrow() + datetime.timedelta(days=1))})
        self.assertEqual(r.data["count"], 0)


class ReservationDetailTests(RestaurantMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.reservation_id = self.book().data["id"]
        self.url = f"/api/reservations/{self.reservation_id}/"

    def test_customer_sees(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["id"], self.reservation_id)
        self.assertEqual(r.data["customer"]["trust_bits"], 100)
        self.assertEqual(r.data["customer"]["trust_level"], "excellent")

    def test_owner_sees(self):
        r = self.owner_client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["user_phone"], "+998902222222")

    def test_staff_sees(self):
        r = client_for(self.admin).get(self.url)
        self.assertEqual(r.status_code, 200, r.data)

    def test_stranger_403(self):
        r = client_for(make_user()).get(self.url)
        self.assertEqual(r.status_code, 403, r.data)

    def test_other_owner_403(self):
        other_owner = make_user()
        make_business(other_owner, "restaurant")
        r = client_for(other_owner).get(self.url)
        self.assertEqual(r.status_code, 403, r.data)

    def test_anonymous_401(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_unknown_404(self):
        import uuid
        r = self.client.get(f"/api/reservations/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, 404, r.data)


class ReservationCancelTests(RestaurantMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.reservation_id = self.book().data["id"]
        self.url = f"/api/reservations/{self.reservation_id}/cancel/"

    def close_window(self):
        # bron 10 kun oldin qilingan bo'lsa, muddat (yarmi) allaqachon o'tgan
        Reservation.objects.filter(pk=self.reservation_id).update(
            created_at=timezone.now() - datetime.timedelta(days=10)
        )

    def test_stranger_403(self):
        r = client_for(make_user()).patch(self.url)
        self.assertEqual(r.status_code, 403, r.data)

    def test_owner_cannot_use_customer_cancel_403(self):
        r = self.owner_client.patch(self.url)
        self.assertEqual(r.status_code, 403, r.data)

    def test_success_penalizes_trust(self):
        r = self.client.patch(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "cancelled")
        self.assertEqual(r.data["trust"]["bits"], 95)
        self.assertIn("5 Bit", r.data["message"])
        self.assertIn("95 Bit", r.data["message"])
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.trust_bits, 95)
        self.assertEqual(self.customer.cancelled_reservations_count, 1)

    def test_customer_notified_on_cancel(self):
        self.client.patch(self.url)
        note = Notification.objects.filter(user=self.customer, kind="reservation").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.title, "Broningiz bekor qilindi")
        self.assertEqual(note.level, "warning")

    def test_twice_400(self):
        self.assertEqual(self.client.patch(self.url).status_code, 200)
        r = self.client.patch(self.url)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["error"]["code"], "bad_request")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.trust_bits, 95)

    def test_completed_400(self):
        Reservation.objects.filter(pk=self.reservation_id).update(status="completed")
        r = self.client.patch(self.url)
        self.assertEqual(r.status_code, 400, r.data)

    def test_window_closed_400(self):
        self.close_window()
        detail = self.client.get(f"/api/reservations/{self.reservation_id}/").data
        self.assertFalse(detail["can_cancel"])
        self.assertIn("muddati tugagan", detail["cancel_blocked_reason"])

        r = self.client.patch(self.url)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data["error"]["code"], "cancel_window_closed")
        self.assertEqual(Reservation.objects.get(pk=self.reservation_id).status, "pending")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.trust_bits, 100)

    def test_staff_cancels_despite_window_without_penalty(self):
        self.close_window()
        r = client_for(self.admin).patch(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "cancelled")
        self.assertNotIn("trust", r.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.trust_bits, 100)
        self.assertEqual(self.customer.cancelled_reservations_count, 0)

    def test_cancel_frees_slot(self):
        self.client.patch(self.url)
        r = self.book(start="18:00", end="20:00")
        self.assertEqual(r.status_code, 201, r.data)


# --------------------------------------------------------------------------
# Egasi: holat va ro'yxat
# --------------------------------------------------------------------------

class OwnerReservationStatusTests(RestaurantMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.reservation_id = self.book().data["id"]

    def url(self, pk=None):
        return f"/api/owner/reservations/{pk or self.reservation_id}/status/"

    def set_status(self, value, client=None):
        return (client or self.owner_client).patch(self.url(), {"status": value}, format="json")

    def test_customer_403(self):
        r = self.set_status("confirmed", client=self.client)
        self.assertEqual(r.status_code, 403, r.data)

    def test_staff_403(self):
        r = self.set_status("confirmed", client=client_for(self.admin))
        self.assertEqual(r.status_code, 403, r.data)

    def test_other_business_owner_404(self):
        other_owner = make_user()
        make_business(other_owner, "restaurant")
        r = self.set_status("confirmed", client=client_for(other_owner))
        self.assertEqual(r.status_code, 404, r.data)

    def test_invalid_status_400(self):
        r = self.set_status("pending")
        self.assertEqual(r.status_code, 400, r.data)
        r = self.set_status("xyz")
        self.assertEqual(r.status_code, 400, r.data)

    def test_pending_to_confirmed_sets_confirmed_at(self):
        r = self.set_status("confirmed")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "confirmed")
        self.assertIsNotNone(r.data["confirmed_at"])
        self.assertIsNotNone(Reservation.objects.get(pk=self.reservation_id).confirmed_at)

    def test_pending_to_completed_400(self):
        r = self.set_status("completed")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(Reservation.objects.get(pk=self.reservation_id).status, "pending")

    def test_confirmed_to_completed(self):
        self.set_status("confirmed")
        r = self.set_status("completed")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "completed")

    def test_confirmed_to_cancelled(self):
        self.set_status("confirmed")
        r = self.set_status("cancelled")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "cancelled")

    def test_confirmed_to_confirmed_400(self):
        self.set_status("confirmed")
        r = self.set_status("confirmed")
        self.assertEqual(r.status_code, 400, r.data)

    def test_cancelled_is_terminal(self):
        self.set_status("cancelled")
        for value in ("confirmed", "completed", "cancelled"):
            r = self.set_status(value)
            self.assertEqual(r.status_code, 400, (value, r.data))

    def test_completed_is_terminal(self):
        self.set_status("confirmed")
        self.set_status("completed")
        for value in ("confirmed", "cancelled"):
            r = self.set_status(value)
            self.assertEqual(r.status_code, 400, (value, r.data))

    def test_owner_reject_does_not_penalize_customer(self):
        r = self.set_status("cancelled")
        self.assertEqual(r.status_code, 200, r.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.trust_bits, 100)
        self.assertEqual(self.customer.cancelled_reservations_count, 0)

    def test_customer_notified_on_status_change(self):
        Notification.objects.filter(user=self.customer).delete()
        self.set_status("confirmed")
        note = Notification.objects.filter(user=self.customer, kind="reservation").first()
        self.assertIsNotNone(note)
        self.assertEqual(note.title, "Broningiz tasdiqlandi")
        self.assertEqual(note.level, "success")
        self.assertIn("Shoxona", note.body)

        self.set_status("completed")
        self.assertEqual(
            Notification.objects.filter(user=self.customer, kind="reservation", title="Broningiz yakunlandi").count(), 1,
        )


class OwnerReservationListTests(RestaurantMixin, TestCase):
    url = "/api/owner/reservations/"

    def test_customer_403(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_owner_without_business_404(self):
        # role=business, lekin biznesi yo'q
        lonely = make_user(role="business")
        r = client_for(lonely).get(self.url)
        self.assertEqual(r.status_code, 404, r.data)

    def test_only_own_business_with_filters(self):
        first = self.book(start="18:00", end="20:00").data
        self.owner_client.patch(f"/api/owner/reservations/{first['id']}/status/", {"status": "confirmed"}, format="json")
        self.assertEqual(self.book(start="12:00", end="13:00").status_code, 201)

        other_owner = make_user("owner_x", phone="+998907777777")
        other = make_business(other_owner, "restaurant", admin=self.admin)
        other_room = make_room(other)
        open_day(other, tomorrow(), room=other_room)
        r = self.client.post("/api/reservations/", restaurant_payload(other_room), format="json")
        self.assertEqual(r.status_code, 201, r.data)

        r = self.owner_client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 2)

        r = self.owner_client.get(self.url, {"status": "confirmed"})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["id"], first["id"])

        r = self.owner_client.get(self.url, {"room": str(self.room.id)})
        self.assertEqual(r.data["count"], 2)

        r = client_for(other_owner).get(self.url)
        self.assertEqual(r.data["count"], 1)


class AdminReservationListTests(RestaurantMixin, TestCase):
    url = "/api/admin/reservations/"

    def test_permissions(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.owner_client.get(self.url).status_code, 403)

    def test_admin_sees_all(self):
        self.assertEqual(self.book().status_code, 201)
        other = make_user("boshqa2", phone="+998908888888")
        self.assertEqual(self.book(client=client_for(other), start="12:00", end="13:00").status_code, 201)

        r = client_for(self.admin).get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 2)

        r = client_for(self.admin).get(self.url, {"business": str(self.business.id), "status": "pending"})
        self.assertEqual(r.data["count"], 2)


# --------------------------------------------------------------------------
# Pending review
# --------------------------------------------------------------------------

class PendingReviewTests(RestaurantMixin, TestCase):
    url = "/api/reservations/pending-review/"

    def past_reservation(self, days_ago, status="completed", user=None):
        date = today() - datetime.timedelta(days=days_ago)
        avail = Availability.objects.filter(room=self.room, date=date).first() or open_day(
            self.business, date, room=self.room,
        )
        return Reservation.objects.create(
            user=user or self.customer, business=self.business, room=self.room, availability=avail,
            start_time=datetime.time(18), end_time=datetime.time(20), guests_count=2, status=status,
        )

    def test_anonymous_401(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_only_recent_completed_without_review_limited_to_three(self):
        from reviews.models import Review

        recent = [self.past_reservation(d) for d in (1, 2, 3, 4)]
        self.past_reservation(20)  # 14 kundan eski
        self.past_reservation(5, status="confirmed")
        self.past_reservation(6, status="cancelled")
        reviewed = self.past_reservation(7)
        Review.objects.create(user=self.customer, business=self.business, reservation=reviewed, rating=5)
        self.past_reservation(1, user=make_user())  # begona

        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data), 3)
        self.assertEqual([x["id"] for x in r.data], [str(x.id) for x in recent[:3]])

    def test_empty_when_nothing(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data, [])


# --------------------------------------------------------------------------
# Celery: bronlarni yakunlash
# --------------------------------------------------------------------------

class CompletePastReservationsTaskTests(RestaurantMixin, TestCase):
    def confirmed(self, date, start=datetime.time(18), end=datetime.time(20), room=None):
        room = room or self.room
        avail = Availability.objects.filter(room=room, date=date).first() or open_day(
            self.business, date, room=room,
        )
        return Reservation.objects.create(
            user=self.customer, business=self.business, room=room, availability=avail,
            start_time=start, end_time=end, guests_count=2, status="confirmed",
        )

    def test_past_confirmed_completed_and_review_requested(self):
        yesterday = today() - datetime.timedelta(days=1)
        r = self.confirmed(yesterday)
        Notification.objects.filter(user=self.customer).delete()

        self.assertEqual(complete_past_reservations_task(), 1)
        r.refresh_from_db()
        self.assertEqual(r.status, "completed")

        review_note = Notification.objects.filter(user=self.customer, kind=Notification.KIND_REVIEW).first()
        self.assertIsNotNone(review_note)
        self.assertIn("Shoxona", review_note.body)
        self.assertTrue(
            Notification.objects.filter(user=self.customer, kind="reservation", title="Broningiz yakunlandi").exists()
        )

    def test_future_untouched(self):
        r = self.confirmed(tomorrow())
        self.assertEqual(complete_past_reservations_task(), 0)
        r.refresh_from_db()
        self.assertEqual(r.status, "confirmed")

    def test_pending_and_cancelled_untouched(self):
        yesterday = today() - datetime.timedelta(days=1)
        pending = self.confirmed(yesterday)
        Reservation.objects.filter(pk=pending.pk).update(status="pending")
        cancelled = self.confirmed(yesterday, start=datetime.time(12), end=datetime.time(13))
        Reservation.objects.filter(pk=cancelled.pk).update(status="cancelled")

        self.assertEqual(complete_past_reservations_task(), 0)
        self.assertEqual(Reservation.objects.get(pk=pending.pk).status, "pending")
        self.assertEqual(Reservation.objects.get(pk=cancelled.pk).status, "cancelled")

    def test_night_reservation_today_not_completed_yet(self):
        # 22:00–02:00 — tugash vaqti ertasi kun, demak hali yakunlanmagan
        r = self.confirmed(today(), start=datetime.time(22), end=datetime.time(2))
        self.assertEqual(complete_past_reservations_task(), 0)
        r.refresh_from_db()
        self.assertEqual(r.status, "confirmed")

    def test_night_reservation_two_days_ago_completed(self):
        r = self.confirmed(today() - datetime.timedelta(days=2), start=datetime.time(22), end=datetime.time(2))
        self.assertEqual(complete_past_reservations_task(), 1)
        r.refresh_from_db()
        self.assertEqual(r.status, "completed")

    def test_second_run_idempotent(self):
        self.confirmed(today() - datetime.timedelta(days=1))
        self.assertEqual(complete_past_reservations_task(), 1)
        self.assertEqual(complete_past_reservations_task(), 0)

    def test_venue_reservation_without_times_uses_availability_end(self):
        v_owner = make_user("owner_v4", phone="+998909999999")
        venue = make_business(v_owner, "venue", admin=self.admin)
        hall = make_hall(venue)
        avail = open_day(venue, today() - datetime.timedelta(days=1), end=datetime.time(0, 0))
        r = Reservation.objects.create(
            user=self.customer, business=venue, hall=hall, availability=avail, guests_count=100, status="confirmed",
        )
        self.assertEqual(complete_past_reservations_task(), 1)
        r.refresh_from_db()
        self.assertEqual(r.status, "completed")
        avail.refresh_from_db()
        self.assertTrue(avail.is_booked)


# --------------------------------------------------------------------------
# Poyga: bir vaqtga parallel so'rovlar
# --------------------------------------------------------------------------

class ReservationRaceConditionTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.owner = make_user("owner_race", phone="+998901111111")
        self.business = make_business(self.owner, "restaurant", "Poyga", admin=self.admin)
        self.room = make_room(self.business, capacity=10)
        self.availability = open_day(self.business, tomorrow(), room=self.room)
        self.customers = [make_user(f"racer{i}", phone=f"+99890000000{i}") for i in range(4)]

    def test_parallel_requests_only_one_succeeds(self):
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(len(self.customers))

        def worker(user):
            try:
                client = client_for(user)
                barrier.wait(timeout=10)
                r = client.post("/api/reservations/", restaurant_payload(self.room), format="json")
                with lock:
                    results.append(r.status_code)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    results.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(u,)) for u in self.customers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(results, key=str), [201, 409, 409, 409], results)
        self.assertEqual(Reservation.objects.filter(room=self.room, status="pending").count(), 1)

    def test_sequential_second_conflicts(self):
        first = client_for(self.customers[0]).post("/api/reservations/", restaurant_payload(self.room), format="json")
        self.assertEqual(first.status_code, 201, first.data)
        second = client_for(self.customers[1]).post("/api/reservations/", restaurant_payload(self.room), format="json")
        self.assertEqual(second.status_code, 409, second.data)


# --------------------------------------------------------------------------
# Tuzatishlar: taom soni paketsiz to'yxonada, busy-hours formati
# --------------------------------------------------------------------------
class VenueDishCountWithoutPackagesTests(VenueMixin, TestCase):
    def test_explicit_dish_count_without_packages_400(self):
        response = self.book(dish_count=2)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("dish_count", response.data["error"]["details"])
        self.assertIn("paketlari belgilanmagan", response.data["error"]["details"]["dish_count"][0])

    def test_menu_items_without_packages_is_rent_only(self):
        item = VenueMenuItem.objects.create(business=self.business, name="Osh")
        response = self.book(menu_items=[str(item.id)])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["dish_count"], 1)
        self.assertIsNone(response.data["price_per_person"])
        self.assertEqual(Decimal(response.data["total_price"]), Decimal("15000000"))
        self.assertEqual(response.data["selected_menu"][0]["name"], "Osh")


class BusyHoursFormatTests(RestaurantMixin, TestCase):
    def test_times_and_money_are_strings(self):
        self.assertEqual(self.book(start="18:00", end="20:00").status_code, 201)
        response = client_for().get(f"/api/rooms/{self.room.id}/busy-hours/", {"date": str(tomorrow())})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["open_time"], "10:00:00")
        self.assertEqual(response.data["date"], str(tomorrow()))
        self.assertIsInstance(response.data["deposit_amount"], str)
        self.assertEqual(response.data["busy_ranges"], [{"start_time": "18:00:00", "end_time": "20:00:00"}])

    def test_closed_day_has_null_times(self):
        day = tomorrow() + datetime.timedelta(days=1)
        response = client_for().get(f"/api/rooms/{self.room.id}/busy-hours/", {"date": str(day)})
        self.assertFalse(response.data["is_open"])
        self.assertIsNone(response.data["open_time"])

    def test_busy_dates_are_iso_strings(self):
        admin = make_admin()
        owner = make_user("owner_v2", phone="+998905555555")
        venue = make_business(owner, "venue", "Saroy", admin=admin)
        hall = make_hall(venue, all_price=Decimal("1000000"))
        open_day(venue, tomorrow(), start=T10, end=datetime.time(0, 0))
        payload = {"hall": str(hall.id), "date": str(tomorrow()), "guests_count": 10}
        self.assertEqual(self.client.post("/api/reservations/", payload, format="json").status_code, 201)
        response = client_for().get(f"/api/halls/{hall.id}/busy-dates/")
        self.assertEqual(response.data["busy_dates"], [str(tomorrow())])
        self.assertEqual(response.data["all_price"], "1000000.00")
