import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from businesses.models import Business, BusinessApplication, Room
from businesses.services import approve_application, reject_application, submit_application
from notifications import links
from notifications.models import Notification
from notifications.services import notify, notify_many
from reservations.models import Reservation
from reviews.models import Review

User = get_user_model()

PASSWORD = "StrongPass123!"


def make_user(username, phone="+998901234567", **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, full_name=username.title(), phone_number=phone, **extra,
    )


def make_admin(username="boss"):
    return make_user(username, is_staff=True, is_superuser=True)


def auth(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def make_note(user, **kwargs):
    defaults = {"title": "Salom", "kind": Notification.KIND_SYSTEM}
    defaults.update(kwargs)
    return Notification.objects.create(user=user, **defaults)


def make_business(owner, admin, business_type="restaurant", name="Shoxona"):
    application, business, _ = submit_application(
        applicant=owner, business_type=business_type, business_name=name,
    )
    approve_application(application=application, approved_by=admin)
    business.refresh_from_db()
    return business


# ---------------------------------------------------------------------------
# Model va servislar
# ---------------------------------------------------------------------------
class NotificationModelTests(TestCase):
    def setUp(self):
        self.user = make_user("ali")
        self.other = make_user("vali")

    def test_mark_read_sets_read_at_once(self):
        note = make_note(self.user)
        self.assertFalse(note.is_read)
        self.assertIsNone(note.read_at)

        note.mark_read()
        note.refresh_from_db()
        self.assertTrue(note.is_read)
        first_read_at = note.read_at
        self.assertIsNotNone(first_read_at)

        note.mark_read()  # ikkinchi chaqiruv hech narsani o'zgartirmaydi
        note.refresh_from_db()
        self.assertEqual(note.read_at, first_read_at)

    def test_for_user_and_unread_querysets(self):
        mine = make_note(self.user)
        make_note(self.user, is_read=True)
        make_note(self.other)
        self.assertEqual(Notification.objects.for_user(self.user).count(), 2)
        self.assertEqual(list(Notification.objects.for_user(self.user).unread()), [mine])
        self.assertEqual(Notification.objects.unread().count(), 2)

    def test_ordering_newest_first(self):
        first = make_note(self.user, title="1")
        second = make_note(self.user, title="2")
        self.assertEqual(list(Notification.objects.for_user(self.user)), [second, first])

    def test_str(self):
        note = make_note(self.user, title="Sarlavha")
        self.assertEqual(str(note), f"{self.user.pk} — Sarlavha")


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.user = make_user("ali")
        self.other = make_user("vali")

    def test_notify_creates_with_defaults(self):
        note = notify(self.user, title="Xabar")
        self.assertEqual(note.kind, Notification.KIND_SYSTEM)
        self.assertEqual(note.level, Notification.LEVEL_INFO)
        self.assertEqual(note.body, "")
        self.assertEqual(note.link_url, "")
        self.assertFalse(note.is_read)

    def test_notify_truncates_long_text(self):
        note = notify(
            self.user, title="T" * 500, body="B" * 1000, link_url="/" + "l" * 1000,
            kind=Notification.KIND_REVIEW, level=Notification.LEVEL_WARNING,
        )
        note.refresh_from_db()
        self.assertEqual(len(note.title), 160)
        self.assertEqual(len(note.body), 400)
        self.assertEqual(len(note.link_url), 300)
        self.assertEqual(note.kind, Notification.KIND_REVIEW)
        self.assertEqual(note.level, Notification.LEVEL_WARNING)

    def test_notify_none_user_returns_none(self):
        self.assertIsNone(notify(None, title="x"))
        self.assertEqual(Notification.objects.count(), 0)

    def test_notify_many_creates_for_each_and_skips_none(self):
        rows = notify_many(
            [self.user, None, self.other], title="H" * 200, body="B" * 500, link_url="L" * 400,
            kind=Notification.KIND_APPLICATION, level=Notification.LEVEL_SUCCESS,
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(Notification.objects.count(), 2)
        for note in Notification.objects.all():
            self.assertEqual(len(note.title), 160)
            self.assertEqual(len(note.body), 400)
            self.assertEqual(len(note.link_url), 300)
            self.assertEqual(note.kind, Notification.KIND_APPLICATION)
            self.assertEqual(note.level, Notification.LEVEL_SUCCESS)
        self.assertEqual({n.user_id for n in Notification.objects.all()}, {self.user.id, self.other.id})

    def test_notify_many_empty(self):
        self.assertEqual(notify_many([], title="x"), [])
        self.assertEqual(notify_many([None], title="x"), [])
        self.assertEqual(Notification.objects.count(), 0)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class NotificationAPITestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("ali")
        self.other = make_user("vali")
        self.client_user = auth(self.user)
        self.anon = APIClient()


class NotificationListTests(NotificationAPITestCase):
    URL = "/api/notifications/"

    def setUp(self):
        super().setUp()
        self.n1 = make_note(self.user, title="Bron", kind=Notification.KIND_RESERVATION)
        self.n2 = make_note(self.user, title="Sharh", kind=Notification.KIND_REVIEW, is_read=True)
        self.n3 = make_note(self.user, title="Tizim")
        self.foreign = make_note(self.other, title="Begona")

    def test_list_only_own_with_unread_count(self):
        response = self.client_user.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(response.data["unread"], 2)
        ids = {row["id"] for row in response.data["results"]}
        self.assertNotIn(str(self.foreign.pk), ids)
        self.assertIn("total_pages", response.data)
        self.assertIn("current_page", response.data)

        row = response.data["results"][0]
        self.assertEqual(row["title"], "Tizim")  # eng yangisi birinchi
        self.assertEqual(row["kind_display"], "Tizim")
        for key in ("id", "kind", "level", "title", "body", "link_url", "is_read", "read_at", "created_at"):
            self.assertIn(key, row)

    def test_filter_is_read(self):
        response = self.client_user.get(self.URL, {"is_read": "true"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Sharh")
        self.assertEqual(response.data["unread"], 2)  # unread soni filtrga bog'liq emas

        response = self.client_user.get(self.URL, {"is_read": "false"})
        self.assertEqual(response.data["count"], 2)

    def test_filter_kind(self):
        response = self.client_user.get(self.URL, {"kind": "reservation"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.n1.pk))

        response = self.client_user.get(self.URL, {"kind": "application"})
        self.assertEqual(response.data["count"], 0)

    def test_pagination(self):
        response = self.client_user.get(self.URL, {"page_size": 2})
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertIsNotNone(response.data["next"])

    def test_anonymous_401(self):
        response = self.anon.get(self.URL)
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data["error"]["code"], "unauthenticated")

    def test_empty_list_for_new_user(self):
        response = auth(make_user("yangi")).get(self.URL)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["unread"], 0)
        self.assertEqual(response.data["results"], [])


class UnreadCountTests(NotificationAPITestCase):
    URL = "/api/notifications/unread-count/"

    def test_unread_count(self):
        make_note(self.user)
        make_note(self.user)
        make_note(self.user, is_read=True)
        make_note(self.other)
        response = self.client_user.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"unread": 2})

    def test_anonymous_401(self):
        self.assertEqual(self.anon.get(self.URL).status_code, 401)


