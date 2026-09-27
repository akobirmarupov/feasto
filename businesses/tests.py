import datetime
import io
import itertools
import shutil
import tempfile
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from decimal import Decimal
from PIL import Image
from rest_framework.test import APIClient

from account.models import User
from businesses.models import Business, BusinessApplication, BusinessPhoto, Hall, Room, VenuePricing
from businesses.services import approve_application, reject_application, submit_application
from catalog.models import RestaurantMenuItem
from common.cache import get_business_version, invalidate_business_cache
from notifications.models import Notification
from reservations.models import Availability, Reservation
from reviews.services import recalculate_ranks
from subscriptions.models import PaymentLog, Subscription, SubscriptionPlan

PASSWORD = "StrongPass123!"
_seq = itertools.count(1)


# ----------------------------------------------------------------------------
# Yordamchilar
# ----------------------------------------------------------------------------
def make_user(username=None, *, phone="+998901234567", **extra):
    username = username or f"user{next(_seq)}"
    return User.objects.create_user(
        username=username, password=PASSWORD,
        full_name=extra.pop("full_name", username.title()),
        phone_number=phone, **extra,
    )


def make_admin(username=None):
    username = username or f"admin{next(_seq)}"
    return User.objects.create_user(
        username=username, password=PASSWORD, full_name="Admin",
        is_staff=True, is_superuser=True,
    )


def make_business(business_type="restaurant", name="Shoxona", *, owner=None, admin=None, approve=True, plan=None):
    """Ariza yuboradi va (kerak bo'lsa) tasdiqlaydi. (owner, business, application) qaytaradi."""
    owner = owner or make_user()
    application, business, _ = submit_application(
        applicant=owner, business_type=business_type, business_name=name, plan=plan,
    )
    if approve:
        approve_application(application=application, approved_by=admin or make_admin())
    owner.refresh_from_db()
    business.refresh_from_db()
    application.refresh_from_db()
    return owner, business, application


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def png_file(name="a.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def make_room(business, name="VIP 1", capacity=6, tier="premium", **extra):
    return Room.objects.create(business=business, name=name, room_type="vip", capacity=capacity, deposit_tier=tier, **extra)


def make_hall(business, name="Katta zal", people=300, **extra):
    return Hall.objects.create(business=business, name=name, people=people, **extra)


def make_reservation(business, customer, *, room=None, hall=None, status="pending", guests=2):
    return Reservation.objects.create(
        user=customer, business=business, room=room, hall=hall,
        guests_count=guests, status=status,
    )


class MediaTestCase(TestCase):
    """Fayl yuklaydigan testlar uchun vaqtinchalik MEDIA_ROOT."""

    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)


