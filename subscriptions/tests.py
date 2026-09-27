from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from businesses.models import Business, BusinessApplication
from businesses.services import (
    TrialNotAvailable,
    approve_application,
    reject_application,
    submit_application,
)
from notifications.models import Notification
from subscriptions.models import PaymentLog, Subscription, SubscriptionPlan, SubscriptionRequest
from subscriptions.services import (
    TrialAlreadyUsed,
    activate_subscription,
    check_expired_subscriptions,
    request_renewal,
    send_expiry_reminders,
    start_trial,
)
from subscriptions.tasks import check_expired_subscriptions_task, notify_expiring_subscriptions_task

User = get_user_model()

PASSWORD = "StrongPass123!"


def make_user(username, phone="+998901234567", **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, full_name=username.title(),
        phone_number=phone, **extra,
    )


def make_admin(username="boss"):
    return make_user(username, is_staff=True, is_superuser=True)


def make_business(owner, admin, business_type="restaurant", name="Shoxona", plan=None, approve=True):
    application, business, _ = submit_application(
        applicant=owner, business_type=business_type, business_name=name, plan=plan,
    )
    if approve:
        approve_application(application=application, approved_by=admin)
        owner.refresh_from_db()
        business.refresh_from_db()
    return business


def plan_for(business_type, months):
    return SubscriptionPlan.objects.get(business_type=business_type, duration_months=months)


