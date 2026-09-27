import datetime
import io
import itertools
import shutil
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from businesses.models import Business, Room
from businesses.services import approve_application, submit_application
from notifications.models import Notification
from reservations.models import Availability, Reservation
from reviews.models import Review, ReviewPhoto
from reviews.services import recalculate_business_rating, recalculate_ranks

User = get_user_model()

_seq = itertools.count(1)


# --------------------------------------------------------------------------
# Yordamchilar
# --------------------------------------------------------------------------

def make_user(username=None, *, phone="+998901234567", **extra):
    n = next(_seq)
    return User.objects.create_user(
        username=username or f"user{n}", password="StrongPass123!",
        full_name=extra.pop("full_name", f"User {n}"), phone_number=phone, **extra,
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
        applicant=owner, business_type=business_type, business_name=name or f"Biznes {next(_seq)}",
    )
    approve_application(application=application, approved_by=admin)
    business.refresh_from_db()
    owner.refresh_from_db()
    return business


def make_reservation(user, business, status="completed", days_ago=1):
    """Restoran uchun o'tgan kunga bron yaratadi (xona/jadval avtomatik)."""
    room = business.rooms.first() or Room.objects.create(
        business=business, name="Zal", room_type="standard", capacity=20, deposit_tier="pro",
    )
    date = timezone.localdate() - datetime.timedelta(days=days_ago)
    availability = Availability.objects.filter(room=room, date=date).first() or Availability.objects.create(
        business=business, room=room, date=date, start_time=datetime.time(10), end_time=datetime.time(23),
    )
    return Reservation.objects.create(
        user=user, business=business, room=room, availability=availability,
        start_time=datetime.time(18), end_time=datetime.time(20), guests_count=2, status=status,
    )