# ----------------------------------------------------------------------------
# Ariza oqimi
# ----------------------------------------------------------------------------
class ApplicationFlowTests(TestCase):
    url = "/api/business-applications/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.user = make_user("ali")
        self.client = client_for(self.user)

    def test_anonymous_gets_401(self):
        response = client_for().post(self.url, {"business_type": "restaurant", "business_name": "Shoxona"}, format="json")
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data["error"]["code"], "unauthenticated")

    def test_user_without_phone_gets_403_phone_required(self):
        nophone = make_user("nophone", phone=None)
        response = client_for(nophone).post(self.url, {"business_type": "restaurant", "business_name": "Shoxona"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "phone_required")
        self.assertIn("aloqa raqamingizni", response.data["error"]["message"])
        self.assertFalse(BusinessApplication.objects.exists())

    def test_short_name_400(self):
        response = self.client.post(self.url, {"business_type": "restaurant", "business_name": " Sh "}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertIn("business_name", response.data["error"]["details"])

    def test_invalid_business_type_400(self):
        response = self.client.post(self.url, {"business_type": "cafe", "business_name": "Shoxona"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("business_type", response.data["error"]["details"])

    def test_trial_application_created_201(self):
        response = self.client.post(self.url, {"business_type": "restaurant", "business_name": "  Shoxona  "}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        data = response.data
        self.assertEqual(data["application"]["status"], "pending_payment")
        self.assertEqual(data["application"]["business_name"], "Shoxona")
        self.assertTrue(data["is_trial"])
        self.assertEqual(data["trial_days"], 7)
        self.assertIn("BEPUL", data["message"])
        self.assertTrue(data["admin_telegram"].startswith("@"))

        business = Business.objects.get(pk=data["business_id"])
        self.assertFalse(business.is_visible)
        self.assertEqual(business.owner, self.user)
        self.assertEqual(data["application"]["business_id"], str(business.id))
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "user")  # tasdiqlanmaguncha rol o'zgarmaydi
        self.assertFalse(hasattr(business, "subscription"))

        # bildirishnoma: arizachi va staff
        self.assertTrue(Notification.objects.filter(user=self.user, kind="application").exists())
        self.assertTrue(Notification.objects.filter(user=self.admin, kind="application").exists())

    def test_second_application_while_pending_400(self):
        self.client.post(self.url, {"business_type": "restaurant", "business_name": "Shoxona"}, format="json")
        response = self.client.post(self.url, {"business_type": "venue", "business_name": "Boshqa"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "application_pending")
        self.assertEqual(BusinessApplication.objects.filter(applicant=self.user).count(), 1)

    def test_plan_type_mismatch_400(self):
        plan = SubscriptionPlan.objects.get(business_type="restaurant", duration_months=1)
        response = self.client.post(
            self.url, {"business_type": "venue", "business_name": "Navro'z", "plan": str(plan.id)}, format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("mos emas", response.data["error"]["message"])
        self.assertFalse(BusinessApplication.objects.exists())

    def test_paid_plan_application_is_not_trial(self):
        plan = SubscriptionPlan.objects.get(business_type="venue", duration_months=3)
        response = self.client.post(
            self.url, {"business_type": "venue", "business_name": "Navro'z", "plan": str(plan.id)}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["is_trial"])
        self.assertIsNone(response.data["trial_days"])
        self.assertEqual(response.data["application"]["plan_label"], "3 oy")
        self.assertIn("3 oy", response.data["message"])

    def test_trial_used_400(self):
        self.user.has_used_trial = True
        self.user.save(update_fields=["has_used_trial"])
        response = self.client.post(self.url, {"business_type": "restaurant", "business_name": "Shoxona"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "trial_used")

    def test_owner_with_approved_business_gets_business_limit_400(self):
        make_business(owner=self.user, admin=self.admin)
        response = self.client.post(self.url, {"business_type": "venue", "business_name": "Ikkinchi"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "business_limit")

    def test_my_applications_list(self):
        self.assertEqual(client_for().get(self.url + "my/").status_code, 401)
        created = self.client.post(self.url, {"business_type": "restaurant", "business_name": "Shoxona"}, format="json").data
        other = make_user("boshqa")
        submit_application(applicant=other, business_type="venue", business_name="Boshqaniki")

        response = self.client.get(self.url + "my/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], created["application"]["id"])
        self.assertEqual(response.data[0]["business_id"], created["business_id"])
        self.assertEqual(response.data[0]["applicant_username"], "ali")

    def test_admin_application_list_and_filters(self):
        submit_application(applicant=self.user, business_type="restaurant", business_name="Shoxona")
        _, _, approved = make_business("venue", "Navro'z", admin=self.admin)

        self.assertEqual(self.client.get("/api/admin/applications/").status_code, 403)

        admin_client = client_for(self.admin)
        response = admin_client.get("/api/admin/applications/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)

        response = admin_client.get("/api/admin/applications/", {"status": "approved"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(approved.id))

        response = admin_client.get("/api/admin/applications/", {"search": "shox"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["business_name"], "Shoxona")

        response = admin_client.get("/api/admin/applications/", {"business_type": "venue"})
        self.assertEqual(response.data["count"], 1)


# ----------------------------------------------------------------------------
# Tasdiqlash / rad etish
# ----------------------------------------------------------------------------
class ApplicationApprovalTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.admin_client = client_for(self.admin)
        self.user = make_user("ali")
        self.application, self.business, _ = submit_application(
            applicant=self.user, business_type="restaurant", business_name="Shoxona",
        )

    def approve_url(self, application=None):
        return f"/api/admin/applications/{(application or self.application).id}/approve/"

    def reject_url(self, application=None):
        return f"/api/admin/applications/{(application or self.application).id}/reject/"

    def test_approve_requires_admin(self):
        self.assertEqual(client_for().post(self.approve_url()).status_code, 401)
        self.assertEqual(client_for(self.user).post(self.approve_url()).status_code, 403)
        self.assertEqual(client_for(make_user("kim")).post(self.approve_url()).status_code, 403)

    def test_approve_unknown_404(self):
        response = self.admin_client.post("/api/admin/applications/00000000-0000-0000-0000-000000000000/approve/")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")

    def test_approve_trial_application(self):
        response = self.admin_client.post(self.approve_url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(response.data["approved_by"], self.admin.id)
        self.assertIsNotNone(response.data["approved_at"])

        self.user.refresh_from_db()
        self.business.refresh_from_db()
        self.assertEqual(self.user.role, "business")
        self.assertTrue(self.user.has_used_trial)
        self.assertTrue(self.business.is_visible)
        subscription = Subscription.objects.get(business=self.business)
        self.assertEqual(subscription.status, "trial")
        self.assertGreater(subscription.trial_ends_at, datetime.datetime.now(datetime.timezone.utc))

        # ommaviy ko'rinadi va rank hisoblangan
        self.assertEqual(client_for().get(f"/api/businesses/{self.business.id}/").status_code, 200)
        self.assertEqual(self.business.rank, 1)
        self.assertTrue(Notification.objects.filter(user=self.user, title__icontains="tasdiqlandi").exists())

    def test_approve_paid_plan_activates_subscription(self):
        plan = SubscriptionPlan.objects.get(business_type="venue", duration_months=3)
        user = make_user("vali")
        application, business, _ = submit_application(
            applicant=user, business_type="venue", business_name="Navro'z", plan=plan,
        )
        response = self.admin_client.post(self.approve_url(application))
        self.assertEqual(response.status_code, 200, response.data)

        subscription = Subscription.objects.get(business=business)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan, plan)
        self.assertIsNotNone(subscription.subscription_ends_at)
        self.assertEqual(PaymentLog.objects.filter(subscription=subscription).count(), 1)
        self.assertEqual(PaymentLog.objects.get(subscription=subscription).amount, plan.price)
        user.refresh_from_db()
        self.assertEqual(user.role, "business")
        self.assertFalse(user.has_used_trial)  # pullik — sinov sarflanmadi

    def test_approve_twice_400(self):
        self.assertEqual(self.admin_client.post(self.approve_url()).status_code, 200)
        response = self.admin_client.post(self.approve_url())
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("allaqachon tasdiqlangan", response.data["error"]["message"])

    def test_approve_when_trial_used_400(self):
        self.user.has_used_trial = True
        self.user.save(update_fields=["has_used_trial"])
        response = self.admin_client.post(self.approve_url())
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "trial_used")
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, "pending_payment")
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "user")

    def test_reject_pending_application_and_reapply(self):
        response = self.admin_client.post(self.reject_url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "rejected")

        self.user.refresh_from_db()
        self.business.refresh_from_db()
        self.assertEqual(self.user.role, "user")
        self.assertFalse(self.business.is_visible)
        self.assertEqual(client_for().get(f"/api/businesses/{self.business.id}/").status_code, 404)
        self.assertTrue(Notification.objects.filter(user=self.user, title__icontains="rad etildi").exists())

        # rad etilgan egasi owner paneliga kira olmaydi — xabar "rad etilgan"
        response = client_for(self.user).get("/api/owner/rooms/")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("rad etilgan", response.data["error"]["message"])

        # qayta ariza — o'sha biznes qayta ishlatiladi
        response = client_for(self.user).post(
            "/api/business-applications/", {"business_type": "venue", "business_name": "Navro'z"}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["business_id"], str(self.business.id))
        self.business.refresh_from_db()
        self.assertEqual(self.business.name, "Navro'z")
        self.assertEqual(self.business.business_type, "venue")
        self.assertEqual(str(self.business.application_id), response.data["application"]["id"])
        self.assertEqual(BusinessApplication.objects.filter(applicant=self.user).count(), 2)

    def test_reject_after_approve_resets_role(self):
        self.admin_client.post(self.approve_url())
        response = self.admin_client.post(self.reject_url())
        self.assertEqual(response.status_code, 200, response.data)

        self.user.refresh_from_db()
        self.business.refresh_from_db()
        self.assertEqual(self.user.role, "user")
        self.assertFalse(self.business.is_visible)
        self.assertEqual(self.business.rank, 0)

        # sinov sarflangan — bepul qayta ariza mumkin emas, pullik reja bilan mumkin
        client = client_for(self.user)
        response = client.post("/api/business-applications/", {"business_type": "restaurant", "business_name": "Shoxona 2"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "trial_used")

        plan = SubscriptionPlan.objects.get(business_type="restaurant", duration_months=1)
        response = client.post(
            "/api/business-applications/",
            {"business_type": "restaurant", "business_name": "Shoxona 2", "plan": str(plan.id)}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["business_id"], str(self.business.id))

    def test_reject_twice_400(self):
        self.assertEqual(self.admin_client.post(self.reject_url()).status_code, 200)
        response = self.admin_client.post(self.reject_url())
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("allaqachon rad etilgan", response.data["error"]["message"])

    def test_reject_service_keeps_role_for_staff_applicant(self):
        # staff arizachi roli pasaytirilmaydi
        staff = make_admin("staffapp")
        application, _, _ = submit_application(applicant=staff, business_type="restaurant", business_name="Staffniki")
        reject_application(application=application, rejected_by=self.admin)
        staff.refresh_from_db()
        self.assertEqual(staff.role, "user")
        self.assertTrue(staff.is_staff)


# ----------------------------------------------------------------------------
# IsBusinessRole xabarlari
# ----------------------------------------------------------------------------
class BusinessRolePermissionTests(TestCase):
    owner_urls = ("/api/owner/business/", "/api/owner/overview/", "/api/owner/rooms/", "/api/owner/photos/")

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)

    def test_anonymous_401(self):
        for url in self.owner_urls:
            response = client_for().get(url)
            self.assertEqual(response.status_code, 401, (url, response.data))

    def test_plain_user_403(self):
        client = client_for(make_user())
        for url in self.owner_urls:
            response = client.get(url)
            self.assertEqual(response.status_code, 403, (url, response.data))
            self.assertEqual(response.data["error"]["code"], "permission_denied")
            self.assertIn("faqat biznes egalari", response.data["error"]["message"])

    def test_unapproved_owner_403_with_pending_message(self):
        owner, _, _ = make_business(approve=False)
        for url in self.owner_urls:
            response = client_for(owner).get(url)
            self.assertEqual(response.status_code, 403, (url, response.data))
            self.assertIn("tasdiqlanmagan", response.data["error"]["message"])
            self.assertIn("7 kunlik", response.data["error"]["message"])

    def test_unapproved_paid_plan_message_mentions_plan(self):
        plan = SubscriptionPlan.objects.get(business_type="restaurant", duration_months=3)
        owner, _, _ = make_business(approve=False, plan=plan)
        response = client_for(owner).get("/api/owner/rooms/")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("3 oy", response.data["error"]["message"])

    def test_staff_cannot_use_owner_panel(self):
        response = client_for(make_admin()).get("/api/owner/business/")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("boshqaruv paneliga", response.data["error"]["message"])

    def test_approved_owner_allowed(self):
        owner, _, _ = make_business()
        for url in self.owner_urls:
            response = client_for(owner).get(url)
            self.assertEqual(response.status_code, 200, (url, response.data))

    def test_business_role_without_business_404(self):
        user = make_user(role="business")
        response = client_for(user).get("/api/owner/business/")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertIn("biznes profili yo'q", response.data["error"]["message"])


# ----------------------------------------------------------------------------
# Egasi: biznes sozlamalari va overview
# ----------------------------------------------------------------------------
class OwnerBusinessTests(TestCase):
    url = "/api/owner/business/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.owner, self.business, _ = make_business()
        self.client = client_for(self.owner)

    def test_get_returns_own_business(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(self.business.id))
        self.assertEqual(response.data["business_type"], "restaurant")
        self.assertTrue(response.data["is_visible"])
        # koordinata yo'q — havolalar nom bo'yicha qidiruvga tushadi, "custom" yo'q
        self.assertNotIn("custom", response.data["map_links"])
        self.assertIn("query=Shoxona", response.data["map_links"]["google"])

    def test_patch_map_link_fills_coordinates(self):
        response = self.client.patch(self.url, {
            "district": "Yunusobod", "address": "Amir Temur 1",
            "map_link": "https://maps.google.com/?q=41.3111,69.2797",
            "telegram_username": "shoxona", "phone_number": "+998712000000",
            "open_time": "10:00", "close_time": "23:00", "cuisine": "milliy",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertAlmostEqual(response.data["latitude"], 41.3111, places=3)
        self.assertAlmostEqual(response.data["longitude"], 69.2797, places=3)
        self.assertEqual(response.data["district"], "Yunusobod")
        self.assertEqual(response.data["cuisine"], "milliy")
        self.assertIn("custom", response.data["map_links"])
        self.assertIn("google", response.data["map_links"])
        self.assertIn("41.311100,69.279700", response.data["map_links"]["google"])

        self.business.refresh_from_db()
        self.assertAlmostEqual(self.business.latitude, 41.3111, places=3)

    def test_patch_yandex_link_lng_first(self):
        response = self.client.patch(self.url, {"map_link": "https://yandex.uz/maps/?ll=69.2797%2C41.3111&z=17"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertAlmostEqual(response.data["latitude"], 41.3111, places=3)
        self.assertAlmostEqual(response.data["longitude"], 69.2797, places=3)

    def test_same_open_and_close_time_400(self):
        response = self.client.patch(self.url, {"open_time": "10:00", "close_time": "10:00"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("close_time", response.data["error"]["details"])

    def test_read_only_fields_ignored(self):
        response = self.client.patch(self.url, {
            "is_visible": False, "business_type": "venue", "rating_avg": 5, "name": "Yangi nom",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Yangi nom")
        self.assertTrue(response.data["is_visible"])
        self.assertEqual(response.data["business_type"], "restaurant")
        self.assertEqual(response.data["rating_avg"], 0)

    def test_invalid_phone_400(self):
        response = self.client.patch(self.url, {"phone_number": "12345"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("phone_number", response.data["error"]["details"])

    def test_patch_invalidates_public_cache(self):
        anon = client_for()
        self.assertEqual(anon.get(f"/api/businesses/{self.business.id}/").data["name"], "Shoxona")
        self.client.patch(self.url, {"name": "Shoxona Plus"}, format="json")
        self.assertEqual(anon.get(f"/api/businesses/{self.business.id}/").data["name"], "Shoxona Plus")

    def test_expired_subscription_blocks_patch_but_allows_get(self):
        Subscription.objects.filter(business=self.business).update(status="expired")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.patch(self.url, {"name": "X"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("tugagan", response.data["error"]["message"])

    def test_overview_stats_and_recent(self):
        room = make_room(self.business)
        customer = make_user("mijoz")
        make_reservation(self.business, customer, room=room, status="pending")
        make_reservation(self.business, customer, room=room, status="confirmed")
        make_reservation(self.business, customer, room=room, status="completed")
        make_reservation(self.business, customer, room=room, status="cancelled")
        make_reservation(self.business, customer, room=room, status="confirmed")
        # boshqa biznesning broni hisobga olinmaydi
        _, other, _ = make_business(name="Boshqa")
        make_reservation(other, customer, room=make_room(other), status="pending")

        response = self.client.get("/api/owner/overview/")
        self.assertEqual(response.status_code, 200, response.data)
        stats = response.data["stats"]
        self.assertEqual(stats["total_reservations"], 5)
        self.assertEqual(stats["pending_reservations"], 1)
        self.assertEqual(stats["confirmed_reservations"], 2)
        self.assertEqual(stats["completed_reservations"], 1)
        self.assertEqual(stats["cancelled_reservations"], 1)
        self.assertEqual(response.data["business"]["id"], str(self.business.id))
        self.assertEqual(response.data["business"]["type"], "restaurant")
        self.assertEqual(response.data["subscription"]["status"], "trial")
        self.assertIsNotNone(response.data["subscription"]["trial_ends_at"])
        self.assertEqual(len(response.data["recent_reservations"]), 5)


# ----------------------------------------------------------------------------
# Ommaviy ro'yxat
# ----------------------------------------------------------------------------
class PublicBusinessListTests(TestCase):
    url = "/api/businesses/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        _, self.restaurant, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        make_room(self.restaurant, "VIP", capacity=6)
        make_room(self.restaurant, "Zal", capacity=20, tier="pro")
        _, self.venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        make_hall(self.venue, people=300)
        _, self.hidden, _ = make_business("restaurant", "Yashirin", approve=False)
        Business.objects.filter(pk=self.restaurant.pk).update(
            district="Yunusobod", address="Amir Temur 1", latitude=41.31, longitude=69.28,
        )
        Business.objects.filter(pk=self.venue.pk).update(district="Chilonzor", latitude=41.0, longitude=70.0)
        self.anon = client_for()

    def ids(self, response):
        return [row["id"] for row in response.data["results"]]

    def test_only_visible_listed_with_counts(self):
        response = self.anon.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertNotIn(str(self.hidden.id), self.ids(response))
        by_id = {row["id"]: row for row in response.data["results"]}
        restaurant = by_id[str(self.restaurant.id)]
        venue = by_id[str(self.venue.id)]
        self.assertEqual((restaurant["rooms_count"], restaurant["halls_count"]), (2, 0))
        self.assertEqual((restaurant["min_capacity"], restaurant["max_capacity"]), (6, 20))
        self.assertEqual((venue["rooms_count"], venue["halls_count"]), (0, 1))
        self.assertEqual((venue["min_capacity"], venue["max_capacity"]), (300, 300))
        self.assertEqual(restaurant["business_type_display"], "Restoran")

    def test_pagination_shape(self):
        response = self.anon.get(self.url, {"page_size": 1})
        self.assertEqual(response.status_code, 200, response.data)
        for key in ("count", "total_pages", "current_page", "next", "previous", "results"):
            self.assertIn(key, response.data)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertEqual(response.data["current_page"], 1)
        self.assertIsNotNone(response.data["next"])
        self.assertEqual(len(response.data["results"]), 1)

    def test_location_locked_for_anonymous_but_not_for_authenticated(self):
        row = next(r for r in self.anon.get(self.url).data["results"] if r["id"] == str(self.restaurant.id))
        self.assertTrue(row["location_locked"])
        self.assertIsNone(row["address"])
        self.assertIsNone(row["latitude"])
        self.assertEqual(row["map_links"], {})
        self.assertEqual(row["district"], "Yunusobod")  # tuman ochiq

        row = next(r for r in client_for(make_user()).get(self.url).data["results"] if r["id"] == str(self.restaurant.id))
        self.assertFalse(row["location_locked"])
        self.assertEqual(row["address"], "Amir Temur 1")
        self.assertAlmostEqual(row["latitude"], 41.31)
        self.assertIn("google", row["map_links"])

    def test_search_type_district_filters(self):
        self.assertEqual(self.ids(self.anon.get(self.url, {"search": "shox"})), [str(self.restaurant.id)])
        self.assertEqual(self.ids(self.anon.get(self.url, {"search": "chilon"})), [str(self.venue.id)])
        self.assertEqual(self.ids(self.anon.get(self.url, {"search": "amir temur"})), [str(self.restaurant.id)])
        self.assertEqual(self.anon.get(self.url, {"search": "   "}).data["count"], 2)
        self.assertEqual(self.ids(self.anon.get(self.url, {"type": "venue"})), [str(self.venue.id)])
        self.assertEqual(self.ids(self.anon.get(self.url, {"district": "yunusobod"})), [str(self.restaurant.id)])
        self.assertEqual(self.anon.get(self.url, {"type": "restaurant", "district": "Chilonzor"}).data["count"], 0)

    def test_guests_filter(self):
        self.assertEqual(self.anon.get(self.url, {"guests": 10}).data["count"], 2)
        self.assertEqual(self.ids(self.anon.get(self.url, {"guests": 50})), [str(self.venue.id)])
        self.assertEqual(self.anon.get(self.url, {"guests": 500}).data["count"], 0)

    def test_min_rating_filter(self):
        Business.objects.filter(pk=self.venue.pk).update(rating_avg=4.5)
        self.assertEqual(self.ids(self.anon.get(self.url, {"min_rating": 4})), [str(self.venue.id)])
        self.assertEqual(self.anon.get(self.url, {"min_rating": 4.6}).data["count"], 0)

    def test_geo_filter(self):
        response = self.anon.get(self.url, {"lat": 41.3, "lng": 69.27, "radius_km": 5})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.ids(response), [str(self.restaurant.id)])
        self.assertLess(response.data["results"][0]["distance_km"], 5)

        # radius kengaytirilsa ikkalasi ham, yaqini birinchi
        response = self.anon.get(self.url, {"lat": 41.3, "lng": 69.27, "radius_km": 100})
        self.assertEqual(self.ids(response), [str(self.restaurant.id), str(self.venue.id)])
        distances = [r["distance_km"] for r in response.data["results"]]
        self.assertEqual(distances, sorted(distances))

        # radius yuborilmasa 5 km standart
        self.assertEqual(self.anon.get(self.url, {"lat": 41.3, "lng": 69.27}).data["count"], 1)

    def test_geo_invalid_400(self):
        for params in ({"lat": "abc", "lng": 1}, {"lat": 95, "lng": 1}, {"lat": 41, "lng": 200}, {"lat": 41, "lng": 69, "radius_km": "x"}):
            response = self.anon.get(self.url, params)
            self.assertEqual(response.status_code, 400, (params, response.data))
            self.assertEqual(response.data["error"]["code"], "bad_request")

    def test_date_filter(self):
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        room = self.restaurant.rooms.first()
        slot = Availability.objects.create(
            business=self.restaurant, room=room, date=tomorrow,
            start_time=datetime.time(10, 0), end_time=datetime.time(23, 0),
        )
        self.assertEqual(self.ids(self.anon.get(self.url, {"date": str(tomorrow)})), [str(self.restaurant.id)])
        self.assertEqual(self.anon.get(self.url, {"date": str(tomorrow + datetime.timedelta(days=1))}).data["count"], 0)

        slot.is_booked = True
        slot.save(update_fields=["is_booked"])
        cache.clear()
        self.assertEqual(self.anon.get(self.url, {"date": str(tomorrow)}).data["count"], 0)

    def test_rank_ordering(self):
        _, second, _ = make_business("restaurant", "Ikkinchi", admin=self.admin)
        _, third, _ = make_business("restaurant", "Uchinchi", admin=self.admin)
        Business.objects.filter(pk=self.restaurant.pk).update(rating_points=10)
        Business.objects.filter(pk=second.pk).update(rating_points=5)
        Business.objects.filter(pk=third.pk).update(rating_points=99)
        recalculate_ranks("restaurant")
        Business.objects.filter(pk=third.pk).update(rank=0)  # hali hisoblanmagan → oxirida
        cache.clear()

        response = self.anon.get(self.url, {"type": "restaurant"})
        self.assertEqual(self.ids(response), [str(self.restaurant.id), str(second.id), str(third.id)])
        self.assertEqual([r["rank"] for r in response.data["results"]], [2, 3, 0])

    def test_cache_is_separate_for_anonymous_and_authenticated(self):
        anon_row = self.anon.get(self.url, {"search": "shox"}).data["results"][0]
        self.assertTrue(anon_row["location_locked"])
        auth_row = client_for(make_user()).get(self.url, {"search": "shox"}).data["results"][0]
        self.assertFalse(auth_row["location_locked"])
        self.assertEqual(auth_row["address"], "Amir Temur 1")
        # keshlangan anon javobi o'zgarmagan
        self.assertTrue(self.anon.get(self.url, {"search": "shox"}).data["results"][0]["location_locked"])

    def test_list_is_cached_until_invalidated(self):
        self.assertEqual(self.anon.get(self.url).data["count"], 2)
        Business.objects.filter(pk=self.hidden.pk).update(is_visible=True)  # signal yo'q → kesh eskiradi
        self.assertEqual(self.anon.get(self.url).data["count"], 2)
        invalidate_business_cache()
        self.assertEqual(self.anon.get(self.url).data["count"], 3)


# ----------------------------------------------------------------------------
# Ommaviy detal
# ----------------------------------------------------------------------------
class PublicBusinessDetailTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.restaurant, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        Business.objects.filter(pk=self.restaurant.pk).update(
            telegram_username="shoxona", phone_number="+998712000000", address="Amir Temur 1",
        )
        self.anon = client_for()

    def url(self, business):
        return f"/api/businesses/{business.id}/"

    def test_hidden_business_404(self):
        _, hidden, _ = make_business(name="Yashirin", approve=False)
        response = self.anon.get(self.url(hidden))
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(self.anon.get("/api/businesses/00000000-0000-0000-0000-000000000000/").status_code, 404)

    def test_contacts_locked_for_anonymous(self):
        data = self.anon.get(self.url(self.restaurant)).data
        self.assertTrue(data["contacts_locked"])
        self.assertTrue(data["location_locked"])
        self.assertIsNone(data["telegram_username"])
        self.assertIsNone(data["phone_number"])
        self.assertIsNone(data["address"])
        self.assertEqual(data["owner_username"], self.owner.username)

        data = client_for(make_user()).get(self.url(self.restaurant)).data
        self.assertFalse(data["contacts_locked"])
        self.assertEqual(data["telegram_username"], "shoxona")
        self.assertEqual(data["phone_number"], "+998712000000")
        self.assertEqual(data["address"], "Amir Temur 1")

    def test_restaurant_detail_has_rooms_and_available_menu_only(self):
        make_room(self.restaurant, "VIP", 6)
        make_room(self.restaurant, "Zal", 20, tier="pro")
        RestaurantMenuItem.objects.create(business=self.restaurant, name="Osh", price="45000")
        RestaurantMenuItem.objects.create(business=self.restaurant, name="Somsa", price="12000", is_available=False)

        data = self.anon.get(self.url(self.restaurant)).data
        self.assertEqual(len(data["rooms"]), 2)
        self.assertEqual(data["rooms"][0]["deposit_amount"], "99000.00")
        self.assertEqual(data["halls"], [])
        self.assertEqual([m["name"] for m in data["menu"]], ["Osh"])
        self.assertEqual(data["dish_pricing"], [])
        self.assertEqual(data["pricing_mode"], "unset")
        self.assertEqual(data["rank"], 1)

    def test_venue_detail_pricing_modes(self):
        _, venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        url = self.url(venue)
        self.assertEqual(self.anon.get(url).data["pricing_mode"], "unset")

        VenuePricing.objects.create(business=venue, dish_count=1, price_per_person="80000")
        VenuePricing.objects.create(business=venue, dish_count=2, price_per_person="120000")
        data = self.anon.get(url).data
        self.assertEqual(data["pricing_mode"], "per_person")
        self.assertEqual(len(data["dish_pricing"]), 2)
        self.assertEqual(data["dish_pricing"][0]["price_per_person"], "80000.00")

        hall = make_hall(venue, all_price="15000000")
        data = self.anon.get(url).data
        self.assertEqual(data["pricing_mode"], "combined")
        self.assertEqual(len(data["halls"]), 1)
        self.assertEqual(data["halls"][0]["deposit_amount"], "599000.00")
        self.assertEqual(data["rooms"], [])

        VenuePricing.objects.filter(business=venue).delete()
        self.assertEqual(self.anon.get(url).data["pricing_mode"], "fixed")

        hall.all_price = None
        hall.save()
        self.assertEqual(self.anon.get(url).data["pricing_mode"], "unset")

    def test_detail_cache_separate_for_audiences(self):
        self.assertTrue(self.anon.get(self.url(self.restaurant)).data["contacts_locked"])
        self.assertFalse(client_for(make_user()).get(self.url(self.restaurant)).data["contacts_locked"])
        self.assertTrue(self.anon.get(self.url(self.restaurant)).data["contacts_locked"])


# ----------------------------------------------------------------------------
# Xonalar
# ----------------------------------------------------------------------------
class OwnerRoomTests(TestCase):
    url = "/api/owner/rooms/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.restaurant, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        self.client = client_for(self.owner)
        self.payload = {"name": "VIP 1", "room_type": "vip", "capacity": 6, "deposit_tier": "premium"}

    def test_permissions(self):
        self.assertEqual(client_for().get(self.url).status_code, 401)
        self.assertEqual(client_for(make_user()).get(self.url).status_code, 403)
        venue_owner, _, _ = make_business("venue", "Navro'z", admin=self.admin)
        response = client_for(venue_owner).post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("restoran egalari", response.data["error"]["message"])
        self.assertEqual(client_for(venue_owner).get(self.url).status_code, 403)

    def test_create_requires_deposit_tier(self):
        payload = {**self.payload}
        payload.pop("deposit_tier")
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("deposit_tier", response.data["error"]["details"])
        self.assertFalse(Room.objects.exists())

    def test_create_validation_400(self):
        response = self.client.post(self.url, {**self.payload, "capacity": 0}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("capacity", response.data["error"]["details"])
        response = self.client.post(self.url, {**self.payload, "room_type": "garden"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(self.url, {**self.payload, "deposit_tier": "gold"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(self.url, {**self.payload, "deposit_price": "-1"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_create_deposit_amount_by_tier_and_custom_price(self):
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["deposit_amount"], "99000.00")
        self.assertEqual(response.data["business"], self.restaurant.id)
        self.assertEqual(response.data["room_type_display"], "VIP xona")

        response = self.client.post(self.url, {**self.payload, "name": "Pro", "deposit_tier": "pro"}, format="json")
        self.assertEqual(response.data["deposit_amount"], "49000.00")

        response = self.client.post(self.url, {**self.payload, "name": "Custom", "deposit_tier": "pro", "deposit_price": "30000"}, format="json")
        self.assertEqual(response.data["deposit_amount"], "30000.00")
        self.assertEqual(Room.objects.filter(business=self.restaurant).count(), 3)

    def test_list_and_filters(self):
        make_room(self.restaurant, "VIP", 6)
        make_room(self.restaurant, "Zal", 20, tier="pro")
        _, other, _ = make_business(name="Boshqa", admin=self.admin)
        make_room(other, "Begona", 4)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual([r["name"] for r in response.data["results"]], ["VIP", "Zal"])  # capacity bo'yicha
        self.assertEqual(self.client.get(self.url, {"min_capacity": 10}).data["count"], 1)
        self.assertEqual(self.client.get(self.url, {"max_capacity": 10}).data["count"], 1)
        self.assertEqual(self.client.get(self.url, {"deposit_tier": "pro"}).data["results"][0]["name"], "Zal")

    def test_get_patch_delete_own_room(self):
        room = make_room(self.restaurant)
        url = f"{self.url}{room.id}/"
        self.assertEqual(self.client.get(url).status_code, 200)

        response = self.client.patch(url, {"capacity": 8, "deposit_tier": "pro"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["capacity"], 8)
        self.assertEqual(response.data["deposit_amount"], "49000.00")

        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())

    def test_other_owners_room_404(self):
        _, other, _ = make_business(name="Boshqa", admin=self.admin)
        room = make_room(other)
        url = f"{self.url}{room.id}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"capacity": 9}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        room.refresh_from_db()
        self.assertEqual(room.capacity, 6)

    def test_delete_with_active_reservation_400(self):
        room = make_room(self.restaurant)
        reservation = make_reservation(self.restaurant, make_user("mijoz"), room=room, status="confirmed")
        response = self.client.delete(f"{self.url}{room.id}/")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("faol bronlar", response.data["error"]["message"])

        Reservation.objects.filter(pk=reservation.pk).update(status="completed")
        self.assertEqual(self.client.delete(f"{self.url}{room.id}/").status_code, 204)

    def test_expired_subscription_blocks_writes(self):
        room = make_room(self.restaurant)
        Subscription.objects.filter(business=self.restaurant).update(status="expired")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload, format="json").status_code, 403)
        self.assertEqual(self.client.patch(f"{self.url}{room.id}/", {"capacity": 9}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"{self.url}{room.id}/").status_code, 403)

    def test_public_rooms_list(self):
        make_room(self.restaurant, "VIP", 6)
        make_room(self.restaurant, "Zal", 20, tier="pro")
        anon = client_for()
        response = anon.get(f"/api/businesses/{self.restaurant.id}/rooms/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(anon.get(f"/api/businesses/{self.restaurant.id}/rooms/", {"min_capacity": 10}).data["count"], 1)
        self.assertEqual(anon.get(f"/api/businesses/{self.restaurant.id}/rooms/", {"room_type": "outdoor"}).data["count"], 0)

        _, hidden, _ = make_business(name="Yashirin", approve=False)
        make_room(hidden)
        self.assertEqual(anon.get(f"/api/businesses/{hidden.id}/rooms/").status_code, 404)

    def test_model_clean_rejects_room_for_venue(self):
        from django.core.exceptions import ValidationError

        _, venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        with self.assertRaises(ValidationError):
            Room(business=venue, name="X", room_type="vip", capacity=4, deposit_tier="pro").full_clean()
        with self.assertRaises(ValidationError):
            Room(business=self.restaurant, name="X", room_type="vip", capacity=4).full_clean()


# ----------------------------------------------------------------------------
# Zallar
# ----------------------------------------------------------------------------
class OwnerHallTests(TestCase):
    url = "/api/owner/halls/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        self.client = client_for(self.owner)

    def test_restaurant_owner_403(self):
        restaurant_owner, _, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        response = client_for(restaurant_owner).post(self.url, {"name": "Zal", "people": 100}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("to'yxona egalari", response.data["error"]["message"])
        self.assertEqual(client_for().get(self.url).status_code, 401)
        self.assertEqual(client_for(make_user()).get(self.url).status_code, 403)

    def test_create_hall_deposit_default_and_custom(self):
        response = self.client.post(self.url, {"name": "Katta zal", "people": 300, "all_price": "15000000"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["deposit_amount"], "599000.00")
        self.assertEqual(response.data["all_price"], "15000000.00")

        response = self.client.post(self.url, {"name": "Kichik", "people": 50, "deposit_price": "100000"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["deposit_amount"], "100000.00")
        self.assertIsNone(response.data["all_price"])

    def test_zero_price_400(self):
        response = self.client.post(self.url, {"name": "Z", "people": 10, "all_price": "0"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("all_price", response.data["error"]["details"])
        response = self.client.post(self.url, {"name": "Z", "people": 0}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("people", response.data["error"]["details"])

    def test_list_patch_delete(self):
        big = make_hall(self.venue, "Katta", 500)
        small = make_hall(self.venue, "Kichik", 100)
        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["id"], str(big.id))  # -people
        self.assertEqual(self.client.get(self.url, {"min_people": 200}).data["count"], 1)
        self.assertEqual(self.client.get(self.url, {"max_people": 200}).data["count"], 1)

        response = self.client.patch(f"{self.url}{small.id}/", {"people": 150, "package": "Standart"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["people"], 150)
        self.assertEqual(response.data["package"], "Standart")
        self.assertEqual(self.client.patch(f"{self.url}{small.id}/", {"all_price": "0"}, format="json").status_code, 400)

        self.assertEqual(self.client.delete(f"{self.url}{small.id}/").status_code, 204)
        self.assertFalse(Hall.objects.filter(pk=small.pk).exists())

    def test_other_owners_hall_404(self):
        _, other, _ = make_business("venue", "Boshqa", admin=self.admin)
        hall = make_hall(other)
        self.assertEqual(self.client.get(f"{self.url}{hall.id}/").status_code, 404)
        self.assertEqual(self.client.delete(f"{self.url}{hall.id}/").status_code, 404)

    def test_delete_with_active_reservation_400(self):
        hall = make_hall(self.venue)
        make_reservation(self.venue, make_user("mijoz"), hall=hall, status="pending", guests=100)
        response = self.client.delete(f"{self.url}{hall.id}/")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("faol bronlar", response.data["error"]["message"])

    def test_public_halls_list(self):
        make_hall(self.venue, "Katta", 500)
        make_hall(self.venue, "Kichik", 100)
        anon = client_for()
        response = anon.get(f"/api/businesses/{self.venue.id}/halls/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(anon.get(f"/api/businesses/{self.venue.id}/halls/", {"min_people": 300}).data["count"], 1)
        _, hidden, _ = make_business("venue", "Yashirin", approve=False)
        self.assertEqual(anon.get(f"/api/businesses/{hidden.id}/halls/").status_code, 404)

    def test_model_clean_rejects_hall_for_restaurant(self):
        from django.core.exceptions import ValidationError

        _, restaurant, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        with self.assertRaises(ValidationError):
            Hall(business=restaurant, name="X", people=10).full_clean()


# ----------------------------------------------------------------------------
# To'yxona narx paketlari
# ----------------------------------------------------------------------------
class VenuePricingTests(TestCase):
    url = "/api/owner/pricing/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        self.client = client_for(self.owner)

    def test_put_creates_updates_and_removes_packages(self):
        response = self.client.put(self.url, [
            {"dish_count": 1, "price_per_person": "80000"},
            {"dish_count": 2, "price_per_person": "120000"},
        ], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([(p["dish_count"], p["price_per_person"]) for p in response.data], [(1, "80000.00"), (2, "120000.00")])

        response = self.client.put(self.url, [
            {"dish_count": 2, "price_per_person": "130000"},
            {"dish_count": 3, "price_per_person": "150000"},
        ], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([(p["dish_count"], p["price_per_person"]) for p in response.data], [(2, "130000.00"), (3, "150000.00")])
        self.assertEqual(VenuePricing.objects.filter(business=self.venue).count(), 2)
        self.assertFalse(VenuePricing.objects.filter(business=self.venue, dish_count=1).exists())

        response = self.client.put(self.url, [], format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, [])
        self.assertEqual(self.client.get(self.url).data, [])

    def test_put_duplicate_400(self):
        response = self.client.put(self.url, [
            {"dish_count": 1, "price_per_person": "80000"},
            {"dish_count": 1, "price_per_person": "90000"},
        ], format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("bitta narx", response.data["error"]["message"])
        self.assertFalse(VenuePricing.objects.exists())

    def test_put_non_list_400(self):
        response = self.client.put(self.url, {"dish_count": 1, "price_per_person": "80000"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Ro'yxat", response.data["error"]["message"])

    def test_put_invalid_items_400(self):
        response = self.client.put(self.url, [{"dish_count": 4, "price_per_person": "80000"}], format="json")
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.put(self.url, [{"dish_count": 1, "price_per_person": "-5"}], format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_permissions(self):
        self.assertEqual(client_for().get(self.url).status_code, 401)
        restaurant_owner, _, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        response = client_for(restaurant_owner).get(self.url)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("to'yxona egalari", response.data["error"]["message"])

        Subscription.objects.filter(business=self.venue).update(status="expired")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.put(self.url, [{"dish_count": 1, "price_per_person": "1"}], format="json").status_code, 403)

    def test_public_pricing(self):
        VenuePricing.objects.create(business=self.venue, dish_count=1, price_per_person="80000")
        anon = client_for()
        response = anon.get(f"/api/businesses/{self.venue.id}/pricing/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["price_per_person"], "80000.00")

        _, restaurant, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        self.assertEqual(anon.get(f"/api/businesses/{restaurant.id}/pricing/").status_code, 404)
        _, hidden, _ = make_business("venue", "Yashirin", approve=False)
        self.assertEqual(anon.get(f"/api/businesses/{hidden.id}/pricing/").status_code, 404)


# ----------------------------------------------------------------------------
# Rasmlar
# ----------------------------------------------------------------------------
class BusinessPhotoTests(MediaTestCase):
    url = "/api/owner/photos/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.business, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        self.client = client_for(self.owner)

    def test_owner_upload_patch_delete(self):
        response = self.client.post(self.url, {"image": png_file(), "order": 2}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["order"], 2)
        self.assertTrue(response.data["image"].startswith("http"))
        photo_id = response.data["id"]

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)

        response = self.client.patch(f"{self.url}{photo_id}/", {"order": 0}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["order"], 0)

        response = self.client.delete(f"{self.url}{photo_id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(BusinessPhoto.objects.filter(pk=photo_id).exists())

    def test_upload_validation(self):
        response = self.client.post(self.url, {"order": 1}, format="multipart")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("image", response.data["error"]["details"])
        bad = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
        response = self.client.post(self.url, {"image": bad}, format="multipart")
        self.assertEqual(response.status_code, 400, response.data)

    def test_limit_reached_400(self):
        with patch("businesses.routes.business_photo_api.MAX_PHOTOS_PER_BUSINESS", 1):
            self.assertEqual(self.client.post(self.url, {"image": png_file()}, format="multipart").status_code, 201)
            response = self.client.post(self.url, {"image": png_file("b.png")}, format="multipart")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("eng ko'pi bilan 1 ta", response.data["error"]["message"])

    def test_permissions(self):
        self.assertEqual(client_for().post(self.url, {"image": png_file()}, format="multipart").status_code, 401)
        self.assertEqual(client_for(make_user()).post(self.url, {"image": png_file()}, format="multipart").status_code, 403)
        unapproved, _, _ = make_business(name="Kutmoqda", approve=False)
        self.assertEqual(client_for(unapproved).post(self.url, {"image": png_file()}, format="multipart").status_code, 403)
        Subscription.objects.filter(business=self.business).update(status="expired")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, {"image": png_file()}, format="multipart").status_code, 403)

    def test_other_owners_photo_404(self):
        _, other, _ = make_business(name="Boshqa", admin=self.admin)
        photo = BusinessPhoto.objects.create(business=other, image=png_file())
        self.assertEqual(self.client.patch(f"{self.url}{photo.id}/", {"order": 5}, format="multipart").status_code, 404)
        self.assertEqual(self.client.delete(f"{self.url}{photo.id}/").status_code, 404)
        self.assertTrue(BusinessPhoto.objects.filter(pk=photo.pk).exists())

    def test_public_list_and_detail_gallery(self):
        BusinessPhoto.objects.create(business=self.business, image=png_file(), order=2)
        BusinessPhoto.objects.create(business=self.business, image=png_file("b.png"), order=1)
        anon = client_for()
        response = anon.get(f"/api/businesses/{self.business.id}/photos/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([p["order"] for p in response.data], [1, 2])
        self.assertEqual(len(anon.get(f"/api/businesses/{self.business.id}/").data["gallery"]), 2)

        _, hidden, _ = make_business(name="Yashirin", approve=False)
        BusinessPhoto.objects.create(business=hidden, image=png_file())
        self.assertEqual(anon.get(f"/api/businesses/{hidden.id}/photos/").status_code, 404)

    def test_showcase_only_visible_businesses(self):
        self.business.cover_photo = png_file("cover.png")
        self.business.save()
        BusinessPhoto.objects.create(business=self.business, image=png_file())
        _, hidden, _ = make_business(name="Yashirin", approve=False)
        BusinessPhoto.objects.create(business=hidden, image=png_file())

        anon = client_for()
        response = anon.get("/api/showcase/photos/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 2)  # cover + galereya
        self.assertEqual({row["business"] for row in response.data}, {str(self.business.id)})
        self.assertEqual(response.data[0]["business_name"], "Shoxona")
        self.assertIn("/media/", response.data[0]["image"])

        self.assertEqual(len(anon.get("/api/showcase/photos/", {"limit": 1}).data), 1)
        response = anon.get("/api/showcase/photos/", {"limit": "abc"})
        self.assertEqual(response.status_code, 400, response.data)


# ----------------------------------------------------------------------------
# Admin: biznes boshqaruvi
# ----------------------------------------------------------------------------
class AdminBusinessTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.client = client_for(self.admin)
        self.owner, self.restaurant, self.application = make_business("restaurant", "Shoxona", admin=self.admin)
        _, self.venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        _, self.pending, _ = make_business("restaurant", "Kutmoqda", approve=False)

    def test_admin_endpoints_require_staff(self):
        urls = ("/api/admin/overview/", "/api/admin/businesses/", f"/api/admin/businesses/{self.restaurant.id}/")
        for url in urls:
            self.assertEqual(client_for().get(url).status_code, 401, url)
            self.assertEqual(client_for(self.owner).get(url).status_code, 403, url)
        self.assertEqual(client_for(self.owner).patch(f"/api/admin/businesses/{self.restaurant.id}/toggle-block/").status_code, 403)
        self.assertEqual(client_for(self.owner).delete(f"/api/admin/businesses/{self.restaurant.id}/").status_code, 403)

    def test_overview(self):
        make_reservation(self.restaurant, make_user("mijoz"), room=make_room(self.restaurant), status="pending")
        response = self.client.get("/api/admin/overview/")
        self.assertEqual(response.status_code, 200, response.data)
        stats = response.data["stats"]
        self.assertEqual(stats["businesses_count"], 3)
        self.assertEqual(stats["restaurants_count"], 2)
        self.assertEqual(stats["venues_count"], 1)
        self.assertEqual(stats["visible_businesses"], 2)
        self.assertEqual(stats["pending_applications"], 1)
        self.assertEqual(stats["reservations_count"], 1)
        self.assertEqual(stats["pending_reservations"], 1)
        self.assertEqual(stats["users_count"], User.objects.filter(is_active=True).count())
        self.assertEqual(response.data["subscriptions"]["trial"], 2)
        self.assertEqual(len(response.data["recent_applications"]), 3)

    def test_list_and_filters(self):
        response = self.client.get("/api/admin/businesses/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)  # yashirinlar ham
        row = next(r for r in response.data["results"] if r["id"] == str(self.restaurant.id))
        self.assertEqual(row["owner_name"], self.owner.full_name)
        self.assertEqual(row["subscription_status"], "trial")

        self.assertEqual(self.client.get("/api/admin/businesses/", {"type": "venue"}).data["count"], 1)
        self.assertEqual(self.client.get("/api/admin/businesses/", {"is_visible": "false"}).data["count"], 1)
        self.assertEqual(self.client.get("/api/admin/businesses/", {"search": "navro"}).data["count"], 1)

    def test_create_with_approve_starts_trial(self):
        new_owner = make_user("yangi", phone=None)
        response = self.client.post("/api/admin/businesses/create/", {
            "owner": new_owner.id, "business_type": "restaurant", "name": "Admin Resto",
            "district": "Yakkasaroy", "approve": True,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["subscription_status"], "trial")
        self.assertTrue(response.data["is_visible"])
        self.assertEqual(response.data["district"], "Yakkasaroy")
        self.assertEqual(response.data["owner"], new_owner.id)
        new_owner.refresh_from_db()
        self.assertEqual(new_owner.role, "business")
        self.assertTrue(new_owner.has_used_trial)
        self.assertEqual(BusinessApplication.objects.get(applicant=new_owner).status, "approved")

    def test_create_without_approve_stays_hidden(self):
        new_owner = make_user("yangi")
        response = self.client.post("/api/admin/businesses/create/", {
            "owner": new_owner.id, "business_type": "venue", "name": "Kutadi",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["subscription_status"])
        self.assertFalse(response.data["is_visible"])
        new_owner.refresh_from_db()
        self.assertEqual(new_owner.role, "user")
        self.assertEqual(BusinessApplication.objects.get(applicant=new_owner).status, "pending_payment")

    def test_create_validation(self):
        response = self.client.post("/api/admin/businesses/create/", {
            "owner": self.owner.id, "business_type": "restaurant", "name": "Dup",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("owner", response.data["error"]["details"])

        response = self.client.post("/api/admin/businesses/create/", {
            "owner": 999999, "business_type": "restaurant", "name": "Yo'q",
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)

        used = make_user("ishlatgan", has_used_trial=True)
        response = self.client.post("/api/admin/businesses/create/", {
            "owner": used.id, "business_type": "restaurant", "name": "Sinovsiz", "approve": True,
        }, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "trial_used")
        self.assertFalse(Business.objects.filter(owner=used).exists())  # tranzaksiya qaytarildi

    def test_detail_get_and_patch_allowed_fields_only(self):
        url = f"/api/admin/businesses/{self.restaurant.id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Shoxona")

        response = self.client.patch(url, {
            "name": "Shoxona 2", "district": "Yunusobod", "rating_avg": 5, "owner": 1,
            "map_link": "https://maps.google.com/?q=41.3111,69.2797",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Shoxona 2")
        self.assertEqual(response.data["district"], "Yunusobod")
        self.assertEqual(response.data["rating_avg"], 0)
        self.assertEqual(response.data["owner"], self.owner.id)
        self.assertIn("custom", response.data["map_links"])
        self.restaurant.refresh_from_db()
        self.assertAlmostEqual(self.restaurant.latitude, 41.3111, places=3)

        response = self.client.patch(url, {"rating_avg": 5, "description": "x"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Tahrirlash mumkin", response.data["error"]["message"])

        self.assertEqual(self.client.get("/api/admin/businesses/00000000-0000-0000-0000-000000000000/").status_code, 404)

    def test_patch_is_visible_hides_from_public(self):
        response = self.client.patch(f"/api/admin/businesses/{self.restaurant.id}/", {"is_visible": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client_for().get(f"/api/businesses/{self.restaurant.id}/").status_code, 404)

    def test_delete_with_active_reservation_400(self):
        make_reservation(self.restaurant, make_user("mijoz"), room=make_room(self.restaurant), status="pending")
        response = self.client.delete(f"/api/admin/businesses/{self.restaurant.id}/")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("1 ta faol bron", response.data["error"]["message"])
        self.assertTrue(Business.objects.filter(pk=self.restaurant.pk).exists())

    def test_delete_success_resets_owner_and_removes_application(self):
        make_reservation(self.restaurant, make_user("mijoz"), room=make_room(self.restaurant), status="completed")
        response = self.client.delete(f"/api/admin/businesses/{self.restaurant.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Business.objects.filter(pk=self.restaurant.pk).exists())
        self.assertFalse(BusinessApplication.objects.filter(pk=self.application.pk).exists())
        self.assertFalse(Subscription.objects.filter(business_id=self.restaurant.pk).exists())
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, "user")
        self.assertEqual(self.client.delete(f"/api/admin/businesses/{self.restaurant.id}/").status_code, 404)

        # egasi qayta ariza bera oladi (sinov ishlatilgan → pullik reja bilan)
        plan = SubscriptionPlan.objects.get(business_type="restaurant", duration_months=1)
        response = client_for(self.owner).post(
            "/api/business-applications/",
            {"business_type": "restaurant", "business_name": "Qayta", "plan": str(plan.id)}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_toggle_block(self):
        url = f"/api/admin/businesses/{self.restaurant.id}/toggle-block/"
        anon = client_for()
        self.assertEqual(anon.get("/api/businesses/").data["count"], 2)

        response = self.client.patch(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["is_visible"])
        self.restaurant.refresh_from_db()
        self.assertFalse(self.restaurant.is_visible)
        self.assertEqual(self.restaurant.rank, 0)
        self.assertEqual(anon.get("/api/businesses/").data["count"], 1)
        self.assertEqual(anon.get(f"/api/businesses/{self.restaurant.id}/").status_code, 404)

        response = self.client.patch(url)
        self.assertTrue(response.data["is_visible"])
        self.restaurant.refresh_from_db()
        self.assertEqual(self.restaurant.rank, 1)
        self.assertEqual(anon.get("/api/businesses/").data["count"], 2)

        self.assertEqual(self.client.patch("/api/admin/businesses/00000000-0000-0000-0000-000000000000/toggle-block/").status_code, 404)


# ----------------------------------------------------------------------------
# Signallar
# ----------------------------------------------------------------------------
class BusinessSignalTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.first, _ = make_business("restaurant", "Birinchi", admin=self.admin)
        _, self.second, _ = make_business("restaurant", "Ikkinchi", admin=self.admin)
        Business.objects.filter(pk=self.first.pk).update(rating_points=10)
        Business.objects.filter(pk=self.second.pk).update(rating_points=5)
        recalculate_ranks("restaurant")
        self.first.refresh_from_db()
        self.second.refresh_from_db()

    def test_ranks_recalculated_when_visibility_changes(self):
        self.assertEqual((self.first.rank, self.second.rank), (1, 2))

        self.first.is_visible = False
        self.first.save(update_fields=["is_visible"])
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.rank, self.second.rank), (0, 1))

        self.first.is_visible = True
        self.first.save(update_fields=["is_visible"])
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.rank, self.second.rank), (1, 2))

    def test_ranks_untouched_when_other_fields_saved(self):
        Business.objects.filter(pk=self.second.pk).update(rank=0)
        self.first.name = "Yangi"
        self.first.save(update_fields=["name"])
        self.second.refresh_from_db()
        self.assertEqual(self.second.rank, 0)

        self.first.save()  # update_fields yo'q → qayta hisoblanadi
        self.second.refresh_from_db()
        self.assertEqual(self.second.rank, 2)

    def test_ranks_are_per_business_type(self):
        _, venue, _ = make_business("venue", "Navro'z", admin=self.admin)
        venue.refresh_from_db()
        self.assertEqual(venue.rank, 1)
        self.first.refresh_from_db()
        self.assertEqual(self.first.rank, 1)

    def test_owner_role_reset_when_business_deleted(self):
        self.assertEqual(self.owner.role, "business")
        self.first.delete()
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, "user")
        self.second.refresh_from_db()
        self.assertEqual(self.second.rank, 1)

    def test_cache_version_bumps_on_related_changes(self):
        version = get_business_version()
        room = make_room(self.first)
        self.assertGreater(get_business_version(), version)

        version = get_business_version()
        room.delete()
        self.assertGreater(get_business_version(), version)

        version = get_business_version()
        VenuePricing.objects.create(business=make_business("venue", "N", admin=self.admin)[1], dish_count=1, price_per_person="1")
        self.assertGreater(get_business_version(), version)

    def test_map_link_fills_coordinates_on_model_save(self):
        self.first.map_link = "https://www.google.com/maps/place/x/@41.2995,69.2401,17z"
        self.first.save(update_fields=["map_link"])
        self.first.refresh_from_db()
        self.assertAlmostEqual(self.first.latitude, 41.2995, places=3)
        self.assertAlmostEqual(self.first.longitude, 69.2401, places=3)
        self.assertEqual(self.first.map_links["custom"], self.first.map_link)


# ----------------------------------------------------------------------------
# Sevimlilar
# ----------------------------------------------------------------------------
class FavoriteTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        _, self.business, _ = make_business(admin=self.admin)
        _, self.hidden, _ = make_business(name="Yashirin", admin=self.admin)
        self.hidden.is_visible = False
        self.hidden.save(update_fields=["is_visible"])
        self.user = make_user()
        self.client = client_for(self.user)

    def test_anonymous_401(self):
        self.assertEqual(client_for().get("/api/favorites/").status_code, 401)
        self.assertEqual(client_for().post(f"/api/favorites/{self.business.id}/").status_code, 401)

    def test_add_list_remove(self):
        response = self.client.post(f"/api/favorites/{self.business.id}/")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_favorite"])

        response = self.client.post(f"/api/favorites/{self.business.id}/")
        self.assertEqual(response.status_code, 200, "Ikkinchi marta qo'shish xato emas")

        response = self.client.get("/api/favorites/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["ids"], [str(self.business.id)])
        self.assertEqual(response.data["results"][0]["name"], "Shoxona")
        self.assertEqual(response.data["results"][0]["address"], self.business.address, "Kirgan foydalanuvchi manzilni ko'radi")

        response = self.client.delete(f"/api/favorites/{self.business.id}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.client.get("/api/favorites/").data["count"], 0)

    def test_remove_missing_404(self):
        response = self.client.delete(f"/api/favorites/{self.business.id}/")
        self.assertEqual(response.status_code, 404)

    def test_hidden_business_cannot_be_added_and_disappears_from_list(self):
        response = self.client.post(f"/api/favorites/{self.hidden.id}/")
        self.assertEqual(response.status_code, 404)

        self.client.post(f"/api/favorites/{self.business.id}/")
        self.business.is_visible = False
        self.business.save(update_fields=["is_visible"])
        response = self.client.get("/api/favorites/")
        self.assertEqual(response.data["count"], 0, "Yashiringan biznes ro'yxatda chiqmaydi")
        self.assertEqual(response.data["ids"], [str(self.business.id)], "ID esa saqlanadi")

    def test_favorites_are_private(self):
        self.client.post(f"/api/favorites/{self.business.id}/")
        other = client_for(make_user())
        self.assertEqual(other.get("/api/favorites/").data["count"], 0)
        self.assertEqual(other.delete(f"/api/favorites/{self.business.id}/").status_code, 404)


# ----------------------------------------------------------------------------
# Geo qidiruv (SQL) va ko'p so'zli qidiruv
# ----------------------------------------------------------------------------
class GeoAndSearchTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.near = self._business("Shoxona Yunusobod", "Yunusobod", 41.3300, 69.2850)
        self.mid = self._business("Bella Italia", "Mirobod", 41.2980, 69.2740)
        self.far = self._business("Oq saroy Sergeli", "Sergeli", 41.2250, 69.2200)

    def _business(self, name, district, lat, lng):
        _, business, _ = make_business(name=name, admin=self.admin)
        business.district, business.address = district, f"{district} ko'chasi 1"
        business.latitude, business.longitude = lat, lng
        business.save()
        return business

    def test_radius_filters_and_orders_by_distance(self):
        response = client_for().get("/api/businesses/", {"lat": 41.33, "lng": 69.285, "radius_km": 5})
        self.assertEqual(response.status_code, 200, response.data)
        names = [b["name"] for b in response.data["results"]]
        self.assertEqual(names, ["Shoxona Yunusobod", "Bella Italia"])
        distances = [b["distance_km"] for b in response.data["results"]]
        self.assertEqual(distances, sorted(distances))
        self.assertLess(distances[0], 0.1)
        self.assertGreater(distances[1], 3)

    def test_default_radius_is_5km_and_capped_at_100(self):
        response = client_for().get("/api/businesses/", {"lat": 41.33, "lng": 69.285})
        self.assertEqual(response.data["count"], 2)
        response = client_for().get("/api/businesses/", {"lat": 41.33, "lng": 69.285, "radius_km": 100000})
        self.assertEqual(response.data["count"], 3)

    def test_geo_combines_with_other_filters(self):
        response = client_for().get("/api/businesses/", {"lat": 41.33, "lng": 69.285, "radius_km": 50, "district": "Sergeli"})
        self.assertEqual([b["name"] for b in response.data["results"]], ["Oq saroy Sergeli"])

    def test_multi_word_search_requires_every_word(self):
        response = client_for().get("/api/businesses/", {"search": "shoxona yunusobod"})
        self.assertEqual(response.data["count"], 1)
        response = client_for().get("/api/businesses/", {"search": "shoxona sergeli"})
        self.assertEqual(response.data["count"], 0)
        response = client_for().get("/api/businesses/", {"search": "  saroy  "})
        self.assertEqual(response.data["count"], 1)


# ----------------------------------------------------------------------------
# Admin overview — davr statistikasi
# ----------------------------------------------------------------------------
class AdminOverviewPeriodTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.client = client_for(self.admin)
        self.owner, self.business, _ = make_business(admin=self.admin)
        self.customer = make_user()
        room = make_room(self.business)
        make_reservation(self.business, self.customer, room=room, status="completed")
        make_reservation(self.business, self.customer, room=room, status="cancelled")
        PaymentLog.objects.create(subscription=self.business.subscription, amount=Decimal("250000"), confirmed_by=self.admin)

    def test_period_block(self):
        response = self.client.get("/api/admin/overview/", {"days": 7})
        self.assertEqual(response.status_code, 200, response.data)
        period = response.data["period"]
        self.assertEqual(period["days"], 7)
        self.assertEqual(period["new_businesses"], 1)
        self.assertEqual(period["reservations"], 2)
        self.assertEqual(period["completed_reservations"], 1)
        self.assertEqual(period["cancelled_reservations"], 1)
        self.assertEqual(Decimal(period["revenue"]), Decimal("250000"))
        self.assertEqual(period["payments_count"], 1)
        self.assertEqual(sum(row["count"] for row in period["reservations_by_day"]), 2)
        self.assertEqual(len(period["payments_by_day"]), 1)

    def test_expiring_subscriptions_within_week(self):
        Subscription.objects.filter(business=self.business).update(
            trial_ends_at=timezone.now() + datetime.timedelta(days=3)
        )
        response = self.client.get("/api/admin/overview/")
        self.assertEqual([s["business_name"] for s in response.data["expiring_subscriptions"]], ["Shoxona"])

        Subscription.objects.filter(business=self.business).update(
            trial_ends_at=timezone.now() + datetime.timedelta(days=30)
        )
        response = self.client.get("/api/admin/overview/")
        self.assertEqual(response.data["expiring_subscriptions"], [])

    def test_days_validation(self):
        self.assertEqual(self.client.get("/api/admin/overview/", {"days": "abc"}).status_code, 400)
        self.assertEqual(self.client.get("/api/admin/overview/", {"days": 9999}).data["period"]["days"], 365)
        self.assertEqual(self.client.get("/api/admin/overview/", {"days": 0}).data["period"]["days"], 1)


# ----------------------------------------------------------------------------
# Mantiq tuzatishlari: koordinata, qayta ariza, admin create
# ----------------------------------------------------------------------------
class LogicFixTests(TestCase):
    LINK = "https://maps.google.com/?q=41.3111,69.2797"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()

    def test_hand_edited_coordinates_survive_save(self):
        _, business, _ = make_business(admin=self.admin)
        business.map_link = self.LINK
        business.save()
        business.refresh_from_db()
        self.assertAlmostEqual(business.latitude, 41.3111, places=3)

        business.latitude, business.longitude = 41.5, 69.5
        business.save()
        business.refresh_from_db()
        self.assertAlmostEqual(business.latitude, 41.5, places=3, msg="Havola o'zgarmagan — qo'lda kiritilgan koordinata qoladi")

        business.map_link = "https://maps.google.com/?q=41.2000,69.2000"
        business.save()
        business.refresh_from_db()
        self.assertAlmostEqual(business.latitude, 41.2, places=3, msg="Havola o'zgardi — koordinata yangilanadi")

    def test_owner_patch_keeps_hand_edited_coordinates(self):
        owner, business, _ = make_business(admin=self.admin)
        client = client_for(owner)
        response = client.patch("/api/owner/business/", {"map_link": self.LINK}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        response = client.patch("/api/owner/business/", {"latitude": 41.9, "longitude": 69.9}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        business.refresh_from_db()
        self.assertAlmostEqual(business.latitude, 41.9, places=3)

    def test_reapply_with_other_type_clears_old_data(self):
        owner, business, application = make_business(admin=self.admin, approve=False)
        make_room(business)
        RestaurantMenuItem.objects.create(business=business, name="Osh", price=Decimal("45000"))
        Availability.objects.create(
            business=business, room=business.rooms.first(), date=datetime.date.today(),
            start_time=datetime.time(10), end_time=datetime.time(22),
        )
        reject_application(application=application, rejected_by=self.admin)

        _, same_business, _ = submit_application(applicant=owner, business_type="venue", business_name="Endi to'yxona")
        self.assertEqual(same_business.pk, business.pk)
        self.assertEqual(same_business.business_type, "venue")
        self.assertEqual(same_business.rooms.count(), 0)
        self.assertEqual(same_business.restaurant_menu_items.count(), 0)
        self.assertEqual(same_business.availabilities.count(), 0)

    def test_reapply_same_type_keeps_data(self):
        owner, business, application = make_business(admin=self.admin, approve=False)
        make_room(business)
        reject_application(application=application, rejected_by=self.admin)
        submit_application(applicant=owner, business_type="restaurant", business_name="Shoxona 2")
        self.assertEqual(business.rooms.count(), 1)

    def test_admin_can_create_business_for_rejected_applicant(self):
        owner, business, application = make_business(admin=self.admin, approve=False)
        reject_application(application=application, rejected_by=self.admin)
        response = client_for(self.admin).post(
            "/api/admin/businesses/create/",
            {"owner": owner.id, "business_type": "restaurant", "name": "Qayta ochildi", "approve": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["name"], "Qayta ochildi")
        self.assertEqual(Business.objects.filter(owner=owner).count(), 1)