class MarkReadTests(NotificationAPITestCase):
    def _url(self, pk):
        return f"/api/notifications/{pk}/read/"

    def test_read_own(self):
        note = make_note(self.user)
        response = self.client_user.patch(self._url(note.pk))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_read"])
        self.assertIsNotNone(response.data["read_at"])
        note.refresh_from_db()
        self.assertTrue(note.is_read)
        self.assertEqual(self.client_user.get("/api/notifications/unread-count/").data["unread"], 0)

    def test_read_twice_idempotent(self):
        note = make_note(self.user)
        first = self.client_user.patch(self._url(note.pk))
        second = self.client_user.patch(self._url(note.pk))
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["read_at"], second.data["read_at"])

    def test_read_foreign_404(self):
        note = make_note(self.other)
        response = self.client_user.patch(self._url(note.pk))
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")
        note.refresh_from_db()
        self.assertFalse(note.is_read)

    def test_read_missing_404(self):
        self.assertEqual(self.client_user.patch(self._url(uuid.uuid4())).status_code, 404)

    def test_anonymous_401(self):
        note = make_note(self.user)
        self.assertEqual(self.anon.patch(self._url(note.pk)).status_code, 401)


class ReadAllTests(NotificationAPITestCase):
    URL = "/api/notifications/read-all/"

    def test_read_all_only_own(self):
        a = make_note(self.user)
        b = make_note(self.user)
        make_note(self.user, is_read=True)
        foreign = make_note(self.other)

        response = self.client_user.post(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"unread": 0, "updated": 2})
        self.assertEqual(Notification.objects.for_user(self.user).unread().count(), 0)
        for note in (a, b):
            note.refresh_from_db()
            self.assertTrue(note.is_read)
            self.assertIsNotNone(note.read_at)
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_read)

    def test_read_all_nothing_to_update(self):
        response = self.client_user.post(self.URL)
        self.assertEqual(response.data, {"unread": 0, "updated": 0})

    def test_anonymous_401(self):
        self.assertEqual(self.anon.post(self.URL).status_code, 401)