def make_review(user, business, rating=5, comment="", days_ago=1):
    reservation = make_reservation(user, business, days_ago=days_ago)
    return Review.objects.create(user=user, business=business, reservation=reservation, rating=rating, comment=comment)


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def png_file(name="a.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ReviewBaseMixin:
    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.owner = make_user("owner_r", phone="+998901111111")
        self.business = make_business(self.owner, "restaurant", "Shoxona", admin=self.admin)
        self.customer = make_user("mijoz", phone="+998902222222")
        self.client = client_for(self.customer)
        self.owner_client = client_for(self.owner)
        self.reservation = make_reservation(self.customer, self.business)

    def post_review(self, client=None, **extra):
        payload = {"reservation": str(self.reservation.id), "rating": 5, "comment": "Zo'r joy"}
        payload.update(extra)
        return (client or self.client).post("/api/reviews/", payload, format="json")


# --------------------------------------------------------------------------
# Model va servislar
# --------------------------------------------------------------------------

class ReviewModelTests(ReviewBaseMixin, TestCase):
    def test_clean_ok(self):
        Review(user=self.customer, business=self.business, reservation=self.reservation, rating=4).full_clean()

    def test_clean_foreign_reservation(self):
        other = make_user()
        review = Review(user=other, business=self.business, reservation=self.reservation, rating=4)
        with self.assertRaises(ValidationError) as ctx:
            review.full_clean()
        self.assertIn("reservation", ctx.exception.message_dict)

    def test_clean_wrong_business(self):
        other_business = make_business(make_user(), "restaurant")
        review = Review(user=self.customer, business=other_business, reservation=self.reservation, rating=4)
        with self.assertRaises(ValidationError) as ctx:
            review.full_clean()
        self.assertIn("business", ctx.exception.message_dict)

    def test_clean_not_completed(self):
        pending = make_reservation(self.customer, self.business, status="pending", days_ago=2)
        review = Review(user=self.customer, business=self.business, reservation=pending, rating=4)
        with self.assertRaises(ValidationError) as ctx:
            review.full_clean()
        self.assertIn("reservation", ctx.exception.message_dict)

    def test_clean_rating_bounds(self):
        for bad in (0, 6):
            review = Review(user=self.customer, business=self.business, reservation=self.reservation, rating=bad)
            with self.assertRaises(ValidationError) as ctx:
                review.full_clean()
            self.assertIn("rating", ctx.exception.message_dict)

    def test_one_review_per_reservation(self):
        from django.db import IntegrityError, transaction
        Review.objects.create(user=self.customer, business=self.business, reservation=self.reservation, rating=4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Review.objects.create(user=self.customer, business=self.business, reservation=self.reservation, rating=3)


class ReviewServicesTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.customer = make_user("mijoz")

    def biz(self, name, business_type="restaurant"):
        return make_business(make_user(), business_type, name, admin=self.admin)

    def test_recalculate_business_rating(self):
        business = self.biz("A")
        make_review(self.customer, business, rating=5, days_ago=1)
        make_review(self.customer, business, rating=4, days_ago=2)
        make_review(self.customer, business, rating=4, days_ago=3)
        # signal allaqachon hisoblagan; qo'lda ham chaqiramiz
        avg = recalculate_business_rating(business)
        self.assertEqual(avg, 4.33)
        business.refresh_from_db()
        self.assertEqual(business.rating_avg, 4.33)
        self.assertEqual(business.reviews_count, 3)
        self.assertEqual(business.rating_points, 13)

    def test_recalculate_rating_empty(self):
        business = self.biz("A")
        Business.objects.filter(pk=business.pk).update(rating_avg=4.5, reviews_count=3, rating_points=13)
        business.refresh_from_db()
        recalculate_business_rating(business)
        business.refresh_from_db()
        self.assertEqual((business.rating_avg, business.reviews_count, business.rating_points), (0, 0, 0))

    def test_rank_order_by_points_then_count_then_created(self):
        a = self.biz("A")
        b = self.biz("B")
        c = self.biz("C")
        d = self.biz("D")
        venue = self.biz("V", "venue")

        make_review(self.customer, a, rating=5, days_ago=1)
        make_review(self.customer, a, rating=4, days_ago=2)   # 9 ball
        make_review(self.customer, b, rating=5, days_ago=1)   # 5 ball
        make_review(self.customer, c, rating=5, days_ago=1)   # 5 ball, keyin yaratilgan → B dan keyin
        make_review(self.customer, venue, rating=5)           # boshqa tur, alohida reyting

        for obj in (a, b, c, d, venue):
            obj.refresh_from_db()
        self.assertEqual([a.rank, b.rank, c.rank, d.rank], [1, 2, 3, 4])
        self.assertEqual(venue.rank, 1)

    def test_rank_tie_is_stable_on_recalculate(self):
        a = self.biz("A")
        b = self.biz("B")
        make_review(self.customer, a, rating=5)
        make_review(self.customer, b, rating=5)
        a.refresh_from_db(); b.refresh_from_db()
        first = (a.rank, b.rank)
        self.assertEqual(sorted(first), [1, 2])
        self.assertEqual(recalculate_ranks("restaurant"), 0)
        recalculate_ranks("restaurant")
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.rank, b.rank), first)

    def test_hidden_business_rank_zero(self):
        a = self.biz("A")
        hidden = self.biz("H")
        make_review(self.customer, hidden, rating=5)
        hidden.refresh_from_db()
        self.assertEqual(hidden.rank, 1)

        hidden.is_visible = False
        hidden.save(update_fields=["is_visible"])
        make_review(self.customer, a, rating=3)
        a.refresh_from_db(); hidden.refresh_from_db()
        self.assertEqual(hidden.rank, 0)
        self.assertEqual(a.rank, 1)


# --------------------------------------------------------------------------
# Yaratish
# --------------------------------------------------------------------------