def auth(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class SubscriptionTestCase(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.admin_client = auth(self.admin)


# ---------------------------------------------------------------------------
# Ommaviy tarif rejalari
# ---------------------------------------------------------------------------
class PublicPlanListTests(SubscriptionTestCase):
    def test_anonymous_sees_all_plans_sorted(self):
        response = APIClient().get("/api/subscription-plans/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 4)
        keys = [(p["business_type"], p["duration_months"]) for p in response.data]
        self.assertEqual(keys, [("restaurant", 1), ("restaurant", 3), ("venue", 1), ("venue", 3)])

    def test_filter_by_business_type(self):
        response = APIClient().get("/api/subscription-plans/", {"business_type": "venue"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({p["business_type"] for p in response.data}, {"venue"})
        self.assertEqual(len(response.data), 2)

    def test_savings_and_price_per_month(self):
        response = APIClient().get("/api/subscription-plans/", {"business_type": "restaurant"})
        monthly, quarterly = response.data
        self.assertIsNone(monthly["savings"])
        # 250000 * 3 - 600000 = 150000
        self.assertEqual(Decimal(quarterly["savings"]), Decimal("150000"))
        self.assertEqual(Decimal(quarterly["price_per_month"]), Decimal("200000.00"))
        self.assertEqual(quarterly["duration_label"], "3 oy")
        self.assertEqual(quarterly["business_type_display"], "Restoran")

    def test_savings_none_when_quarterly_not_cheaper(self):
        plan = plan_for("restaurant", 3)
        plan.price = Decimal("900000")
        plan.save(update_fields=["price"])
        response = APIClient().get("/api/subscription-plans/", {"business_type": "restaurant"})
        self.assertIsNone(response.data[1]["savings"])


# ---------------------------------------------------------------------------
# Admin: tarif rejalari CRUD
# ---------------------------------------------------------------------------
class AdminPlanTests(SubscriptionTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("oddiy")

    def test_admin_list_paginated(self):
        response = self.admin_client.get("/api/admin/subscription-plans/", {"business_type": "restaurant"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertIn("total_pages", response.data)
        self.assertIn("results", response.data)

    def test_create_plan_201(self):
        payload = {"business_type": "restaurant", "duration_months": 6, "price": "1000000", "trial_days": 10}
        response = self.admin_client.post("/api/admin/subscription-plans/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["duration_label"], "6 oy")
        self.assertTrue(SubscriptionPlan.objects.filter(business_type="restaurant", duration_months=6).exists())

    def test_create_duplicate_plan_400(self):
        payload = {"business_type": "restaurant", "duration_months": 1, "price": "1"}
        response = self.admin_client.post("/api/admin/subscription-plans/", payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(response.data["success"])
        self.assertEqual(response.data["error"]["code"], "bad_request")

    def test_create_plan_invalid_type_400(self):
        payload = {"business_type": "hotel", "duration_months": 1, "price": "1"}
        response = self.admin_client.post("/api/admin/subscription-plans/", payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("business_type", response.data["error"]["details"])

    def test_regular_user_forbidden(self):
        client = auth(self.user)
        self.assertEqual(client.get("/api/admin/subscription-plans/").status_code, 403)
        response = client.post(
            "/api/admin/subscription-plans/",
            {"business_type": "venue", "duration_months": 12, "price": "1"}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")

    def test_anonymous_401(self):
        response = APIClient().get("/api/admin/subscription-plans/")
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data["error"]["code"], "unauthenticated")

    def test_detail_and_patch(self):
        plan = plan_for("venue", 1)
        response = self.admin_client.get(f"/api/admin/subscription-plans/{plan.pk}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], str(plan.pk))

        response = self.admin_client.patch(
            f"/api/admin/subscription-plans/{plan.pk}/", {"price": "350000"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        plan.refresh_from_db()
        self.assertEqual(plan.price, Decimal("350000"))

    def test_detail_404(self):
        import uuid

        response = self.admin_client.get(f"/api/admin/subscription-plans/{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")

    def test_delete_unused_plan_204(self):
        plan = plan_for("venue", 3)
        response = self.admin_client.delete(f"/api/admin/subscription-plans/{plan.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(SubscriptionPlan.objects.filter(pk=plan.pk).exists())

    def test_delete_plan_in_use_400(self):
        owner = make_user("ega")
        make_business(owner, self.admin)  # trial → restaurant 1 oy rejasiga bog'lanadi
        plan = plan_for("restaurant", 1)
        response = self.admin_client.delete(f"/api/admin/subscription-plans/{plan.pk}/")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertTrue(SubscriptionPlan.objects.filter(pk=plan.pk).exists())

    def test_delete_plan_with_application_400(self):
        plan = plan_for("venue", 1)
        applicant = make_user("arizachi")
        submit_application(applicant=applicant, business_type="venue", business_name="Saroy", plan=plan)
        response = self.admin_client.delete(f"/api/admin/subscription-plans/{plan.pk}/")
        self.assertEqual(response.status_code, 400, response.data)


# ---------------------------------------------------------------------------
# Egasi: /owner/subscription/
# ---------------------------------------------------------------------------
class OwnerSubscriptionTests(SubscriptionTestCase):
    URL = "/api/owner/subscription/"

    def test_anonymous_401(self):
        self.assertEqual(APIClient().get(self.URL).status_code, 401)

    def test_user_without_business_403(self):
        response = auth(make_user("yolgiz")).get(self.URL)
        self.assertEqual(response.status_code, 403, response.data)

    def test_staff_cannot_use_owner_panel(self):
        response = self.admin_client.get(self.URL)
        self.assertEqual(response.status_code, 403, response.data)

    def test_pending_trial_application_awaiting_approval(self):
        applicant = make_user("arizachi")
        submit_application(applicant=applicant, business_type="restaurant", business_name="Yangi")
        applicant.refresh_from_db()
        self.assertEqual(applicant.role, "user")  # roli hali user, lekin arizasi bor — kiradi

        response = auth(applicant).get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertFalse(data["has_subscription"])
        self.assertEqual(data["status"], "awaiting_approval")
        self.assertFalse(data["can_reapply"])
        self.assertTrue(data["is_trial_application"])
        self.assertEqual(data["trial_days"], 7)
        self.assertIsNone(data["applied_plan"])
        self.assertIn("bepul sinov", data["detail"])
        self.assertEqual(len(data["plans"]), 4)
        self.assertTrue(data["admin_telegram"].startswith("@"))
        self.assertIsNone(data["pending_request"])

    def test_pending_paid_application_text(self):
        applicant = make_user("pullik")
        plan = plan_for("venue", 3)
        submit_application(applicant=applicant, business_type="venue", business_name="Saroy", plan=plan)

        response = auth(applicant).get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertFalse(data["has_subscription"])
        self.assertFalse(data["is_trial_application"])
        self.assertIsNone(data["trial_days"])
        self.assertEqual(data["applied_plan"]["id"], str(plan.pk))
        self.assertIn("3 oy", data["detail"])
        self.assertIn("To'lov tasdiqlangach", data["detail"])
        self.assertEqual(data["business_type"], "venue")

    def test_rejected_application_can_reapply(self):
        applicant = make_user("radetilgan")
        application, _, _ = submit_application(
            applicant=applicant, business_type="restaurant", business_name="Yangi",
        )
        reject_application(application=application, rejected_by=self.admin)

        response = auth(applicant).get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "rejected")
        self.assertTrue(response.data["can_reapply"])
        self.assertIn("rad etilgan", response.data["detail"])

    def test_trial_subscription_days_left(self):
        owner = make_user("ega")
        business = make_business(owner, self.admin)
        self.assertEqual(owner.role, "business")

        response = auth(owner).get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertTrue(data["has_subscription"])
        self.assertEqual(data["status"], "trial")
        self.assertEqual(str(data["business"]), str(business.pk))
        self.assertEqual(data["business_name"], "Shoxona")
        self.assertEqual(data["owner_phone"], "+998901234567")
        self.assertIn(data["days_left"], (6, 7))
        self.assertIsNone(data["subscription_ends_at"])
        self.assertEqual(data["payments"], [])
        self.assertEqual(data["plan_label"], "1 oy")
        self.assertNotIn("trial_used", data)

    def test_pending_request_is_included(self):
        owner = make_user("ega")
        business = make_business(owner, self.admin)
        renewal, _ = request_renewal(business=business, plan=plan_for("restaurant", 3))

        response = auth(owner).get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["pending_request"]["id"], str(renewal.pk))
        self.assertEqual(response.data["pending_request"]["status"], "pending_payment")

    def test_days_left_uses_subscription_end_when_active(self):
        owner = make_user("ega")
        business = make_business(owner, self.admin)
        activate_subscription(business=business, approved_by=self.admin)

        response = auth(owner).get(self.URL)
        self.assertEqual(response.data["status"], "active")
        self.assertIn(response.data["days_left"], (29, 30))
        self.assertEqual(len(response.data["payments"]), 1)


# ---------------------------------------------------------------------------
# Admin: obunalar ro'yxati / activate / expire
# ---------------------------------------------------------------------------
class AdminSubscriptionTests(SubscriptionTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("ega")
        self.business = make_business(self.owner, self.admin)
        self.subscription = self.business.subscription
        self.venue_owner = make_user("toyxona")
        self.venue = make_business(self.venue_owner, self.admin, business_type="venue", name="Saroy")

    def _url(self, suffix=""):
        return f"/api/admin/subscriptions/{self.subscription.pk}/{suffix}"

    def test_list_and_filters(self):
        response = self.admin_client.get("/api/admin/subscriptions/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)

        response = self.admin_client.get("/api/admin/subscriptions/", {"business_type": "venue"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["business_name"], "Saroy")

        response = self.admin_client.get("/api/admin/subscriptions/", {"search": "shox"})
        self.assertEqual(response.data["count"], 1)

        response = self.admin_client.get("/api/admin/subscriptions/", {"status": "active"})
        self.assertEqual(response.data["count"], 0)

        response = self.admin_client.get("/api/admin/subscriptions/", {"plan": str(plan_for("venue", 1).pk)})
        self.assertEqual(response.data["count"], 1)

    def test_detail(self):
        response = self.admin_client.get(self._url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["owner_name"], "Ega")
        self.assertEqual(response.data["status_display"], "Trial (bepul)")

    def test_regular_user_and_owner_forbidden_on_admin_endpoints(self):
        owner_client = auth(self.owner)
        self.assertEqual(owner_client.get("/api/admin/subscriptions/").status_code, 403)
        self.assertEqual(owner_client.get(self._url()).status_code, 403)
        self.assertEqual(owner_client.post(self._url("activate/"), {}, format="json").status_code, 403)
        self.assertEqual(owner_client.post(self._url("expire/")).status_code, 403)
        # o'z obunasini o'qish mumkin
        self.assertEqual(owner_client.get("/api/owner/subscription/").status_code, 200)

    def test_activate_from_trial(self):
        self.business.is_visible = False
        self.business.save(update_fields=["is_visible"])

        before = timezone.now()
        response = self.admin_client.post(self._url("activate/"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(len(response.data["payments"]), 1)

        self.subscription.refresh_from_db()
        self.business.refresh_from_db()
        self.assertEqual(self.subscription.status, "active")
        self.assertEqual(self.subscription.approved_by, self.admin)
        expected = before + timedelta(days=30)
        self.assertLess(abs((self.subscription.subscription_ends_at - expected).total_seconds()), 60)
        self.assertTrue(self.business.is_visible)

        payment = PaymentLog.objects.get(subscription=self.subscription)
        self.assertEqual(payment.amount, plan_for("restaurant", 1).price)
        self.assertEqual(payment.confirmed_by, self.admin)
        self.assertTrue(payment.note)

    def test_activate_extends_from_existing_end_date(self):
        # erta to'lagan egasi qolgan kunlarini yo'qotmaydi
        current_end = timezone.now() + timedelta(days=10)
        Subscription.objects.filter(pk=self.subscription.pk).update(
            status="active", subscription_ends_at=current_end,
        )
        response = self.admin_client.post(
            self._url("activate/"), {"amount": "250000", "note": "Naqd"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.subscription.refresh_from_db()
        expected = current_end + timedelta(days=30)
        self.assertLess(abs((self.subscription.subscription_ends_at - expected).total_seconds()), 5)
        payment = self.subscription.payments.get()
        self.assertEqual(payment.amount, Decimal("250000"))
        self.assertEqual(payment.note, "Naqd")

    def test_activate_expired_starts_from_now(self):
        Subscription.objects.filter(pk=self.subscription.pk).update(
            status="expired", subscription_ends_at=timezone.now() - timedelta(days=5),
        )
        Business.objects.filter(pk=self.business.pk).update(is_visible=False)

        before = timezone.now()
        response = self.admin_client.post(self._url("activate/"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.subscription.refresh_from_db()
        self.business.refresh_from_db()
        self.assertEqual(self.subscription.status, "active")
        self.assertLess(
            abs((self.subscription.subscription_ends_at - (before + timedelta(days=30))).total_seconds()), 60,
        )
        self.assertTrue(self.business.is_visible)

    def test_activate_negative_amount_400(self):
        response = self.admin_client.post(self._url("activate/"), {"amount": "-1"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("amount", response.data["error"]["details"])

    def test_activate_switches_plan_via_service(self):
        quarterly = plan_for("restaurant", 3)
        before = timezone.now()
        activate_subscription(business=self.business, approved_by=self.admin, plan=quarterly)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.plan, quarterly)
        self.assertLess(
            abs((self.subscription.subscription_ends_at - (before + timedelta(days=90))).total_seconds()), 60,
        )
        self.assertEqual(self.subscription.payments.get().amount, quarterly.price)

    def test_activate_404(self):
        import uuid

        response = self.admin_client.post(f"/api/admin/subscriptions/{uuid.uuid4()}/activate/", {}, format="json")
        self.assertEqual(response.status_code, 404, response.data)

    def test_expire_hides_business(self):
        self.assertTrue(self.business.is_visible)
        response = self.admin_client.post(self._url("expire/"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "expired")
        self.business.refresh_from_db()
        self.assertFalse(self.business.is_visible)

    def test_expire_twice_400(self):
        self.assertEqual(self.admin_client.post(self._url("expire/")).status_code, 200)
        response = self.admin_client.post(self._url("expire/"))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")

    def test_expired_then_activate_restores(self):
        self.admin_client.post(self._url("expire/"))
        response = self.admin_client.post(self._url("activate/"), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.business.refresh_from_db()
        self.assertTrue(self.business.is_visible)
        self.assertEqual(response.data["status"], "active")


# ---------------------------------------------------------------------------
# Egasi: uzaytirish arizasi
# ---------------------------------------------------------------------------
class OwnerRenewalRequestTests(SubscriptionTestCase):
    URL = "/api/owner/subscription/requests/"

    def setUp(self):
        super().setUp()
        self.staff2 = make_admin("staff2")
        self.owner = make_user("ega")
        self.business = make_business(self.owner, self.admin)
        self.client_owner = auth(self.owner)
        self.quarterly = plan_for("restaurant", 3)

    def test_create_request_201_and_notifies_staff(self):
        Notification.objects.all().delete()
        response = self.client_owner.post(
            self.URL, {"plan": str(self.quarterly.pk), "note": "Tez orada to'layman"}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "pending_payment")
        self.assertEqual(Decimal(response.data["price"]), self.quarterly.price)
        self.assertEqual(response.data["duration_months"], 3)
        self.assertEqual(response.data["note"], "Tez orada to'layman")
        self.assertEqual(response.data["owner_phone"], "+998901234567")

        staff_notes = Notification.objects.filter(kind=Notification.KIND_SUBSCRIPTION)
        self.assertEqual(staff_notes.count(), 2)
        self.assertEqual({n.user_id for n in staff_notes}, {self.admin.id, self.staff2.id})
        self.assertIn("Shoxona", staff_notes.first().body)

    def test_duplicate_returns_existing_200(self):
        first = self.client_owner.post(self.URL, {"plan": str(self.quarterly.pk)}, format="json")
        second = self.client_owner.post(
            self.URL, {"plan": str(plan_for("restaurant", 1).pk)}, format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(second.data["id"], first.data["id"])
        self.assertEqual(SubscriptionRequest.objects.filter(business=self.business).count(), 1)

    def test_plan_type_mismatch_400(self):
        response = self.client_owner.post(self.URL, {"plan": str(plan_for("venue", 1).pk)}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("plan", response.data["error"]["details"])

    def test_unknown_plan_400(self):
        import uuid

        response = self.client_owner.post(self.URL, {"plan": str(uuid.uuid4())}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("plan", response.data["error"]["details"])

    def test_missing_plan_400(self):
        response = self.client_owner.post(self.URL, {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_without_phone_403_phone_required(self):
        User.objects.filter(pk=self.owner.pk).update(phone_number=None)
        response = auth(User.objects.get(pk=self.owner.pk)).post(
            self.URL, {"plan": str(self.quarterly.pk)}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "phone_required")

    def test_list_only_own_requests(self):
        other_owner = make_user("boshqa")
        other_business = make_business(other_owner, self.admin, name="Boshqa")
        request_renewal(business=other_business, plan=plan_for("restaurant", 1))
        mine, _ = request_renewal(business=self.business, plan=self.quarterly)

        response = self.client_owner.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(mine.pk))

    def test_regular_user_403_and_anon_401(self):
        self.assertEqual(auth(make_user("oddiy")).get(self.URL).status_code, 403)
        self.assertEqual(self.admin_client.get(self.URL).status_code, 403)
        self.assertEqual(APIClient().get(self.URL).status_code, 401)


# ---------------------------------------------------------------------------
# Admin: arizalar ro'yxati / tasdiqlash / rad etish
# ---------------------------------------------------------------------------
class AdminRenewalTests(SubscriptionTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("ega")
        self.business = make_business(self.owner, self.admin)
        self.subscription = self.business.subscription
        self.quarterly = plan_for("restaurant", 3)
        self.renewal, _ = request_renewal(business=self.business, plan=self.quarterly, note="izoh")
        Notification.objects.all().delete()

    def _url(self, action):
        return f"/api/admin/subscription-requests/{self.renewal.pk}/{action}/"

    def test_list_and_filters(self):
        other = make_business(make_user("boshqa"), self.admin, name="Boshqa")
        other_request, _ = request_renewal(business=other, plan=plan_for("restaurant", 1))

        response = self.admin_client.get("/api/admin/subscription-requests/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)

        response = self.admin_client.get("/api/admin/subscription-requests/", {"business": str(other.pk)})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(other_request.pk))

        self.admin_client.post(self._url("reject"), {}, format="json")
        response = self.admin_client.get("/api/admin/subscription-requests/", {"status": "pending_payment"})
        self.assertEqual(response.data["count"], 1)
        response = self.admin_client.get("/api/admin/subscription-requests/", {"status": "rejected"})
        self.assertEqual(response.data["count"], 1)

    def test_approve_extends_90_days_switches_plan_logs_payment_notifies(self):
        before = timezone.now()
        response = self.admin_client.post(self._url("approve"), {"note": "Click orqali"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "approved")
        self.assertEqual(response.data["admin_note"], "Click orqali")
        self.assertEqual(response.data["reviewed_by"], self.admin.id)
        self.assertIsNotNone(response.data["reviewed_at"])

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "active")
        self.assertEqual(self.subscription.plan, self.quarterly)
        self.assertEqual(self.subscription.reminded_days, [])
        self.assertLess(
            abs((self.subscription.subscription_ends_at - (before + timedelta(days=90))).total_seconds()), 60,
        )

        payment = PaymentLog.objects.get(subscription=self.subscription)
        self.assertEqual(payment.amount, self.quarterly.price)
        self.assertEqual(payment.confirmed_by, self.admin)

        note = Notification.objects.get(user=self.owner)
        self.assertEqual(note.kind, Notification.KIND_SUBSCRIPTION)
        self.assertEqual(note.level, Notification.LEVEL_SUCCESS)
        self.assertIn("faollashtirildi", note.title)
        self.assertIn("3 oy", note.body)

    def test_approve_with_custom_amount(self):
        response = self.admin_client.post(self._url("approve"), {"amount": "550000"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(PaymentLog.objects.get(subscription=self.subscription).amount, Decimal("550000"))

    def test_approve_twice_400(self):
        self.assertEqual(self.admin_client.post(self._url("approve"), {}, format="json").status_code, 200)
        response = self.admin_client.post(self._url("approve"), {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertEqual(PaymentLog.objects.filter(subscription=self.subscription).count(), 1)

    def test_reject_notifies_owner(self):
        response = self.admin_client.post(self._url("reject"), {"note": "To'lov kelmadi"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(response.data["admin_note"], "To'lov kelmadi")

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "trial")
        self.assertFalse(PaymentLog.objects.filter(subscription=self.subscription).exists())

        note = Notification.objects.get(user=self.owner)
        self.assertEqual(note.level, Notification.LEVEL_WARNING)
        self.assertIn("rad etildi", note.title)
        self.assertEqual(note.body, "To'lov kelmadi")

    def test_reject_twice_400_and_approve_after_reject_400(self):
        self.assertEqual(self.admin_client.post(self._url("reject"), {}, format="json").status_code, 200)
        self.assertEqual(self.admin_client.post(self._url("reject"), {}, format="json").status_code, 400)
        self.assertEqual(self.admin_client.post(self._url("approve"), {}, format="json").status_code, 400)

    def test_owner_can_reapply_after_reject(self):
        self.admin_client.post(self._url("reject"), {}, format="json")
        renewal, created = request_renewal(business=self.business, plan=self.quarterly)
        self.assertTrue(created)
        self.assertNotEqual(renewal.pk, self.renewal.pk)

    def test_non_admin_forbidden_and_404(self):
        import uuid

        self.assertEqual(auth(self.owner).post(self._url("approve"), {}, format="json").status_code, 403)
        self.assertEqual(auth(self.owner).get("/api/admin/subscription-requests/").status_code, 403)
        response = self.admin_client.post(
            f"/api/admin/subscription-requests/{uuid.uuid4()}/approve/", {}, format="json",
        )
        self.assertEqual(response.status_code, 404, response.data)


# ---------------------------------------------------------------------------
# To'lovlar jurnali
# ---------------------------------------------------------------------------
class PaymentLogTests(SubscriptionTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("ega")
        self.business = make_business(self.owner, self.admin)
        self.other_owner = make_user("boshqa")
        self.other_business = make_business(self.other_owner, self.admin, business_type="venue", name="Saroy")
        activate_subscription(business=self.business, approved_by=self.admin, amount=Decimal("100000"))
        activate_subscription(business=self.other_business, approved_by=self.admin, amount=Decimal("200000"))

    def test_owner_sees_only_own_payments(self):
        response = auth(self.owner).get("/api/owner/payments/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        self.assertEqual(Decimal(row["amount"]), Decimal("100000"))
        self.assertEqual(row["business_name"], "Shoxona")
        self.assertEqual(row["confirmed_by_name"], "Boss")

    def test_owner_payments_forbidden_for_staff_and_regular(self):
        self.assertEqual(self.admin_client.get("/api/owner/payments/").status_code, 403)
        self.assertEqual(auth(make_user("oddiy")).get("/api/owner/payments/").status_code, 403)
        self.assertEqual(APIClient().get("/api/owner/payments/").status_code, 401)

    def test_admin_list_and_filters(self):
        response = self.admin_client.get("/api/admin/payments/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)

        response = self.admin_client.get("/api/admin/payments/", {"business": str(self.other_business.pk)})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["business_type"], "venue")

        response = self.admin_client.get(
            "/api/admin/payments/", {"subscription": str(self.business.subscription.pk)},
        )
        self.assertEqual(response.data["count"], 1)

    def test_admin_create_payment_201(self):
        payload = {"subscription": str(self.business.subscription.pk), "amount": "75000", "note": "Qo'shimcha"}
        response = self.admin_client.post("/api/admin/payments/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["confirmed_by"], self.admin.id)
        self.assertEqual(response.data["owner_phone"], "+998901234567")
        self.assertEqual(PaymentLog.objects.filter(subscription=self.business.subscription).count(), 2)

    def test_admin_create_amount_not_positive_400(self):
        for amount in ("0", "-5"):
            response = self.admin_client.post(
                "/api/admin/payments/",
                {"subscription": str(self.business.subscription.pk), "amount": amount}, format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn("amount", response.data["error"]["details"])

    def test_admin_create_regular_user_403(self):
        response = auth(self.owner).post(
            "/api/admin/payments/", {"subscription": str(self.business.subscription.pk), "amount": "1"},
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.data)


# ---------------------------------------------------------------------------
# Celery vazifalari: muddati o'tganlar va eslatmalar
# ---------------------------------------------------------------------------
class ExpiryTaskTests(SubscriptionTestCase):
    def setUp(self):
        super().setUp()
        self.trial_business = make_business(make_user("sinov"), self.admin, name="Sinov")
        self.active_business = make_business(make_user("faol"), self.admin, business_type="venue", name="Faol")
        self.fresh_business = make_business(make_user("yangi"), self.admin, name="Yangi")

    def test_check_expired_marks_both_trial_and_active(self):
        now = timezone.now()
        Subscription.objects.filter(business=self.trial_business).update(trial_ends_at=now - timedelta(hours=1))
        Subscription.objects.filter(business=self.active_business).update(
            status="active", subscription_ends_at=now - timedelta(days=1),
        )

        self.assertEqual(check_expired_subscriptions_task(), 2)

        self.trial_business.refresh_from_db()
        self.active_business.refresh_from_db()
        self.fresh_business.refresh_from_db()
        self.assertEqual(self.trial_business.subscription.status, "expired")
        self.assertEqual(self.active_business.subscription.status, "expired")
        self.assertFalse(self.trial_business.is_visible)
        self.assertFalse(self.active_business.is_visible)
        self.assertEqual(self.fresh_business.subscription.status, "trial")
        self.assertTrue(self.fresh_business.is_visible)

    def test_check_expired_nothing_to_do(self):
        self.assertEqual(check_expired_subscriptions(), 0)
        self.assertEqual(check_expired_subscriptions_task(), 0)

    def test_active_with_future_end_not_expired(self):
        Subscription.objects.filter(business=self.active_business).update(
            status="active", subscription_ends_at=timezone.now() + timedelta(days=1),
            trial_ends_at=timezone.now() - timedelta(days=10),  # trial sanasi o'tgan, lekin status active
        )
        self.assertEqual(check_expired_subscriptions(), 0)


class ExpiryReminderTests(SubscriptionTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("ega")
        self.business = make_business(self.owner, self.admin)
        self.subscription = self.business.subscription
        Notification.objects.all().delete()

    def _set_trial_end(self, days):
        Subscription.objects.filter(pk=self.subscription.pk).update(
            trial_ends_at=timezone.now() + timedelta(days=days),
        )

    def _owner_notes(self):
        return Notification.objects.filter(user=self.owner, kind=Notification.KIND_SUBSCRIPTION)

    def test_reminder_sent_once_for_each_day(self):
        self._set_trial_end(5)
        self.assertEqual(notify_expiring_subscriptions_task(), 1)
        self.assertEqual(send_expiry_reminders(), 0)  # takrorlanmaydi
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.reminded_days, [5])

        note = self._owner_notes().get()
        self.assertEqual(note.title, "Bepul sinovga 5 kun qoldi")
        self.assertEqual(note.level, Notification.LEVEL_INFO)
        self.assertIn("sinov muddati", note.body)

        self._set_trial_end(3)
        self.assertEqual(send_expiry_reminders(), 1)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.reminded_days, [5, 3])
        self.assertEqual(self._owner_notes().latest("created_at").level, Notification.LEVEL_WARNING)

    def test_no_reminder_on_other_days(self):
        for days in (7, 4, 1, 0):
            self._set_trial_end(days)
            self.assertEqual(send_expiry_reminders(), 0, days)
        self.assertEqual(self._owner_notes().count(), 0)

    def test_reminder_for_active_subscription_uses_subscription_end(self):
        Subscription.objects.filter(pk=self.subscription.pk).update(
            status="active",
            subscription_ends_at=timezone.now() + timedelta(days=2),
            trial_ends_at=timezone.now() - timedelta(days=20),
        )
        self.assertEqual(send_expiry_reminders(), 1)
        note = self._owner_notes().get()
        self.assertEqual(note.title, "Obunangizga 2 kun qoldi")
        self.assertIn("obuna", note.body)
        self.assertEqual(note.level, Notification.LEVEL_WARNING)

    def test_no_reminder_for_expired_or_past(self):
        Subscription.objects.filter(pk=self.subscription.pk).update(
            status="expired", trial_ends_at=timezone.now() + timedelta(days=3),
        )
        self.assertEqual(send_expiry_reminders(), 0)
        Subscription.objects.filter(pk=self.subscription.pk).update(
            status="trial", trial_ends_at=timezone.now() - timedelta(days=3),
        )
        self.assertEqual(send_expiry_reminders(), 0)

    def test_reminded_days_reset_after_renewal_approval(self):
        self._set_trial_end(3)
        send_expiry_reminders()
        renewal, _ = request_renewal(business=self.business, plan=plan_for("restaurant", 1))
        self.admin_client.post(f"/api/admin/subscription-requests/{renewal.pk}/approve/", {}, format="json")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.reminded_days, [])


# ---------------------------------------------------------------------------
# Bepul sinov faqat bir marta
# ---------------------------------------------------------------------------
class TrialOnceTests(SubscriptionTestCase):
    def test_trial_flag_set_after_approval(self):
        owner = make_user("ega")
        self.assertFalse(owner.has_used_trial)
        business = make_business(owner, self.admin)
        self.assertTrue(owner.has_used_trial)
        self.assertEqual(business.subscription.status, "trial")
        self.assertEqual(business.subscription.plan, plan_for("restaurant", 1))
        self.assertLess(
            abs((business.subscription.trial_ends_at - (timezone.now() + timedelta(days=7))).total_seconds()), 60,
        )

    def test_second_business_cannot_start_trial(self):
        owner = make_user("ega")
        make_business(owner, self.admin)

        application = BusinessApplication.objects.create(
            applicant=owner, business_type="venue", business_name="Ikkinchi",
        )
        second = Business.objects.create(
            owner=owner, application=application, name="Ikkinchi", business_type="venue",
            is_visible=False,
        )
        with self.assertRaises(TrialAlreadyUsed):
            start_trial(business=second)
        self.assertFalse(Subscription.objects.filter(business=second).exists())

    def test_trial_application_rejected_when_trial_already_used(self):
        user = make_user("eski")
        User.objects.filter(pk=user.pk).update(has_used_trial=True)
        user.refresh_from_db()
        with self.assertRaises(TrialNotAvailable):
            submit_application(applicant=user, business_type="restaurant", business_name="Yana")

    def test_approving_trial_application_fails_when_trial_used(self):
        user = make_user("eski")
        application, _, _ = submit_application(
            applicant=user, business_type="restaurant", business_name="Yana",
        )
        User.objects.filter(pk=user.pk).update(has_used_trial=True)
        application.refresh_from_db()
        with self.assertRaises(TrialNotAvailable):
            approve_application(application=application, approved_by=self.admin)

    def test_paid_application_activates_without_trial(self):
        user = make_user("pullik")
        User.objects.filter(pk=user.pk).update(has_used_trial=True)
        user.refresh_from_db()
        plan = plan_for("venue", 3)
        application, business, _ = submit_application(
            applicant=user, business_type="venue", business_name="Saroy", plan=plan,
        )
        before = timezone.now()
        approve_application(application=application, approved_by=self.admin)

        subscription = Subscription.objects.get(business=business)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan, plan)
        self.assertLess(
            abs((subscription.subscription_ends_at - (before + timedelta(days=90))).total_seconds()), 60,
        )
        self.assertEqual(subscription.payments.get().amount, plan.price)
        business.refresh_from_db()
        self.assertTrue(business.is_visible)


# ---------------------------------------------------------------------------
# Tuzatishlar: sinov muddati manbai, reminded_days, days_left
# ---------------------------------------------------------------------------
class SubscriptionFixTests(SubscriptionTestCase):
    def test_trial_length_comes_from_platform_settings(self):
        from common.models import PlatformSettings

        platform = PlatformSettings.get_solo()
        platform.trial_days = 12
        platform.save()
        SubscriptionPlan.objects.filter(business_type="restaurant", duration_months=1).update(trial_days=3)

        owner = make_user("owner")
        business = make_business(owner, self.admin)
        days = (business.subscription.trial_ends_at - timezone.now()).total_seconds() / 86400
        self.assertAlmostEqual(days, 12, delta=0.01)

        response = auth(owner).get("/api/owner/subscription/")
        self.assertEqual(response.data["days_left"], 12, "days_left yuqoriga yaxlitlanadi")

    def test_application_message_matches_trial_length(self):
        from common.models import PlatformSettings

        platform = PlatformSettings.get_solo()
        platform.trial_days = 9
        platform.save()
        owner = make_user("owner")
        response = auth(owner).post(
            "/api/business-applications/", {"business_type": "restaurant", "business_name": "Shoxona"}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["trial_days"], 9)
        self.assertIn("9 kunlik", response.data["message"])

    def test_admin_activate_resets_reminders(self):
        owner = make_user("owner")
        business = make_business(owner, self.admin)
        subscription = business.subscription
        subscription.reminded_days = [5, 3]
        subscription.save(update_fields=["reminded_days"])

        response = self.admin_client.post(f"/api/admin/subscriptions/{subscription.id}/activate/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        subscription.refresh_from_db()
        self.assertEqual(subscription.reminded_days, [])

    def test_days_left_rounds_up(self):
        owner = make_user("owner")
        business = make_business(owner, self.admin)
        Subscription.objects.filter(business=business).update(
            status="active", subscription_ends_at=timezone.now() + timedelta(days=2, hours=1),
        )
        response = auth(owner).get("/api/owner/subscription/")
        self.assertEqual(response.data["days_left"], 3)
        Subscription.objects.filter(business=business).update(subscription_ends_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(auth(owner).get("/api/owner/subscription/").data["days_left"], 0)