class DeleteTests(NotificationAPITestCase):
    def _url(self, pk):
        return f"/api/notifications/{pk}/"

    def test_delete_own_204(self):
        note = make_note(self.user)
        response = self.client_user.delete(self._url(note.pk))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Notification.objects.filter(pk=note.pk).exists())

    def test_delete_foreign_404(self):
        note = make_note(self.other)
        response = self.client_user.delete(self._url(note.pk))
        self.assertEqual(response.status_code, 404, response.data)
        self.assertTrue(Notification.objects.filter(pk=note.pk).exists())

    def test_delete_missing_404(self):
        self.assertEqual(self.client_user.delete(self._url(uuid.uuid4())).status_code, 404)

    def test_anonymous_401(self):
        note = make_note(self.user)
        self.assertEqual(self.anon.delete(self._url(note.pk)).status_code, 401)


# ---------------------------------------------------------------------------
# Signallar
# ---------------------------------------------------------------------------
class SignalTestCase(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.staff2 = make_admin("staff2")
        make_user("inactive_staff", is_staff=True, is_active=False)
        self.owner = make_user("ega")
        self.business = make_business(self.owner, self.admin)
        self.customer = make_user("mijoz")
        Notification.objects.all().delete()


class ReservationSignalTests(SignalTestCase):
    def setUp(self):
        super().setUp()
        self.room = Room.objects.create(
            business=self.business, name="VIP", room_type="vip", capacity=10, deposit_tier="premium",
        )

    def _reserve(self):
        return Reservation.objects.create(
            user=self.customer, business=self.business, room=self.room, guests_count=4,
        )

    def test_owner_notified_on_new_reservation(self):
        self._reserve()
        note = Notification.objects.get(user=self.owner)
        self.assertEqual(note.kind, Notification.KIND_RESERVATION)
        self.assertEqual(note.title, "Yangi bron so'rovi")
        self.assertIn("Mijoz", note.body)
        self.assertIn("4 kishi", note.body)
        self.assertEqual(note.link_url, links.OWNER_RESERVATIONS)
        self.assertFalse(Notification.objects.filter(user=self.customer).exists())

    def test_customer_notified_on_status_change(self):
        reservation = self._reserve()
        Notification.objects.all().delete()

        reservation.status = "confirmed"
        reservation.save()

        note = Notification.objects.get(user=self.customer)
        self.assertEqual(note.title, "Broningiz tasdiqlandi")
        self.assertEqual(note.level, Notification.LEVEL_SUCCESS)
        self.assertIn("Shoxona", note.body)
        self.assertEqual(note.link_url, links.CUSTOMER_RESERVATIONS)
        self.assertFalse(Notification.objects.filter(user=self.owner).exists())

        reservation.status = "cancelled"
        reservation.save(update_fields=["status"])
        latest = Notification.objects.filter(user=self.customer).latest("created_at")
        self.assertEqual(latest.title, "Broningiz bekor qilindi")
        self.assertEqual(latest.level, Notification.LEVEL_WARNING)
        self.assertEqual(Notification.objects.filter(user=self.customer).count(), 2)

    def test_no_notification_when_status_unchanged(self):
        reservation = self._reserve()
        Notification.objects.all().delete()

        reservation.special_request = "Deraza yonida"
        reservation.save()
        self.assertEqual(Notification.objects.count(), 0)

        # yangidan yuklangan obyekt ham: holat o'zgarmasa xabar yo'q
        fresh = Reservation.objects.get(pk=reservation.pk)
        fresh.guests_count = 5
        fresh.save()
        self.assertEqual(Notification.objects.count(), 0)

    def test_status_change_on_freshly_loaded_instance(self):
        reservation = self._reserve()
        Notification.objects.all().delete()

        fresh = Reservation.objects.get(pk=reservation.pk)
        fresh.status = "completed"
        fresh.save(update_fields=["status"])
        note = Notification.objects.get(user=self.customer)
        self.assertEqual(note.title, "Broningiz yakunlandi")


class ApplicationSignalTests(SignalTestCase):
    def test_applicant_and_active_staff_notified_on_create(self):
        applicant = make_user("arizachi")
        Notification.objects.all().delete()
        submit_application(applicant=applicant, business_type="venue", business_name="Saroy")

        mine = Notification.objects.get(user=applicant)
        self.assertEqual(mine.kind, Notification.KIND_APPLICATION)
        self.assertEqual(mine.title, "Arizangiz qabul qilindi")
        self.assertIn("Saroy", mine.body)
        self.assertEqual(mine.link_url, links.CUSTOMER_APPLICATION)

        staff_notes = Notification.objects.filter(title="Yangi biznes arizasi")
        self.assertEqual({n.user_id for n in staff_notes}, {self.admin.id, self.staff2.id})
        self.assertIn("To'yxona", staff_notes.first().body)
        self.assertEqual(staff_notes.first().link_url, links.ADMIN_APPLICATIONS)

    def test_applicant_notified_on_approve(self):
        applicant = make_user("arizachi")
        application, _, _ = submit_application(
            applicant=applicant, business_type="restaurant", business_name="Yangi",
        )
        Notification.objects.all().delete()

        approve_application(application=application, approved_by=self.admin)
        note = Notification.objects.get(user=applicant, kind=Notification.KIND_APPLICATION)
        self.assertTrue(note.title.startswith("Arizangiz tasdiqlandi"))
        self.assertEqual(note.level, Notification.LEVEL_SUCCESS)
        self.assertEqual(note.link_url, links.OWNER_HOME)

    def test_applicant_notified_on_reject(self):
        applicant = make_user("arizachi")
        application, _, _ = submit_application(
            applicant=applicant, business_type="restaurant", business_name="Yangi",
        )
        Notification.objects.all().delete()

        reject_application(application=application, rejected_by=self.admin)
        note = Notification.objects.get(user=applicant)
        self.assertEqual(note.title, "Ariza rad etildi")
        self.assertEqual(note.level, Notification.LEVEL_WARNING)

    def test_no_notification_when_application_saved_without_status_change(self):
        applicant = make_user("arizachi")
        application, _, _ = submit_application(
            applicant=applicant, business_type="restaurant", business_name="Yangi",
        )
        Notification.objects.all().delete()

        application.business_name = "Boshqa nom"
        application.save()
        fresh = BusinessApplication.objects.get(pk=application.pk)
        fresh.save()
        self.assertEqual(Notification.objects.count(), 0)


class ReviewSignalTests(SignalTestCase):
    def setUp(self):
        super().setUp()
        self.room = Room.objects.create(
            business=self.business, name="VIP", room_type="vip", capacity=10, deposit_tier="premium",
        )
        self.reservation = Reservation.objects.create(
            user=self.customer, business=self.business, room=self.room, guests_count=2, status="completed",
        )
        Notification.objects.all().delete()

    def test_owner_notified_on_good_review(self):
        Review.objects.create(
            user=self.customer, business=self.business, reservation=self.reservation,
            rating=5, comment="Juda zo'r joy",
        )
        note = Notification.objects.get(user=self.owner)
        self.assertEqual(note.kind, Notification.KIND_REVIEW)
        self.assertEqual(note.title, "Yangi sharh — 5★")
        self.assertEqual(note.body, "Juda zo'r joy")
        self.assertEqual(note.level, Notification.LEVEL_SUCCESS)
        self.assertEqual(note.link_url, links.OWNER_REVIEWS)
        self.assertFalse(Notification.objects.filter(user=self.customer).exists())

    def test_low_rating_is_warning_and_empty_comment_has_default_body(self):
        review = Review.objects.create(
            user=self.customer, business=self.business, reservation=self.reservation, rating=2,
        )
        note = Notification.objects.get(user=self.owner)
        self.assertEqual(note.level, Notification.LEVEL_WARNING)
        self.assertEqual(note.body, "Mijoz baho qoldirdi.")

        review.comment = "Tahrir"
        review.save()
        self.assertEqual(Notification.objects.count(), 1)  # tahrirda xabar yo'q

    def test_long_comment_truncated_to_180(self):
        Review.objects.create(
            user=self.customer, business=self.business, reservation=self.reservation,
            rating=4, comment="x" * 500,
        )
        self.assertEqual(len(Notification.objects.get(user=self.owner).body), 180)


# ---------------------------------------------------------------------------
# Qurilmalar (push token)
# ---------------------------------------------------------------------------
from django.test import override_settings  # noqa: E402
from unittest.mock import patch  # noqa: E402

from notifications import push, telegram  # noqa: E402
from notifications.models import Device  # noqa: E402
from notifications.tasks import send_admin_telegram_task, send_push_task  # noqa: E402


class DeviceAPITests(TestCase):
    def setUp(self):
        self.user = make_user("ali")
        self.client = auth(self.user)

    def test_anonymous_401(self):
        self.assertEqual(APIClient().post("/api/notifications/devices/", {"token": "t"}, format="json").status_code, 401)

    def test_register_then_update(self):
        response = self.client.post("/api/notifications/devices/", {"token": "abc", "platform": "ios"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["platform"], "ios")

        response = self.client.post("/api/notifications/devices/", {"token": "abc", "platform": "android"}, format="json")
        self.assertEqual(response.status_code, 200, "Bir xil token — yangilanadi, dublikat yaratilmaydi")
        self.assertEqual(Device.objects.count(), 1)
        self.assertEqual(Device.objects.get().platform, "android")

    def test_token_moves_to_new_owner(self):
        self.client.post("/api/notifications/devices/", {"token": "shared"}, format="json")
        other = make_user("vali", phone="+998909999999")
        auth(other).post("/api/notifications/devices/", {"token": "shared"}, format="json")
        self.assertEqual(Device.objects.get(token="shared").user, other)

    def test_list_and_delete(self):
        self.client.post("/api/notifications/devices/", {"token": "one"}, format="json")
        Device.objects.create(user=self.user, token="dead", is_active=False)
        response = self.client.get("/api/notifications/devices/")
        self.assertEqual([d["token"] for d in response.data], ["one"], "Faqat faol qurilmalar")

        self.assertEqual(self.client.delete("/api/notifications/devices/one/").status_code, 204)
        self.assertEqual(self.client.delete("/api/notifications/devices/one/").status_code, 404)

    def test_cannot_delete_foreign_device(self):
        Device.objects.create(user=make_user("vali", phone="+998909999999"), token="his")
        self.assertEqual(self.client.delete("/api/notifications/devices/his/").status_code, 404)

    def test_missing_token_400(self):
        self.assertEqual(self.client.post("/api/notifications/devices/", {}, format="json").status_code, 400)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TelegramTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="", TELEGRAM_ADMIN_CHAT_ID="")
    def test_not_configured_is_noop(self):
        self.assertFalse(telegram.is_configured())
        self.assertFalse(telegram.send_admin_message("salom"))
        self.assertFalse(telegram.notify_admins("salom"))

    @override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_ADMIN_CHAT_ID="-100")
    def test_send_admin_message_posts_to_bot_api(self):
        with patch("notifications.telegram.urlopen", return_value=FakeResponse(b'{"ok": true}')) as urlopen:
            self.assertTrue(telegram.send_admin_message("<b>Yangi ariza</b>"))
        request = urlopen.call_args[0][0]
        self.assertIn("bot123:abc/sendMessage", request.full_url)
        body = request.data.decode()
        self.assertIn('"chat_id": "-100"', body)
        self.assertIn("Yangi ariza", body)

    @override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_ADMIN_CHAT_ID="-100")
    def test_network_error_returns_false(self):
        with patch("notifications.telegram.urlopen", side_effect=OSError("down")):
            self.assertFalse(telegram.send_admin_message("x"))
        with patch("notifications.telegram.urlopen", return_value=FakeResponse(b'{"ok": false}')):
            self.assertFalse(telegram.send_admin_message("x"))

    @override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_ADMIN_CHAT_ID="-100")
    def test_notify_admins_queues_task_or_falls_back(self):
        with patch("notifications.tasks.send_admin_telegram_task.delay") as delay:
            self.assertTrue(telegram.notify_admins("x"))
            delay.assert_called_once_with("x")
        with patch("notifications.tasks.send_admin_telegram_task.delay", side_effect=RuntimeError("broker down")), \
             patch("notifications.telegram.send_admin_message", return_value=True) as direct:
            self.assertTrue(telegram.notify_admins("y"))
            direct.assert_called_once_with("y")

    @override_settings(TELEGRAM_BOT_TOKEN="123:abc", TELEGRAM_ADMIN_CHAT_ID="-100")
    def test_new_application_notifies_telegram_group(self):
        admin = make_admin()
        owner = make_user("owner")
        with patch("notifications.signals.notify_admins") as notify_admins:
            submit_application(applicant=owner, business_type="restaurant", business_name="Shoxona")
        self.assertTrue(notify_admins.called)
        self.assertIn("Shoxona", notify_admins.call_args[0][0])
        self.assertIn(owner.phone_number, notify_admins.call_args[0][0])

    def test_task_retries_when_send_fails(self):
        with patch("notifications.telegram.send_admin_message", return_value=True):
            self.assertTrue(send_admin_telegram_task.apply(args=("ok",)).get())