class ReviewCreateTests(ReviewBaseMixin, TestCase):
    def test_anonymous_401(self):
        r = self.post_review(client=APIClient())
        self.assertEqual(r.status_code, 401, r.data)

    def test_success_updates_business(self):
        r = self.post_review(rating=4, comment="Yaxshi")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["rating"], 4)
        self.assertEqual(r.data["comment"], "Yaxshi")
        self.assertEqual(r.data["user_username"], "mijoz")
        self.assertEqual(r.data["business_name"], "Shoxona")
        self.assertEqual(str(r.data["reservation"]), str(self.reservation.id))
        self.assertEqual(r.data["photos"], [])

        self.business.refresh_from_db()
        self.assertEqual(self.business.rating_avg, 4.0)
        self.assertEqual(self.business.reviews_count, 1)
        self.assertEqual(self.business.rating_points, 4)
        self.assertEqual(self.business.rank, 1)

        review = Review.objects.get(pk=r.data["id"])
        self.assertEqual(review.user, self.customer)
        self.assertEqual(review.business, self.business)

    def test_second_review_updates_average(self):
        self.assertEqual(self.post_review(rating=5).status_code, 201)
        second = make_reservation(self.customer, self.business, days_ago=2)
        r = self.post_review(reservation=str(second.id), rating=2)
        self.assertEqual(r.status_code, 201, r.data)
        self.business.refresh_from_db()
        self.assertEqual(self.business.rating_avg, 3.5)
        self.assertEqual(self.business.reviews_count, 2)
        self.assertEqual(self.business.rating_points, 7)

    def test_comment_optional(self):
        r = self.client.post("/api/reviews/", {"reservation": str(self.reservation.id), "rating": 3}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["comment"], "")

    def test_pending_reservation_400(self):
        pending = make_reservation(self.customer, self.business, status="pending", days_ago=2)
        r = self.post_review(reservation=str(pending.id))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("reservation", r.data["error"]["details"])

    def test_cancelled_reservation_400(self):
        cancelled = make_reservation(self.customer, self.business, status="cancelled", days_ago=2)
        r = self.post_review(reservation=str(cancelled.id))
        self.assertEqual(r.status_code, 400, r.data)

    def test_confirmed_reservation_400(self):
        confirmed = make_reservation(self.customer, self.business, status="confirmed", days_ago=2)
        r = self.post_review(reservation=str(confirmed.id))
        self.assertEqual(r.status_code, 400, r.data)

    def test_foreign_reservation_400(self):
        stranger = make_user("begona", phone="+998903333333")
        r = self.post_review(client=client_for(stranger))
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("reservation", r.data["error"]["details"])
        self.assertEqual(Review.objects.count(), 0)

    def test_unknown_reservation_400(self):
        r = self.post_review(reservation=str(uuid.uuid4()))
        self.assertEqual(r.status_code, 400, r.data)

    def test_twice_400(self):
        self.assertEqual(self.post_review().status_code, 201)
        r = self.post_review(rating=1)
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("allaqachon", r.data["error"]["message"])
        self.assertEqual(Review.objects.count(), 1)

    def test_rating_out_of_range_400(self):
        for bad in (0, 6, -1):
            r = self.post_review(rating=bad)
            self.assertEqual(r.status_code, 400, (bad, r.data))
            self.assertIn("rating", r.data["error"]["details"])
        self.assertEqual(Review.objects.count(), 0)

    def test_rating_required_400(self):
        r = self.client.post("/api/reviews/", {"reservation": str(self.reservation.id)}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("rating", r.data["error"]["details"])

    def test_owner_notified_success_level_for_high_rating(self):
        Notification.objects.filter(user=self.owner).delete()
        self.assertEqual(self.post_review(rating=4, comment="Juda yaxshi").status_code, 201)
        note = Notification.objects.filter(user=self.owner, kind=Notification.KIND_REVIEW).first()
        self.assertIsNotNone(note)
        self.assertEqual(note.title, "Yangi sharh — 4★")
        self.assertEqual(note.level, Notification.LEVEL_SUCCESS)
        self.assertEqual(note.body, "Juda yaxshi")

    def test_owner_notified_warning_level_for_low_rating(self):
        Notification.objects.filter(user=self.owner).delete()
        self.assertEqual(self.post_review(rating=3, comment="").status_code, 201)
        note = Notification.objects.filter(user=self.owner, kind=Notification.KIND_REVIEW).first()
        self.assertEqual(note.title, "Yangi sharh — 3★")
        self.assertEqual(note.level, Notification.LEVEL_WARNING)
        self.assertEqual(note.body, "Mijoz baho qoldirdi.")

    def test_staff_can_review_own_completed_reservation(self):
        # sharh uchun IsCustomer talab qilinmaydi — faqat o'z broni bo'lsa bo'ldi
        res = make_reservation(self.admin, self.business, days_ago=3)
        r = client_for(self.admin).post("/api/reviews/", {"reservation": str(res.id), "rating": 5}, format="json")
        self.assertEqual(r.status_code, 201, r.data)


# --------------------------------------------------------------------------
# Detail / patch / delete
# --------------------------------------------------------------------------

class ReviewDetailTests(ReviewBaseMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.review = Review.objects.create(
            user=self.customer, business=self.business, reservation=self.reservation, rating=5, comment="Zo'r",
        )
        self.url = f"/api/reviews/{self.review.id}/"

    def test_get_any_authenticated(self):
        r = client_for(make_user()).get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["rating"], 5)

    def test_get_anonymous_401(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_get_unknown_404(self):
        r = self.client.get(f"/api/reviews/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, 404, r.data)

    def test_patch_by_owner_recalculates(self):
        r = self.client.patch(self.url, {"rating": 3, "comment": "O'rtacha"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["rating"], 3)
        self.assertEqual(r.data["comment"], "O'rtacha")
        self.business.refresh_from_db()
        self.assertEqual(self.business.rating_avg, 3.0)
        self.assertEqual(self.business.rating_points, 3)
        self.assertEqual(self.business.reviews_count, 1)

    def test_patch_only_comment(self):
        r = self.client.patch(self.url, {"comment": "Yangilandi"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["rating"], 5)
        self.assertEqual(r.data["comment"], "Yangilandi")

    def test_patch_by_stranger_403(self):
        r = client_for(make_user()).patch(self.url, {"rating": 1}, format="json")
        self.assertEqual(r.status_code, 403, r.data)
        self.review.refresh_from_db()
        self.assertEqual(self.review.rating, 5)

    def test_patch_by_business_owner_403(self):
        r = self.owner_client.patch(self.url, {"rating": 1}, format="json")
        self.assertEqual(r.status_code, 403, r.data)

    def test_patch_by_staff_403(self):
        r = client_for(self.admin).patch(self.url, {"rating": 1}, format="json")
        self.assertEqual(r.status_code, 403, r.data)

    def test_patch_empty_payload_400(self):
        r = self.client.patch(self.url, {}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("comment", r.data["error"]["message"])
        r = self.client.patch(self.url, {"reservation": str(uuid.uuid4())}, format="json")
        self.assertEqual(r.status_code, 400, r.data)

    def test_patch_invalid_rating_400(self):
        r = self.client.patch(self.url, {"rating": 9}, format="json")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("rating", r.data["error"]["details"])

    def test_patch_does_not_create_notification(self):
        Notification.objects.filter(user=self.owner).delete()
        self.client.patch(self.url, {"rating": 1}, format="json")
        self.assertFalse(Notification.objects.filter(user=self.owner, kind="review").exists())

    def test_delete_by_owner_recalculates(self):
        self.business.refresh_from_db()
        self.assertEqual(self.business.reviews_count, 1)
        r = self.client.delete(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Review.objects.filter(pk=self.review.pk).exists())
        self.business.refresh_from_db()
        self.assertEqual((self.business.rating_avg, self.business.reviews_count, self.business.rating_points), (0, 0, 0))

    def test_delete_by_staff(self):
        r = client_for(self.admin).delete(self.url)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Review.objects.filter(pk=self.review.pk).exists())

    def test_delete_by_stranger_403(self):
        r = client_for(make_user()).delete(self.url)
        self.assertEqual(r.status_code, 403, r.data)
        self.assertTrue(Review.objects.filter(pk=self.review.pk).exists())

    def test_delete_by_business_owner_403(self):
        r = self.owner_client.delete(self.url)
        self.assertEqual(r.status_code, 403, r.data)

    def test_delete_allows_new_review_for_same_reservation(self):
        self.client.delete(self.url)
        r = self.post_review(rating=2)
        self.assertEqual(r.status_code, 201, r.data)


# --------------------------------------------------------------------------
# Ro'yxatlar
# --------------------------------------------------------------------------

class BusinessReviewListTests(ReviewBaseMixin, TestCase):
    def url(self, business=None):
        return f"/api/businesses/{(business or self.business).id}/reviews/"

    def test_anonymous_can_read(self):
        make_review(self.customer, self.business, rating=5, days_ago=2)
        r = APIClient().get(self.url())
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(set(r.data.keys()), {"count", "total_pages", "current_page", "next", "previous", "results"})

    def test_hidden_business_404(self):
        self.business.is_visible = False
        self.business.save(update_fields=["is_visible"])
        r = APIClient().get(self.url())
        self.assertEqual(r.status_code, 404, r.data)
        self.assertEqual(r.data["error"]["code"], "not_found")

    def test_unknown_business_404(self):
        r = APIClient().get(f"/api/businesses/{uuid.uuid4()}/reviews/")
        self.assertEqual(r.status_code, 404, r.data)

    def test_only_this_business(self):
        other = make_business(make_user(), "restaurant")
        make_review(self.customer, other, rating=1)
        make_review(self.customer, self.business, rating=5, days_ago=2)
        r = APIClient().get(self.url())
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["rating"], 5)
        r = APIClient().get(self.url(other))
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["rating"], 1)

    def test_rating_filters(self):
        for i, rating in enumerate((1, 3, 5), start=2):
            make_review(self.customer, self.business, rating=rating, days_ago=i)
        r = APIClient().get(self.url(), {"min_rating": 3})
        self.assertEqual(r.data["count"], 2)
        self.assertEqual({x["rating"] for x in r.data["results"]}, {3, 5})
        r = APIClient().get(self.url(), {"max_rating": 3})
        self.assertEqual({x["rating"] for x in r.data["results"]}, {1, 3})
        r = APIClient().get(self.url(), {"rating": 5})
        self.assertEqual(r.data["count"], 1)
        r = APIClient().get(self.url(), {"min_rating": 2, "max_rating": 4})
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["rating"], 3)

    def test_pagination_page_size_10(self):
        for i in range(12):
            make_review(self.customer, self.business, rating=4, days_ago=i + 2)
        r = APIClient().get(self.url())
        self.assertEqual(r.data["count"], 12)
        self.assertEqual(r.data["total_pages"], 2)
        self.assertEqual(r.data["current_page"], 1)
        self.assertEqual(len(r.data["results"]), 10)
        self.assertIsNotNone(r.data["next"])
        r = APIClient().get(self.url(), {"page": 2})
        self.assertEqual(len(r.data["results"]), 2)
        self.assertIsNone(r.data["next"])
        # max_page_size = 50
        r = APIClient().get(self.url(), {"page_size": 5})
        self.assertEqual(len(r.data["results"]), 5)

    def test_ordered_newest_first(self):
        old = make_review(self.customer, self.business, rating=2, days_ago=3)
        new = make_review(self.customer, self.business, rating=4, days_ago=2)
        r = APIClient().get(self.url())
        self.assertEqual([x["id"] for x in r.data["results"]], [str(new.id), str(old.id)])


class MyReviewListTests(ReviewBaseMixin, TestCase):
    url = "/api/reviews/my/"

    def test_anonymous_401(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_only_own(self):
        make_review(self.customer, self.business, rating=5, days_ago=2)
        other = make_user("boshqa", phone="+998904444444")
        make_review(other, self.business, rating=2, days_ago=2)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["user_username"], "mijoz")
        r = client_for(other).get(self.url)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["rating"], 2)

    def test_empty(self):
        r = self.client.get(self.url)
        self.assertEqual(r.data["count"], 0)
        self.assertEqual(r.data["results"], [])


class OwnerReviewListTests(ReviewBaseMixin, TestCase):
    url = "/api/owner/reviews/"

    def test_customer_403(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_staff_403(self):
        self.assertEqual(client_for(self.admin).get(self.url).status_code, 403)

    def test_anonymous_401(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_only_own_business(self):
        make_review(self.customer, self.business, rating=5, days_ago=2)
        make_review(self.customer, self.business, rating=2, days_ago=3)
        other_owner = make_user("owner_x", phone="+998905555555")
        other = make_business(other_owner, "restaurant", admin=self.admin)
        make_review(self.customer, other, rating=1)

        r = self.owner_client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 2)
        self.assertTrue(all(str(x["business"]) == str(self.business.id) for x in r.data["results"]))

        r = self.owner_client.get(self.url, {"min_rating": 4})
        self.assertEqual(r.data["count"], 1)

        r = client_for(other_owner).get(self.url)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["rating"], 1)

    def test_hidden_business_owner_still_sees(self):
        make_review(self.customer, self.business, rating=5, days_ago=2)
        self.business.is_visible = False
        self.business.save(update_fields=["is_visible"])
        r = self.owner_client.get(self.url)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 1)


