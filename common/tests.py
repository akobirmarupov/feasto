import io
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.http import Http404
from notifications import links
from django.test import TestCase
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, Throttled
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from account.models import User
from common.cache import (
    BUSINESS_VERSION_KEY,
    build_cache_key,
    cached_response,
    get_business_version,
    invalidate_business_cache,
)
from common.exceptions import BadRequest, Conflict, api_exception_handler
from common.models import Feedback, PlatformSettings, _solo_memo
from common.pagination import BusinessFeedCursorPagination, ReviewsPagination, StandardResultsPagination
from common.permissions import (
    HasActiveSubscription,
    HasContactPhone,
    IsBusinessRole,
    IsCustomer,
    IsSuperAdmin,
)
from common.throttles import FeedbackThrottle, LoginThrottle
from notifications.models import Notification

PASSWORD = "StrongPass123!"

HEALTH_URL = "/api/health/"
SETTINGS_URL = "/api/settings/"
ADMIN_SETTINGS_URL = "/api/admin/settings/"
FEEDBACK_URL = "/api/feedback/"
ADMIN_FEEDBACK_URL = "/api/admin/feedback/"

LONG_MESSAGE = "Qidiruv juda sekin ishlayapti, iltimos tezlashtiring."


# ---------------------------------------------------------------- yordamchilar

def make_user(username="ali", full_name="Ali Valiyev", phone="+998901234567", **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, full_name=full_name, phone_number=phone, **extra,
    )


def make_admin(username="admin", **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, full_name="Admin Adminov",
        is_staff=True, is_superuser=True, **extra,
    )


def auth_client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def reset_platform_cache():
    cache.clear()
    _solo_memo.set(None)


def make_business(owner, admin, business_type="restaurant", approve=True):
    from businesses.services import approve_application, submit_application

    application, business, _ = submit_application(
        applicant=owner, business_type=business_type, business_name="Shoxona",
    )
    if approve:
        approve_application(application=application, approved_by=admin)
    owner.refresh_from_db()
    return application, business


def fake_request(user=None, method="GET", **kwargs):
    factory = APIRequestFactory()
    request = getattr(factory, method.lower())("/", **kwargs)
    request.user = user
    return request


class DummyView:
    pass


# ---------------------------------------------------------------- health

class HealthCheckTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_health_ok_anonymous(self):
        response = APIClient().get(HEALTH_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "healthy")
        self.assertEqual(response.data["checks"]["database"], "ok")
        self.assertEqual(response.data["checks"]["cache"], "ok")
        self.assertIn(response.data["checks"]["celery"], ("ok", "unknown"))
        self.assertEqual(response.data["version"], "1.0.0")
        self.assertTrue(response["X-Request-ID"])

    def test_health_database_error_503(self):
        with patch("common.routes.health_api.connection") as connection, \
                self.assertLogs("common", level="ERROR"):
            connection.cursor.side_effect = Exception("db down")
            response = APIClient().get(HEALTH_URL)
        self.assertEqual(response.status_code, 503, response.data)
        self.assertEqual(response.data["status"], "unhealthy")
        self.assertEqual(response.data["checks"]["database"], "error")

    def test_health_cache_error_is_only_degraded(self):
        with patch("common.routes.health_api.cache") as fake_cache, \
                self.assertLogs("common", level="ERROR"):
            fake_cache.set.side_effect = Exception("redis down")
            response = APIClient().get(HEALTH_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["checks"]["cache"], "error")
        self.assertEqual(response.data["status"], "healthy")


# ---------------------------------------------------------------- platforma sozlamalari

class PlatformSettingsModelTests(TestCase):
    def setUp(self):
        reset_platform_cache()

    def test_get_solo_creates_single_row_with_pk_1(self):
        obj = PlatformSettings.get_solo()
        self.assertEqual(obj.pk, 1)
        self.assertEqual(obj.trial_days, 7)
        self.assertEqual(PlatformSettings.objects.count(), 1)

    def test_save_always_forces_pk_1(self):
        PlatformSettings(trial_days=3).save()
        PlatformSettings(trial_days=9).save()
        self.assertEqual(PlatformSettings.objects.count(), 1)
        self.assertEqual(PlatformSettings.objects.get().trial_days, 9)

    def test_get_solo_is_memoized_and_cached(self):
        PlatformSettings.get_solo()
        with self.assertNumQueries(0):
            PlatformSettings.get_solo()
        # memo tozalansa ham keshdan olinadi
        _solo_memo.set(None)
        with self.assertNumQueries(0):
            obj = PlatformSettings.get_solo()
        self.assertEqual(obj.pk, 1)
        self.assertIsNotNone(cache.get(PlatformSettings.CACHE_KEY))

    def test_save_invalidates_cache_and_memo(self):
        obj = PlatformSettings.get_solo()
        obj.trial_days = 14
        obj.save()
        self.assertIsNone(cache.get(PlatformSettings.CACHE_KEY))
        self.assertIsNone(_solo_memo.get())
        self.assertEqual(PlatformSettings.get_solo().trial_days, 14)

    def test_queryset_update_is_not_seen_until_cache_reset(self):
        PlatformSettings.get_solo()
        PlatformSettings.objects.filter(pk=1).update(trial_days=30)
        self.assertEqual(PlatformSettings.get_solo().trial_days, 7)
        reset_platform_cache()
        self.assertEqual(PlatformSettings.get_solo().trial_days, 30)