# ---------------------------------------------------------------------------
# Push (FCM)
# ---------------------------------------------------------------------------
class PushTests(TestCase):
    def setUp(self):
        self.user = make_user("ali")

    @override_settings(FCM_SERVICE_ACCOUNT_FILE="", FCM_PROJECT_ID="")
    def test_not_configured_is_noop(self):
        Device.objects.create(user=self.user, token="t1")
        self.assertEqual(push.send_to_user(self.user, title="a", body="b"), 0)
        note = notify(self.user, title="Salom")
        self.assertFalse(push.schedule_push(note))

    @override_settings(FCM_SERVICE_ACCOUNT_FILE="/tmp/sa.json", FCM_PROJECT_ID="feasto")
    def test_send_to_user_and_deactivate_invalid_tokens(self):
        Device.objects.create(user=self.user, token="good")
        Device.objects.create(user=self.user, token="bad")
        Device.objects.create(user=self.user, token="off", is_active=False)

        def fake_send(token, **kwargs):
            return "sent" if token == "good" else "invalid_token"

        with patch("notifications.push.send_to_token", side_effect=fake_send) as send:
            self.assertEqual(push.send_to_user(self.user, title="Salom", body="Dunyo"), 1)
        self.assertEqual(send.call_count, 2, "Faol bo'lmagan qurilmaga yuborilmaydi")
        self.assertFalse(Device.objects.get(token="bad").is_active)
        self.assertTrue(Device.objects.get(token="good").is_active)

    @override_settings(FCM_SERVICE_ACCOUNT_FILE="/tmp/sa.json", FCM_PROJECT_ID="feasto")
    def test_notify_schedules_push_task(self):
        with patch("notifications.tasks.send_push_task.delay") as delay:
            note = notify(self.user, title="Salom")
        delay.assert_called_once_with(str(note.pk))

    @override_settings(FCM_SERVICE_ACCOUNT_FILE="/tmp/sa.json", FCM_PROJECT_ID="feasto")
    def test_send_push_task_uses_notification_content(self):
        Device.objects.create(user=self.user, token="good")
        with patch("notifications.tasks.send_push_task.delay"):
            note = notify(self.user, title="Bron tasdiqlandi", body="Shoxona", kind=Notification.KIND_RESERVATION, link_url=links.CUSTOMER_RESERVATIONS)
        with patch("notifications.push.send_to_token", return_value="sent") as send:
            self.assertEqual(send_push_task.apply(args=(str(note.pk),)).get(), 1)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["title"], "Bron tasdiqlandi")
        self.assertEqual(kwargs["data"]["link_url"], links.CUSTOMER_RESERVATIONS)
        self.assertEqual(send_push_task.apply(args=(str(uuid.uuid4()),)).get(), 0)
