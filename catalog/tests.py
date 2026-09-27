import io
import itertools
import shutil
import tempfile

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from account.models import User
from businesses.models import VenuePricing
from businesses.services import approve_application, submit_application
from catalog.models import RestaurantMenuItem, VenueMenuItem
from common.cache import get_business_version
from subscriptions.models import Subscription

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
    return User.objects.create_user(
        username=username or f"admin{next(_seq)}", password=PASSWORD, full_name="Admin",
        is_staff=True, is_superuser=True,
    )


def make_business(business_type="restaurant", name="Shoxona", *, owner=None, admin=None, approve=True):
    owner = owner or make_user()
    application, business, _ = submit_application(
        applicant=owner, business_type=business_type, business_name=name,
    )
    if approve:
        approve_application(application=application, approved_by=admin or make_admin())
    owner.refresh_from_db()
    business.refresh_from_db()
    return owner, business


def client_for(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user)
    return client


def png_file(name="a.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (30, 120, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def make_dish(business, name="Osh", price="45000", **extra):
    return RestaurantMenuItem.objects.create(business=business, name=name, price=price, **extra)


def make_venue_dish(business, name="Osh", **extra):
    return VenueMenuItem.objects.create(business=business, name=name, **extra)


class MediaTestCase(TestCase):
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
# Ommaviy restoran menyusi
# ----------------------------------------------------------------------------
class PublicRestaurantMenuTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.restaurant = make_business("restaurant", "Shoxona", admin=self.admin)
        self.osh = make_dish(self.restaurant, "Osh", "45000", category="main")
        self.shurva = make_dish(self.restaurant, "Sho'rva", "25000", category="soup")
        self.somsa = make_dish(self.restaurant, "Somsa", "12000", category="starter", is_available=False)
        self.anon = client_for()

    def url(self, business=None):
        return f"/api/businesses/{(business or self.restaurant).id}/menu/"

    def names(self, response):
        return [row["name"] for row in response.data["results"]]

    def test_only_available_items_listed(self):
        response = self.anon.get(self.url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(self.names(response), ["Osh", "Sho'rva"])  # category, name tartibi
        self.assertNotIn("Somsa", self.names(response))
        row = response.data["results"][0]
        self.assertEqual(row["category_display"], "Asosiy taom")
        self.assertEqual(row["price"], "45000.00")
        self.assertEqual(row["business"], self.restaurant.id)
        # is_available=false so'ralsa ham yashirinlar chiqmaydi
        self.assertEqual(self.anon.get(self.url(), {"is_available": "false"}).data["count"], 0)

    def test_hidden_business_404(self):
        _, hidden = make_business("restaurant", "Yashirin", approve=False)
        make_dish(hidden)
        response = self.anon.get(self.url(hidden))
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(self.anon.get("/api/businesses/00000000-0000-0000-0000-000000000000/menu/").status_code, 404)

    def test_filters(self):
        self.assertEqual(self.names(self.anon.get(self.url(), {"search": "osh"})), ["Osh"])
        self.assertEqual(self.names(self.anon.get(self.url(), {"category": "soup"})), ["Sho'rva"])
        self.assertEqual(self.names(self.anon.get(self.url(), {"min_price": 30000})), ["Osh"])
        self.assertEqual(self.names(self.anon.get(self.url(), {"max_price": 30000})), ["Sho'rva"])
        self.assertEqual(self.anon.get(self.url(), {"min_price": 20000, "max_price": 30000}).data["count"], 1)
        self.assertEqual(self.anon.get(self.url(), {"category": "dessert"}).data["count"], 0)

    def test_pagination_shape(self):
        response = self.anon.get(self.url(), {"page_size": 1})
        for key in ("count", "total_pages", "current_page", "next", "previous", "results"):
            self.assertIn(key, response.data)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertEqual(len(response.data["results"]), 1)

    def test_menu_inside_business_detail_only_available(self):
        data = self.anon.get(f"/api/businesses/{self.restaurant.id}/").data
        self.assertEqual(sorted(m["name"] for m in data["menu"]), ["Osh", "Sho'rva"])


# ----------------------------------------------------------------------------
# Ommaviy to'yxona menyusi
# ----------------------------------------------------------------------------
class PublicVenueMenuTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.venue = make_business("venue", "Navro'z", admin=self.admin)
        make_venue_dish(self.venue, "Osh", category="main")
        make_venue_dish(self.venue, "Sho'rva", category="soup")
        self.anon = client_for()

    def url(self, business=None):
        return f"/api/businesses/{(business or self.venue).id}/venue-menu/"

    def test_list_and_filters(self):
        response = self.anon.get(self.url())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual([r["name"] for r in response.data["results"]], ["Osh", "Sho'rva"])
        self.assertNotIn("price", response.data["results"][0])
        self.assertEqual(self.anon.get(self.url(), {"search": "sho"}).data["count"], 1)
        self.assertEqual(self.anon.get(self.url(), {"category": "main"}).data["count"], 1)

    def test_hidden_business_404(self):
        _, hidden = make_business("venue", "Yashirin", approve=False)
        make_venue_dish(hidden)
        self.assertEqual(self.anon.get(self.url(hidden)).status_code, 404)

    def test_menu_inside_business_detail(self):
        data = self.anon.get(f"/api/businesses/{self.venue.id}/").data
        self.assertEqual(len(data["menu"]), 2)
        self.assertEqual(data["menu"][0]["category_display"], "Asosiy taom")


# ----------------------------------------------------------------------------
# Vitrina (showcase)
# ----------------------------------------------------------------------------
class ShowcaseRestaurantMenuTests(MediaTestCase):
    url = "/api/menu/restaurant/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        _, self.restaurant = make_business("restaurant", "Shoxona", admin=self.admin)
        self.anon = client_for()

    def test_items_with_photo_come_first(self):
        no_photo = make_dish(self.restaurant, "Rasmsiz", "10000")
        with_photo = make_dish(self.restaurant, "Rasmli", "20000", photo=png_file())
        newer_no_photo = make_dish(self.restaurant, "Yangi rasmsiz", "30000")

        response = self.anon.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(
            [r["id"] for r in response.data["results"]],
            [str(with_photo.id), str(newer_no_photo.id), str(no_photo.id)],
        )
        row = response.data["results"][0]
        self.assertEqual(row["business_name"], "Shoxona")
        self.assertIn("business_district", row)
        self.assertTrue(row["photo"].startswith("http"))

    def test_hidden_and_unavailable_excluded(self):
        make_dish(self.restaurant, "Ko'rinadi", "10000")
        make_dish(self.restaurant, "Tugagan", "10000", is_available=False)
        _, hidden = make_business("restaurant", "Yashirin", approve=False)
        make_dish(hidden, "Yashirinniki", "10000")

        response = self.anon.get(self.url)
        self.assertEqual([r["name"] for r in response.data["results"]], ["Ko'rinadi"])

        # biznes bloklansa vitrinadan yo'qoladi
        self.restaurant.is_visible = False
        self.restaurant.save(update_fields=["is_visible"])
        self.assertEqual(self.anon.get(self.url).data["count"], 0)


class ShowcaseVenueMenuTests(TestCase):
    url = "/api/menu/venue/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        _, self.venue = make_business("venue", "Navro'z", admin=self.admin)
        self.anon = client_for()

    def test_price_from_is_cheapest_package(self):
        make_venue_dish(self.venue, "Osh")
        VenuePricing.objects.create(business=self.venue, dish_count=1, price_per_person="80000")
        VenuePricing.objects.create(business=self.venue, dish_count=2, price_per_person="120000")
        _, other = make_business("venue", "Narxsiz", admin=self.admin)
        make_venue_dish(other, "Sho'rva")

        response = self.anon.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        by_name = {r["name"]: r for r in response.data["results"]}
        self.assertEqual(by_name["Osh"]["price_from"], "80000.00")
        self.assertEqual(by_name["Osh"]["business_name"], "Navro'z")
        self.assertIsNone(by_name["Sho'rva"]["price_from"])

    def test_hidden_venue_excluded_and_newest_first(self):
        first = make_venue_dish(self.venue, "Birinchi")
        second = make_venue_dish(self.venue, "Ikkinchi")
        _, hidden = make_business("venue", "Yashirin", approve=False)
        make_venue_dish(hidden, "Yashirinniki")

        response = self.anon.get(self.url)
        self.assertEqual([r["id"] for r in response.data["results"]], [str(second.id), str(first.id)])


# ----------------------------------------------------------------------------
# Egasi: restoran menyusi CRUD
# ----------------------------------------------------------------------------
class OwnerRestaurantMenuTests(MediaTestCase):
    url = "/api/owner/menu/restaurant/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.restaurant = make_business("restaurant", "Shoxona", admin=self.admin)
        self.client = client_for(self.owner)
        self.payload = {"name": "Osh", "category": "main", "price": "45000", "description": "Toshkent oshi"}

    def test_permissions(self):
        self.assertEqual(client_for().get(self.url).status_code, 401)
        response = client_for(make_user()).get(self.url)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("faqat biznes egalari", response.data["error"]["message"])

        unapproved, _ = make_business("restaurant", "Kutmoqda", approve=False)
        response = client_for(unapproved).post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("tasdiqlanmagan", response.data["error"]["message"])

        response = client_for(self.admin).get(self.url)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("boshqaruv paneliga", response.data["error"]["message"])

    def test_venue_owner_gets_403(self):
        venue_owner, _ = make_business("venue", "Navro'z", admin=self.admin)
        response = client_for(venue_owner).post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("restoran egalari", response.data["error"]["message"])
        self.assertEqual(client_for(venue_owner).get(self.url).status_code, 403)
        self.assertFalse(RestaurantMenuItem.objects.exists())

    def test_create_list_get(self):
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["name"], "Osh")
        self.assertEqual(response.data["price"], "45000.00")
        self.assertEqual(response.data["business"], self.restaurant.id)
        self.assertTrue(response.data["is_available"])
        item_id = response.data["id"]

        self.client.post(self.url, {**self.payload, "name": "Somsa", "category": "starter", "is_available": False}, format="json")
        _, other = make_business("restaurant", "Boshqa", admin=self.admin)
        make_dish(other, "Begona")

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)  # egasi yashirinlarni ham ko'radi
        self.assertEqual(self.client.get(self.url, {"is_available": "false"}).data["results"][0]["name"], "Somsa")
        self.assertEqual(self.client.get(self.url, {"search": "som"}).data["count"], 1)
        self.assertEqual(self.client.get(f"{self.url}{item_id}/").data["name"], "Osh")

    def test_create_validation_400(self):
        response = self.client.post(self.url, {**self.payload, "price": "-1"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertIn("price", response.data["error"]["details"])

        response = self.client.post(self.url, {"category": "main", "price": "1"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("name", response.data["error"]["details"])

        response = self.client.post(self.url, {**self.payload, "category": "pizza"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("category", response.data["error"]["details"])
        self.assertFalse(RestaurantMenuItem.objects.exists())

    def test_create_with_photo(self):
        response = self.client.post(self.url, {**self.payload, "photo": png_file()}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["photo"].startswith("http"))
        bad = SimpleUploadedFile("a.txt", b"x", content_type="text/plain")
        response = self.client.post(self.url, {**self.payload, "photo": bad}, format="multipart")
        self.assertEqual(response.status_code, 400, response.data)

    def test_patch_and_delete(self):
        item = make_dish(self.restaurant)
        url = f"{self.url}{item.id}/"
        response = self.client.patch(url, {"price": "50000", "is_available": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["price"], "50000.00")
        self.assertFalse(response.data["is_available"])
        self.assertEqual(self.client.patch(url, {"price": "-5"}, format="json").status_code, 400)

        response = self.client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(RestaurantMenuItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_other_owners_item_404(self):
        _, other = make_business("restaurant", "Boshqa", admin=self.admin)
        item = make_dish(other)
        url = f"{self.url}{item.id}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"price": "1"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        item.refresh_from_db()
        self.assertEqual(str(item.price), "45000.00")

    def test_expired_subscription_can_read_but_not_write(self):
        item = make_dish(self.restaurant)
        Subscription.objects.filter(business=self.restaurant).update(status="expired")

        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(f"{self.url}{item.id}/").status_code, 200)

        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("tugagan", response.data["error"]["message"])
        self.assertEqual(self.client.patch(f"{self.url}{item.id}/", {"price": "1"}, format="json").status_code, 403)
        self.assertEqual(self.client.delete(f"{self.url}{item.id}/").status_code, 403)
        self.assertTrue(RestaurantMenuItem.objects.filter(pk=item.pk).exists())

    def test_approved_owner_without_subscription_gets_403(self):
        Subscription.objects.filter(business=self.restaurant).delete()
        response = self.client.post(self.url, self.payload, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("hali ochilmagan", response.data["error"]["message"])


# ----------------------------------------------------------------------------
# Egasi: to'yxona menyusi CRUD
# ----------------------------------------------------------------------------
class OwnerVenueMenuTests(TestCase):
    url = "/api/owner/menu/venue/"

    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.venue = make_business("venue", "Navro'z", admin=self.admin)
        self.client = client_for(self.owner)

    def test_restaurant_owner_gets_403(self):
        restaurant_owner, _ = make_business("restaurant", "Shoxona", admin=self.admin)
        response = client_for(restaurant_owner).post(self.url, {"name": "Osh"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("to'yxona egalari", response.data["error"]["message"])
        self.assertEqual(client_for().get(self.url).status_code, 401)
        self.assertEqual(client_for(make_user()).get(self.url).status_code, 403)

    def test_create_list_patch_delete(self):
        response = self.client.post(self.url, {"name": "Osh", "category": "main"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["business"], self.venue.id)
        self.assertNotIn("price", response.data)
        item_id = response.data["id"]

        self.assertEqual(self.client.post(self.url, {"name": "Sho'rva", "category": "soup"}, format="json").status_code, 201)
        self.assertEqual(self.client.post(self.url, {"category": "soup"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"name": "X", "category": "pizza"}, format="json").status_code, 400)

        response = self.client.get(self.url)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(self.client.get(self.url, {"category": "soup"}).data["count"], 1)

        response = self.client.patch(f"{self.url}{item_id}/", {"name": "Samarqand oshi"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Samarqand oshi")

        self.assertEqual(self.client.delete(f"{self.url}{item_id}/").status_code, 204)
        self.assertFalse(VenueMenuItem.objects.filter(pk=item_id).exists())

    def test_other_owners_item_404(self):
        _, other = make_business("venue", "Boshqa", admin=self.admin)
        item = make_venue_dish(other)
        url = f"{self.url}{item.id}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {"name": "X"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(VenueMenuItem.objects.filter(pk=item.pk).exists())

    def test_expired_subscription_can_read_but_not_write(self):
        item = make_venue_dish(self.venue)
        Subscription.objects.filter(business=self.venue).update(status="expired")
        self.assertEqual(self.client.get(self.url).status_code, 200)
        response = self.client.post(self.url, {"name": "Yangi"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertIn("tugagan", response.data["error"]["message"])
        self.assertEqual(self.client.delete(f"{self.url}{item.id}/").status_code, 403)


# ----------------------------------------------------------------------------
# Kesh va signallar
# ----------------------------------------------------------------------------
class MenuCacheInvalidationTests(TestCase):
    def setUp(self):
        cache.clear()
        call_command("seed_platform", verbosity=0)
        self.admin = make_admin()
        self.owner, self.restaurant = make_business("restaurant", "Shoxona", admin=self.admin)
        _, self.venue = make_business("venue", "Navro'z", admin=self.admin)
        self.client = client_for(self.owner)
        self.anon = client_for()

    def detail_menu(self, business):
        response = self.anon.get(f"/api/businesses/{business.id}/")
        self.assertEqual(response.status_code, 200, response.data)
        return [m["name"] for m in response.data["menu"]]

    def test_restaurant_detail_refreshes_after_owner_changes(self):
        self.assertEqual(self.detail_menu(self.restaurant), [])

        response = self.client.post("/api/owner/menu/restaurant/", {"name": "Osh", "price": "45000"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.detail_menu(self.restaurant), ["Osh"])

        url = f"/api/owner/menu/restaurant/{response.data['id']}/"
        self.client.patch(url, {"is_available": False}, format="json")
        self.assertEqual(self.detail_menu(self.restaurant), [])

        self.client.patch(url, {"is_available": True, "name": "Palov"}, format="json")
        self.assertEqual(self.detail_menu(self.restaurant), ["Palov"])

        self.client.delete(url)
        self.assertEqual(self.detail_menu(self.restaurant), [])

    def test_venue_detail_refreshes_on_orm_changes(self):
        self.assertEqual(self.detail_menu(self.venue), [])
        item = make_venue_dish(self.venue, "Osh")
        self.assertEqual(self.detail_menu(self.venue), ["Osh"])
        item.delete()
        self.assertEqual(self.detail_menu(self.venue), [])

    def test_signal_bumps_business_cache_version(self):
        version = get_business_version()
        item = make_dish(self.restaurant)
        self.assertEqual(get_business_version(), version + 1)

        item.name = "Palov"
        item.save()
        self.assertEqual(get_business_version(), version + 2)

        item.delete()
        self.assertEqual(get_business_version(), version + 3)

        venue_item = make_venue_dish(self.venue)
        self.assertEqual(get_business_version(), version + 4)
        venue_item.delete()
        self.assertEqual(get_business_version(), version + 5)

    def test_public_menu_list_not_cached(self):
        # ro'yxat endpointi keshsiz — o'zgarish darhol ko'rinadi
        url = f"/api/businesses/{self.restaurant.id}/menu/"
        self.assertEqual(self.anon.get(url).data["count"], 0)
        make_dish(self.restaurant)
        self.assertEqual(self.anon.get(url).data["count"], 1)


class MenuModelTests(TestCase):
    def setUp(self):
        call_command("seed_platform", verbosity=0)
        admin = make_admin()
        _, self.restaurant = make_business("restaurant", "Shoxona", admin=admin)
        _, self.venue = make_business("venue", "Navro'z", admin=admin)

    def test_restaurant_item_rejects_venue_business(self):
        with self.assertRaises(ValidationError) as ctx:
            RestaurantMenuItem(business=self.venue, name="Osh", price="1").full_clean()
        self.assertIn("business", ctx.exception.message_dict)
        RestaurantMenuItem(business=self.restaurant, name="Osh", price="1").full_clean()

    def test_venue_item_rejects_restaurant_business(self):
        with self.assertRaises(ValidationError) as ctx:
            VenueMenuItem(business=self.restaurant, name="Osh").full_clean()
        self.assertIn("business", ctx.exception.message_dict)
        VenueMenuItem(business=self.venue, name="Osh").full_clean()

    def test_negative_price_rejected_by_model(self):
        with self.assertRaises(ValidationError):
            RestaurantMenuItem(business=self.restaurant, name="Osh", price="-1").full_clean()

    def test_default_category_and_ordering(self):
        b = make_dish(self.restaurant, "B taom", category="main")
        a = make_dish(self.restaurant, "A taom", category="main")
        drink = make_dish(self.restaurant, "Choy", category="drink")
        self.assertEqual(make_dish(self.restaurant, "Default").category, "main")
        self.assertEqual(
            list(RestaurantMenuItem.objects.filter(business=self.restaurant).values_list("name", flat=True)),
            ["Choy", "A taom", "B taom", "Default"],
        )
        self.assertEqual(drink.get_category_display(), "Ichimlik")
        self.assertEqual(str(a), "A taom")
        self.assertEqual(str(b), "B taom")