# --------------------------------------------------------------------------
# Rasmlar
# --------------------------------------------------------------------------

class ReviewPhotoTests(ReviewBaseMixin, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_root = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.review = Review.objects.create(
            user=self.customer, business=self.business, reservation=self.reservation, rating=5,
        )
        self.url = f"/api/reviews/{self.review.id}/photos/"

    def upload(self, client=None, name="a.png"):
        return (client or self.client).post(self.url, {"image": png_file(name)}, format="multipart")

    def test_anonymous_401(self):
        self.assertEqual(APIClient().post(self.url, {"image": png_file()}, format="multipart").status_code, 401)

    def test_owner_uploads(self):
        r = self.upload()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(str(r.data["review"]), str(self.review.id))
        self.assertTrue(r.data["image"].startswith("http"))
        self.assertIn("review_photos/", r.data["image"])
        self.assertEqual(self.review.photos.count(), 1)

        detail = self.client.get(f"/api/reviews/{self.review.id}/").data
        self.assertEqual(len(detail["photos"]), 1)

    def test_stranger_403(self):
        r = self.upload(client=client_for(make_user()))
        self.assertEqual(r.status_code, 403, r.data)
        self.assertEqual(self.review.photos.count(), 0)

    def test_staff_cannot_add_403(self):
        r = self.upload(client=client_for(self.admin))
        self.assertEqual(r.status_code, 403, r.data)

    def test_unknown_review_404(self):
        r = self.client.post(f"/api/reviews/{uuid.uuid4()}/photos/", {"image": png_file()}, format="multipart")
        self.assertEqual(r.status_code, 404, r.data)

    def test_missing_image_400(self):
        r = self.client.post(self.url, {}, format="multipart")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("image", r.data["error"]["details"])

    def test_wrong_extension_400(self):
        bad = SimpleUploadedFile("a.txt", b"hello", content_type="text/plain")
        r = self.client.post(self.url, {"image": bad}, format="multipart")
        self.assertEqual(r.status_code, 400, r.data)

    def test_limit_five_400(self):
        for i in range(5):
            self.assertEqual(self.upload(name=f"p{i}.png").status_code, 201)
        r = self.upload(name="p6.png")
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn("5", r.data["error"]["message"])
        self.assertEqual(self.review.photos.count(), 5)

    def test_delete_by_owner(self):
        photo_id = self.upload().data["id"]
        r = self.client.delete(f"/api/review-photos/{photo_id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(ReviewPhoto.objects.filter(pk=photo_id).exists())
        # limit qayta ochiladi
        for i in range(5):
            self.assertEqual(self.upload(name=f"q{i}.png").status_code, 201)

    def test_delete_by_staff(self):
        photo_id = self.upload().data["id"]
        r = client_for(self.admin).delete(f"/api/review-photos/{photo_id}/")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(ReviewPhoto.objects.filter(pk=photo_id).exists())

    def test_delete_by_stranger_403(self):
        photo_id = self.upload().data["id"]
        r = client_for(make_user()).delete(f"/api/review-photos/{photo_id}/")
        self.assertEqual(r.status_code, 403, r.data)
        self.assertTrue(ReviewPhoto.objects.filter(pk=photo_id).exists())

    def test_delete_by_business_owner_403(self):
        photo_id = self.upload().data["id"]
        r = self.owner_client.delete(f"/api/review-photos/{photo_id}/")
        self.assertEqual(r.status_code, 403, r.data)

    def test_delete_unknown_404(self):
        r = self.client.delete(f"/api/review-photos/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, 404, r.data)

    def test_photos_removed_with_review(self):
        self.upload()
        self.client.delete(f"/api/reviews/{self.review.id}/")
        self.assertEqual(ReviewPhoto.objects.count(), 0)