class PublicSettingsTests(TestCase):
    def setUp(self):
        reset_platform_cache()
        call_command("seed_platform", verbosity=0, stdout=io.StringIO())

    def test_public_settings_anonymous(self):
        response = APIClient().get(SETTINGS_URL)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertTrue(data["admin_telegram"].startswith("@"))
        self.assertEqual(data["trial_days"], 7)
        self.assertEqual(data["subscription_days"], 30)
        self.assertEqual(set(data["deposits"]), {"room_premium", "room_pro", "venue"})
        self.assertEqual(len(data["plans"]), 4)
        plan = data["plans"][0]
        self.assertEqual(plan["business_type"], "restaurant")
        self.assertEqual(plan["duration_months"], 1)
        self.assertIn("price_per_month", plan)
        # 3 oylik reja tejamkorlikni ko'rsatadi
        quarterly = next(p for p in data["plans"] if p["duration_months"] == 3 and p["business_type"] == "restaurant")
        self.assertEqual(Decimal(quarterly["savings"]), Decimal("150000"))

    def test_public_settings_reflects_admin_patch(self):
        admin_client = auth_client(make_admin())
        response = admin_client.patch(ADMIN_SETTINGS_URL, {"trial_days": 10, "admin_telegram_username": "feasto"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["trial_days"], 10)
        self.assertEqual(response.data["admin_telegram"], "@feasto")

        public = APIClient().get(SETTINGS_URL)
        self.assertEqual(public.data["trial_days"], 10)
        self.assertEqual(public.data["admin_telegram"], "@feasto")


class AdminSettingsTests(TestCase):
    def setUp(self):
        reset_platform_cache()
        self.admin = make_admin()
        self.client = auth_client(self.admin)

    def test_anonymous_401(self):
        response = APIClient().get(ADMIN_SETTINGS_URL)
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data["error"]["code"], "unauthenticated")

    def test_regular_user_403(self):
        client = auth_client(make_user())
        self.assertEqual(client.get(ADMIN_SETTINGS_URL).status_code, 403)
        response = client.patch(ADMIN_SETTINGS_URL, {"trial_days": 1}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertEqual(PlatformSettings.get_solo().trial_days, 7)

    def test_business_user_403(self):
        client = auth_client(make_user(role="business"))
        self.assertEqual(client.get(ADMIN_SETTINGS_URL).status_code, 403)

    def test_admin_get(self):
        response = self.client.get(ADMIN_SETTINGS_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["admin_telegram"], f"@{response.data['admin_telegram_username']}")
        self.assertEqual(Decimal(response.data["room_deposit_premium"]), Decimal("99000"))

    def test_admin_patch_deposits(self):
        response = self.client.patch(
            ADMIN_SETTINGS_URL,
            {"room_deposit_premium": "120000", "venue_deposit": "700000.50"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        obj = PlatformSettings.objects.get(pk=1)
        self.assertEqual(obj.room_deposit_premium, Decimal("120000"))
        self.assertEqual(obj.venue_deposit, Decimal("700000.50"))

    def test_admin_patch_negative_deposit_400(self):
        response = self.client.patch(ADMIN_SETTINGS_URL, {"room_deposit_pro": "-1"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("room_deposit_pro", response.data["error"]["details"])

    def test_admin_patch_negative_trial_days_400(self):
        response = self.client.patch(ADMIN_SETTINGS_URL, {"trial_days": -3}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_staff_without_superuser_can_patch(self):
        staff = make_user(username="staff", phone=None, is_staff=True)
        response = auth_client(staff).patch(ADMIN_SETTINGS_URL, {"subscription_days": 45}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["subscription_days"], 45)


# ---------------------------------------------------------------- feedback

class FeedbackCreateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_admin()
        self.inactive_staff = make_user(username="oldstaff", phone=None, is_staff=True, is_active=False)

    def test_anonymous_feedback_201(self):
        response = APIClient().post(
            FEEDBACK_URL,
            {"kind": "problem", "message": LONG_MESSAGE, "page": "/qidiruv/", "contact": "@ali"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("detail", response.data)
        feedback = Feedback.objects.get()
        self.assertIsNone(feedback.user)
        self.assertEqual(feedback.kind, "problem")
        self.assertEqual(feedback.page, "/qidiruv/")
        self.assertEqual(feedback.contact, "@ali")
        self.assertEqual(feedback.status, Feedback.STATUS_NEW)

    def test_authenticated_feedback_binds_user(self):
        user = make_user()
        response = auth_client(user).post(FEEDBACK_URL, {"message": LONG_MESSAGE}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        feedback = Feedback.objects.get()
        self.assertEqual(feedback.user, user)
        self.assertEqual(feedback.kind, Feedback.KIND_IDEA)

    def test_short_message_400(self):
        response = APIClient().post(FEEDBACK_URL, {"message": "qisqa"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertIn("message", response.data["error"]["details"])
        self.assertEqual(Feedback.objects.count(), 0)

    def test_whitespace_padding_does_not_count(self):
        response = APIClient().post(FEEDBACK_URL, {"message": "   qisqa      "}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_invalid_kind_400(self):
        response = APIClient().post(FEEDBACK_URL, {"kind": "spam", "message": LONG_MESSAGE}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("kind", response.data["error"]["details"])

    def test_message_is_stripped(self):
        APIClient().post(FEEDBACK_URL, {"message": f"  {LONG_MESSAGE}  "}, format="json")
        self.assertEqual(Feedback.objects.get().message, LONG_MESSAGE)

    def test_page_is_truncated_to_200(self):
        APIClient().post(FEEDBACK_URL, {"message": LONG_MESSAGE, "page": "/" + "x" * 500}, format="json")
        self.assertEqual(len(Feedback.objects.get().page), 200)

    def test_active_staff_get_notification(self):
        APIClient().post(FEEDBACK_URL, {"kind": "problem", "message": LONG_MESSAGE}, format="json")
        notification = Notification.objects.get(user=self.staff)
        self.assertEqual(notification.kind, Notification.KIND_SYSTEM)
        self.assertEqual(notification.title, "Yangi taklif: muammo")
        self.assertEqual(notification.body, LONG_MESSAGE)
        self.assertEqual(notification.link_url, links.ADMIN_FEEDBACK)
        self.assertFalse(Notification.objects.filter(user=self.inactive_staff).exists())

    def test_regular_user_not_notified(self):
        user = make_user()
        APIClient().post(FEEDBACK_URL, {"message": LONG_MESSAGE}, format="json")
        self.assertFalse(Notification.objects.filter(user=user).exists())
        self.assertEqual(Notification.objects.count(), 1)

    def test_notification_failure_does_not_break_creation(self):
        with patch("notifications.services.notify_many", side_effect=RuntimeError("boom")), \
                self.assertLogs("common", level="WARNING"):
            response = APIClient().post(FEEDBACK_URL, {"message": LONG_MESSAGE}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Feedback.objects.count(), 1)

    def test_feedback_throttle_10_per_day(self):
        client = APIClient()
        for _ in range(10):
            response = client.post(FEEDBACK_URL, {"message": LONG_MESSAGE}, format="json")
            self.assertEqual(response.status_code, 201, response.data)
        response = client.post(FEEDBACK_URL, {"message": LONG_MESSAGE}, format="json")
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(response.data["error"]["code"], "too_many_requests")
        self.assertEqual(Feedback.objects.count(), 10)


class FeedbackModelTests(TestCase):
    def test_short_truncates_and_flattens(self):
        long_text = "a" * 100
        feedback = Feedback(message=f"  {long_text}\nikkinchi qator ")
        self.assertEqual(feedback.short, "a" * 77 + "…")
        self.assertEqual(len(feedback.short), 78)

        feedback = Feedback(message="Salom\ndunyo")
        self.assertEqual(feedback.short, "Salom dunyo")

    def test_short_keeps_80_chars(self):
        feedback = Feedback(message="b" * 80)
        self.assertEqual(feedback.short, "b" * 80)

    def test_str(self):
        self.assertEqual(str(Feedback(kind="problem")), "Muammo — mehmon")
        user = make_user()
        self.assertEqual(str(Feedback(kind="idea", user=user)), "Taklif — ali")


class AdminFeedbackTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.user = make_user()
        self.client = auth_client(self.admin)
        self.f_new = Feedback.objects.create(kind="idea", message=LONG_MESSAGE, user=self.user)
        self.f_seen = Feedback.objects.create(kind="problem", message=LONG_MESSAGE, status="seen")
        self.f_done = Feedback.objects.create(kind="problem", message=LONG_MESSAGE, status="done")

    def test_regular_user_403(self):
        response = auth_client(self.user).get(ADMIN_FEEDBACK_URL)
        self.assertEqual(response.status_code, 403, response.data)
        response = auth_client(self.user).get(f"{ADMIN_FEEDBACK_URL}{self.f_new.pk}/")
        self.assertEqual(response.status_code, 403, response.data)

    def test_anonymous_401(self):
        response = APIClient().get(ADMIN_FEEDBACK_URL)
        self.assertEqual(response.status_code, 401, response.data)

    def test_list_count_and_unread(self):
        response = self.client.get(ADMIN_FEEDBACK_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(response.data["unread"], 1)
        self.assertEqual(response.data["total_pages"], 1)
        self.assertEqual(response.data["current_page"], 1)
        row = next(r for r in response.data["results"] if r["id"] == str(self.f_new.pk))
        self.assertEqual(row["author_name"], "Ali Valiyev")
        self.assertEqual(row["author_phone"], "+998901234567")
        self.assertEqual(row["author_role"], "user")
        self.assertEqual(row["kind_display"], "Taklif")
        self.assertEqual(row["status_display"], "Yangi")
        guest = next(r for r in response.data["results"] if r["id"] == str(self.f_seen.pk))
        self.assertEqual(guest["author_name"], "Mehmon")
        self.assertIsNone(guest["user"])

    def test_filter_by_status_keeps_unread(self):
        response = self.client.get(ADMIN_FEEDBACK_URL, {"status": "done"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.f_done.pk))
        self.assertEqual(response.data["unread"], 1)

    def test_filter_by_kind(self):
        response = self.client.get(ADMIN_FEEDBACK_URL, {"kind": "problem"})
        self.assertEqual(response.data["count"], 2)
        response = self.client.get(ADMIN_FEEDBACK_URL, {"kind": "problem", "status": "seen"})
        self.assertEqual(response.data["count"], 1)

    def test_detail_get(self):
        response = self.client.get(f"{ADMIN_FEEDBACK_URL}{self.f_new.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["message"], LONG_MESSAGE)
        self.assertEqual(response.data["user"], self.user.pk)

    def test_detail_404(self):
        response = self.client.get(f"{ADMIN_FEEDBACK_URL}{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(response.data["error"]["message"], "Taklif topilmadi.")

    def test_patch_status_and_note(self):
        response = self.client.patch(
            f"{ADMIN_FEEDBACK_URL}{self.f_new.pk}/", {"status": "done", "admin_note": "hal qilindi"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "done")
        self.assertEqual(response.data["status_display"], "Hal qilingan")
        self.f_new.refresh_from_db()
        self.assertEqual(self.f_new.admin_note, "hal qilindi")
        self.assertEqual(self.client.get(ADMIN_FEEDBACK_URL).data["unread"], 0)

    def test_patch_invalid_status_400(self):
        response = self.client.patch(f"{ADMIN_FEEDBACK_URL}{self.f_new.pk}/", {"status": "closed"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("status", response.data["error"]["details"])

    def test_patch_read_only_fields_ignored(self):
        response = self.client.patch(
            f"{ADMIN_FEEDBACK_URL}{self.f_new.pk}/",
            {"message": "o'zgartirildi", "kind": "other", "user": None, "page": "/x/"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.f_new.refresh_from_db()
        self.assertEqual(self.f_new.message, LONG_MESSAGE)
        self.assertEqual(self.f_new.kind, "idea")
        self.assertEqual(self.f_new.user, self.user)

    def test_list_pagination(self):
        for _ in range(22):
            Feedback.objects.create(message=LONG_MESSAGE)
        response = self.client.get(ADMIN_FEEDBACK_URL)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertIsNotNone(response.data["next"])
        self.assertIsNone(response.data["previous"])

        response = self.client.get(ADMIN_FEEDBACK_URL, {"page": 2, "page_size": 10})
        self.assertEqual(response.data["current_page"], 2)
        self.assertEqual(response.data["total_pages"], 3)
        self.assertEqual(len(response.data["results"]), 10)
        self.assertIsNotNone(response.data["previous"])

        response = self.client.get(ADMIN_FEEDBACK_URL, {"page": 99})
        self.assertEqual(response.status_code, 404, response.data)


# ---------------------------------------------------------------- exceptions

class ExceptionHandlerTests(TestCase):
    def handle(self, exc):
        return api_exception_handler(exc, {"view": DummyView()})

    def assertUnified(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code, response.data)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["error"]["code"], code)
        self.assertIsInstance(response.data["error"]["message"], str)
        self.assertIn("request_id", response.data)

    def test_bad_request_default(self):
        response = self.handle(BadRequest())
        self.assertUnified(response, 400, "bad_request")
        self.assertEqual(response.data["error"]["message"], "Noto'g'ri so'rov.")
        self.assertNotIn("details", response.data["error"])

    def test_bad_request_custom_code(self):
        response = self.handle(BadRequest("Sinov ishlatilgan.", code="trial_used"))
        self.assertUnified(response, 400, "trial_used")
        self.assertEqual(response.data["error"]["message"], "Sinov ishlatilgan.")

    def test_conflict(self):
        response = self.handle(Conflict("Band."))
        self.assertUnified(response, 409, "conflict")
        self.assertEqual(response.data["error"]["message"], "Band.")

    def test_permission_denied_custom_code(self):
        response = self.handle(PermissionDenied("Telefon kerak.", code="phone_required"))
        self.assertUnified(response, 403, "phone_required")
        self.assertUnified(self.handle(PermissionDenied()), 403, "permission_denied")

    def test_django_http404_and_permission_denied_are_converted(self):
        self.assertUnified(self.handle(Http404()), 404, "not_found")
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
        self.assertUnified(self.handle(DjangoPermissionDenied()), 403, "permission_denied")
        self.assertUnified(self.handle(NotFound("Yo'q.")), 404, "not_found")

    def test_serializer_validation_error_has_details(self):
        response = self.handle(serializers.ValidationError({"phone_number": ["Noto'g'ri format."]}))
        self.assertUnified(response, 400, "bad_request")
        self.assertEqual(response.data["error"]["message"], "Noto'g'ri format.")
        self.assertEqual(response.data["error"]["details"], {"phone_number": ["Noto'g'ri format."]})

    def test_django_validation_error_dict(self):
        response = self.handle(DjangoValidationError({"name": ["Bo'sh."]}))
        self.assertUnified(response, 400, "bad_request")
        self.assertIn("name", response.data["error"]["details"])

    def test_django_validation_error_list(self):
        response = self.handle(DjangoValidationError("Umumiy xato."))
        self.assertUnified(response, 400, "bad_request")
        self.assertEqual(response.data["error"]["message"], "Umumiy xato.")
        self.assertEqual(response.data["error"]["details"], {"errors": ["Umumiy xato."]})

    def test_throttled(self):
        with self.assertLogs("common", level="WARNING"):
            response = self.handle(Throttled(wait=30))
        self.assertUnified(response, 429, "too_many_requests")

    def test_integrity_error_409(self):
        with self.assertLogs("common", level="ERROR"):
            response = self.handle(IntegrityError("duplicate"))
        self.assertUnified(response, 409, "conflict")

    def test_unhandled_exception_500(self):
        with self.assertLogs("common", level="ERROR"):
            response = self.handle(RuntimeError("boom"))
        self.assertUnified(response, 500, "server_error")
        self.assertNotIn("boom", response.data["error"]["message"])

    def test_real_endpoint_uses_unified_format_and_request_id_header(self):
        response = APIClient().get(ADMIN_SETTINGS_URL, HTTP_X_REQUEST_ID="req-abc_123!!")
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data["error"]["code"], "unauthenticated")
        self.assertEqual(response.data["request_id"], "req-abc_123")
        self.assertEqual(response["X-Request-ID"], "req-abc_123")

    def test_method_not_allowed_405(self):
        response = APIClient().put(HEALTH_URL)
        self.assertEqual(response.status_code, 405, response.data)
        self.assertEqual(response.data["error"]["code"], "method_not_allowed")


# ---------------------------------------------------------------- permissions (birlik)

class IsSuperAdminAndCustomerTests(TestCase):
    def test_is_super_admin(self):
        perm = IsSuperAdmin()
        self.assertTrue(perm.has_permission(fake_request(make_admin()), DummyView()))
        self.assertTrue(perm.has_permission(fake_request(make_user(username="s", phone=None, is_staff=True)), DummyView()))
        self.assertFalse(perm.has_permission(fake_request(make_user(role="admin")), DummyView()))
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(perm.has_permission(fake_request(AnonymousUser()), DummyView()))

    def test_is_customer(self):
        perm = IsCustomer()
        self.assertTrue(perm.has_permission(fake_request(make_user()), DummyView()))
        self.assertTrue(perm.has_permission(fake_request(make_user(username="b", phone=None, role="business")), DummyView()))
        self.assertFalse(perm.has_permission(fake_request(make_admin()), DummyView()))
        self.assertFalse(perm.has_permission(fake_request(make_user(username="s", phone=None, is_staff=True)), DummyView()))
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(perm.has_permission(fake_request(AnonymousUser()), DummyView()))
        self.assertIn("Platforma egasi bron qila olmaydi", perm.message)

    def test_staff_cannot_create_reservation_403(self):
        staff = make_admin(phone_number="+998901111111")
        response = auth_client(staff).post("/api/reservations/", {}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("Platforma egasi bron qila olmaydi", response.data["error"]["message"])


class HasContactPhoneTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_permission_logic(self):
        perm = HasContactPhone()
        self.assertTrue(perm.has_permission(fake_request(make_user()), DummyView()))
        self.assertFalse(perm.has_permission(fake_request(make_user(username="np", phone=None)), DummyView()))
        self.assertEqual(perm.code, "phone_required")
        self.assertEqual(perm.message, "Davom etish uchun aloqa raqamingizni kiriting.")

    def test_view_can_override_message(self):
        view = DummyView()
        view.phone_message = "Bron uchun raqam kerak."
        perm = HasContactPhone()
        perm.has_permission(fake_request(make_user(username="np", phone=None)), view)
        self.assertEqual(perm.message, "Bron uchun raqam kerak.")

    def test_endpoint_returns_phone_required_code(self):
        user = make_user(username="np", phone=None)
        response = auth_client(user).post(
            "/api/business-applications/", {"business_type": "restaurant", "business_name": "X"}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "phone_required")


class IsBusinessRoleTests(TestCase):
    def setUp(self):
        reset_platform_cache()
        self.admin = make_admin()
        self.view = DummyView()

    def check(self, user):
        perm = IsBusinessRole()
        return perm.has_permission(fake_request(user), self.view), perm.message

    def test_anonymous_and_plain_user_denied_with_default_message(self):
        from django.contrib.auth.models import AnonymousUser
        allowed, message = self.check(AnonymousUser())
        self.assertFalse(allowed)
        allowed, message = self.check(make_user())
        self.assertFalse(allowed)
        self.assertEqual(message, "Bu bo'lim faqat biznes egalari uchun.")

    def test_staff_denied_with_panel_message(self):
        allowed, message = self.check(self.admin)
        self.assertFalse(allowed)
        self.assertIn("boshqaruv paneliga", message)

    def test_business_role_allowed(self):
        allowed, _ = self.check(make_user(role="business"))
        self.assertTrue(allowed)

    def test_pending_trial_application_message_mentions_trial_days(self):
        owner = make_user()
        make_business(owner, self.admin, approve=False)
        allowed, message = self.check(owner)
        self.assertFalse(allowed)
        self.assertIn("hali tasdiqlanmagan", message)
        self.assertIn("7 kunlik bepul sinov", message)

    def test_pending_paid_application_message_mentions_plan(self):
        from businesses.services import submit_application
        from subscriptions.services import get_or_create_plan

        owner = make_user()
        plan = get_or_create_plan("restaurant", 3)
        submit_application(applicant=owner, business_type="restaurant", business_name="X", plan=plan)
        allowed, message = self.check(owner)
        self.assertFalse(allowed)
        self.assertIn("to'lovingizni", message)
        self.assertIn("3 oy", message)

    def test_rejected_application_message(self):
        from businesses.services import reject_application

        owner = make_user()
        application, _ = make_business(owner, self.admin, approve=False)
        reject_application(application=application, rejected_by=self.admin)
        allowed, message = self.check(owner)
        self.assertFalse(allowed)
        self.assertIn("rad etilgan", message)

    def test_endpoint_pending_owner_403_and_staff_403(self):
        owner = make_user()
        make_business(owner, self.admin, approve=False)
        response = auth_client(owner).get("/api/owner/rooms/")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("tasdiqlanmagan", response.data["error"]["message"])

        response = auth_client(self.admin).get("/api/owner/rooms/")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("boshqaruv paneliga", response.data["error"]["message"])


class HasActiveSubscriptionTests(TestCase):
    def setUp(self):
        reset_platform_cache()
        self.admin = make_admin()
        self.owner = make_user()
        self.view = DummyView()

    def check(self, user, method="POST"):
        perm = HasActiveSubscription()
        return perm.has_permission(fake_request(user, method=method), self.view), perm.message

    def test_safe_methods_always_allowed(self):
        from django.contrib.auth.models import AnonymousUser
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertTrue(self.check(AnonymousUser(), method)[0])
            self.assertTrue(self.check(self.owner, method)[0])

    def test_write_anonymous_and_no_business_denied(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(self.check(AnonymousUser())[0])
        self.assertFalse(self.check(self.owner)[0])

    def test_staff_allowed_to_write(self):
        self.assertTrue(self.check(self.admin)[0])

    def test_trial_and_active_allowed(self):
        from subscriptions.models import Subscription

        _, business = make_business(self.owner, self.admin)
        self.assertEqual(business.subscription.status, "trial")
        self.assertTrue(self.check(self.owner)[0])
        Subscription.objects.filter(business=business).update(status="active")
        self.owner = User.objects.get(pk=self.owner.pk)
        self.assertTrue(self.check(self.owner)[0])

    def test_expired_denied_with_message(self):
        from subscriptions.models import Subscription

        _, business = make_business(self.owner, self.admin)
        Subscription.objects.filter(business=business).update(status="expired")
        allowed, message = self.check(User.objects.get(pk=self.owner.pk))
        self.assertFalse(allowed)
        self.assertIn("Obunangiz muddati tugagan", message)

    def test_approved_without_subscription_message(self):
        _, business = make_business(self.owner, self.admin)
        business.subscription.delete()
        allowed, message = self.check(User.objects.get(pk=self.owner.pk))
        self.assertFalse(allowed)
        self.assertIn("hali ochilmagan", message)

    def test_pending_application_message(self):
        make_business(self.owner, self.admin, approve=False)
        allowed, message = self.check(self.owner)
        self.assertFalse(allowed)
        self.assertIn("hali tasdiqlanmagan", message)

    def test_endpoint_expired_read_200_write_403(self):
        from subscriptions.models import Subscription

        _, business = make_business(self.owner, self.admin)
        Subscription.objects.filter(business=business).update(status="expired")
        client = auth_client(User.objects.get(pk=self.owner.pk))

        response = client.get("/api/owner/rooms/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 0)

        response = client.post(
            "/api/owner/rooms/",
            {"name": "VIP 1", "room_type": "vip", "capacity": 6, "deposit_tier": "premium"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertIn("Obunangiz muddati tugagan", response.data["error"]["message"])

    def test_endpoint_trial_write_201(self):
        make_business(self.owner, self.admin)
        client = auth_client(User.objects.get(pk=self.owner.pk))
        response = client.post(
            "/api/owner/rooms/",
            {"name": "VIP 1", "room_type": "vip", "capacity": 6, "deposit_tier": "premium"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)


# ---------------------------------------------------------------- pagination

class PaginationTests(TestCase):
    def test_standard_page_size_and_cap(self):
        paginator = StandardResultsPagination()
        factory = APIRequestFactory()
        self.assertEqual(paginator.get_page_size(Request(factory.get("/"))), 20)
        self.assertEqual(paginator.get_page_size(Request(factory.get("/", {"page_size": 5}))), 5)
        self.assertEqual(paginator.get_page_size(Request(factory.get("/", {"page_size": 1000}))), 100)
        self.assertEqual(paginator.get_page_size(Request(factory.get("/", {"page_size": "abc"}))), 20)

    def test_standard_response_shape(self):
        for i in range(7):
            Feedback.objects.create(message=f"{LONG_MESSAGE} {i}")
        paginator = StandardResultsPagination()
        request = Request(APIRequestFactory().get("/", {"page_size": 3, "page": 2}))
        page = paginator.paginate_queryset(Feedback.objects.order_by("created_at"), request)
        response = paginator.get_paginated_response([f.pk for f in page])
        self.assertEqual(set(response.data), {"count", "total_pages", "current_page", "next", "previous", "results"})
        self.assertEqual(response.data["count"], 7)
        self.assertEqual(response.data["total_pages"], 3)
        self.assertEqual(response.data["current_page"], 2)
        self.assertEqual(len(response.data["results"]), 3)
        self.assertIn("page=3", response.data["next"])
        self.assertIsNotNone(response.data["previous"])

    def test_reviews_and_cursor_settings(self):
        self.assertEqual(ReviewsPagination.page_size, 10)
        self.assertEqual(ReviewsPagination.max_page_size, 50)
        self.assertTrue(issubclass(ReviewsPagination, StandardResultsPagination))
        self.assertEqual(BusinessFeedCursorPagination.page_size, 15)
        self.assertEqual(BusinessFeedCursorPagination.ordering, "-created_at")
        self.assertEqual(BusinessFeedCursorPagination.cursor_query_param, "cursor")


# ---------------------------------------------------------------- cache

class CacheHelpersTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_version_defaults_to_1(self):
        self.assertEqual(get_business_version(), 1)
        self.assertEqual(cache.get(BUSINESS_VERSION_KEY), 1)

    def test_build_cache_key_is_deterministic_and_versioned(self):
        key1 = build_cache_key("biz_list", "a", 1, None)
        key2 = build_cache_key("biz_list", "a", 1, None)
        self.assertEqual(key1, key2)
        self.assertTrue(key1.startswith("biz_list:v1:"))
        self.assertEqual(len(key1.split(":")[-1]), 20)
        self.assertNotEqual(key1, build_cache_key("biz_list", "a", 2, None))
        self.assertNotEqual(key1, build_cache_key("biz_detail", "a", 1, None))

    def test_invalidate_bumps_version_and_changes_keys(self):
        old_key = build_cache_key("biz_list", "x")
        invalidate_business_cache()
        self.assertEqual(get_business_version(), 2)
        new_key = build_cache_key("biz_list", "x")
        self.assertNotEqual(old_key, new_key)
        self.assertTrue(new_key.startswith("biz_list:v2:"))

    def test_invalidate_when_version_missing(self):
        # kesh bo'sh: incr ValueError beradi, versiya 2 dan boshlanadi
        self.assertIsNone(cache.get(BUSINESS_VERSION_KEY))
        invalidate_business_cache()
        self.assertEqual(get_business_version(), 2)
        invalidate_business_cache()
        self.assertEqual(get_business_version(), 3)

    def test_cached_response_calls_producer_once(self):
        calls = []

        def producer():
            calls.append(1)
            return {"rows": [1, 2, 3]}

        first = cached_response("k1", 60, producer)
        second = cached_response("k1", 60, producer)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cache.get("k1"), {"rows": [1, 2, 3]})

    def test_cached_response_none_is_not_cached(self):
        calls = []

        def producer():
            calls.append(1)
            return None

        cached_response("k2", 60, producer)
        cached_response("k2", 60, producer)
        self.assertEqual(len(calls), 2)

    def test_cached_response_falsy_values_are_cached(self):
        calls = []

        def producer():
            calls.append(1)
            return []

        self.assertEqual(cached_response("k3", 60, producer), [])
        self.assertEqual(cached_response("k3", 60, producer), [])
        self.assertEqual(len(calls), 1)

    def test_invalidation_makes_old_entry_unreachable(self):
        key = build_cache_key("biz_list", "q")
        cached_response(key, 60, lambda: "old")
        invalidate_business_cache()
        new_key = build_cache_key("biz_list", "q")
        self.assertEqual(cached_response(new_key, 60, lambda: "new"), "new")


# ---------------------------------------------------------------- throttles

class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_rates_from_settings(self):
        self.assertEqual(LoginThrottle().get_rate(), "10/min")
        self.assertEqual(FeedbackThrottle().get_rate(), "10/day")
        self.assertEqual(FeedbackThrottle.scope, "feedback")
        self.assertEqual(LoginThrottle.scope, "login")

    def test_login_throttle_key_includes_ip_and_username(self):
        factory = APIRequestFactory()
        request = Request(factory.post("/", {"username": "ali", "password": "x"}, format="json"), parsers=[JSONParser()])
        key = LoginThrottle().get_cache_key(request, DummyView())
        self.assertTrue(key.startswith("throttle_login_"))
        self.assertTrue(key.endswith(":ali"), key)
        self.assertIn("127.0.0.1", key)

        other = Request(factory.post("/", {"username": "vali", "password": "x"}, format="json"), parsers=[JSONParser()])
        self.assertNotEqual(key, LoginThrottle().get_cache_key(other, DummyView()))

    def test_login_throttle_key_without_username(self):
        request = Request(APIRequestFactory().post("/", {}, format="json"), parsers=[JSONParser()])
        key = LoginThrottle().get_cache_key(request, DummyView())
        self.assertTrue(key.endswith(":anonymous"), key)

    def test_feedback_throttle_key_uses_user_pk_or_ip(self):
        factory = APIRequestFactory()
        user = make_user()
        request = Request(factory.post("/"))
        request.user = user
        self.assertIn(str(user.pk), FeedbackThrottle().get_cache_key(request, DummyView()))
        from django.contrib.auth.models import AnonymousUser
        anon = Request(factory.post("/"))
        anon.user = AnonymousUser()
        self.assertIn("127.0.0.1", FeedbackThrottle().get_cache_key(anon, DummyView()))


# ---------------------------------------------------------------------------
# Rasm siqish va Celery heartbeat
# ---------------------------------------------------------------------------
import shutil  # noqa: E402
import tempfile  # noqa: E402

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import override_settings  # noqa: E402
from PIL import Image  # noqa: E402

from common.images import MAX_SIDE, shrink_image  # noqa: E402
from common.tasks import HEARTBEAT_KEY, heartbeat_task  # noqa: E402


def big_image(fmt="PNG", size=(2400, 1200), name="big.png"):
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


class ImageShrinkTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)

    def test_shrink_image_limits_longest_side_and_keeps_format(self):
        upload = big_image()
        shrunk = shrink_image(upload, "big.png")
        image = Image.open(io.BytesIO(shrunk.read()))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(max(image.size), MAX_SIDE)
        self.assertEqual(image.size, (1600, 800))

    def test_small_image_is_not_upscaled(self):
        upload = big_image(size=(300, 200), fmt="JPEG", name="s.jpg")
        image = Image.open(io.BytesIO(shrink_image(upload, "s.jpg").read()))
        self.assertEqual(image.size, (300, 200))
        self.assertEqual(image.format, "JPEG")

    def test_unknown_extension_or_broken_file_is_skipped(self):
        self.assertIsNone(shrink_image(io.BytesIO(b"abc"), "x.gif"))
        self.assertIsNone(shrink_image(io.BytesIO(b"not an image"), "x.png"))

    def test_uploaded_avatar_is_shrunk_on_save(self):
        user = make_user()
        user.avatar = big_image(name="avatar.png")
        user.save()
        user.refresh_from_db()
        with user.avatar.open("rb") as stored:
            image = Image.open(stored)
            image.load()
        self.assertEqual(max(image.size), MAX_SIDE)
        self.assertLess(user.avatar.size, big_image().size)

    def test_saving_again_does_not_touch_committed_file(self):
        user = make_user()
        user.avatar = big_image(name="avatar.png")
        user.save()
        size_before = user.avatar.size
        user.bio = "salom"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.avatar.size, size_before)


class CeleryHeartbeatTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_health_reports_unknown_without_heartbeat(self):
        response = APIClient().get("/api/health/")
        self.assertEqual(response.data["checks"]["celery"], "unknown")

    def test_health_reports_ok_after_heartbeat(self):
        heartbeat_task()
        self.assertTrue(cache.get(HEARTBEAT_KEY))
        response = APIClient().get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["checks"]["celery"], "ok")
