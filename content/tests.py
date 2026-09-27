import io
import shutil
import tempfile
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from content.models import Banner, News
from content.routes.serializers import resolve_language

User = get_user_model()

PASSWORD = "StrongPass123!"


def make_user(username, **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, full_name=username.title(),
        phone_number="+998901234567", **extra,
    )


def make_admin(username="boss"):
    return make_user(username, is_staff=True, is_superuser=True)


def auth(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def png_upload(name="a.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def make_banner(**kwargs):
    defaults = {"title_uz": "Salom", "placement": Banner.PLACEMENT_HERO}
    defaults.update(kwargs)
    return Banner.objects.create(**defaults)


def make_news(**kwargs):
    defaults = {"title_uz": "Yangilik"}
    defaults.update(kwargs)
    return News.objects.create(**defaults)


class ContentTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.admin_client = auth(self.admin)
        self.user = make_user("oddiy")
        self.user_client = auth(self.user)
        self.anon = APIClient()


# ---------------------------------------------------------------------------
# Modellar: live(), clean(), media_src, tr()
# ---------------------------------------------------------------------------
class ContentModelTests(TestCase):
    def test_live_filters_inactive_and_out_of_window(self):
        now = timezone.now()
        active = make_banner(title_uz="faol")
        make_banner(title_uz="nofaol", is_active=False)
        make_banner(title_uz="kelajak", starts_at=now + timedelta(days=1))
        make_banner(title_uz="tugagan", ends_at=now - timedelta(days=1))
        in_window = make_banner(
            title_uz="oynada", starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=1),
        )
        self.assertEqual(set(Banner.objects.live()), {active, in_window})

    def test_news_live_same_rules(self):
        now = timezone.now()
        live = make_news()
        make_news(is_active=False)
        make_news(starts_at=now + timedelta(hours=1))
        make_news(ends_at=now - timedelta(hours=1))
        self.assertEqual(list(News.objects.live()), [live])

    def test_clean_image_requires_image(self):
        banner = Banner(title_uz="x", media_type=Banner.MEDIA_IMAGE)
        with self.assertRaises(ValidationError) as ctx:
            banner.clean()
        self.assertIn("image", ctx.exception.message_dict)

    def test_clean_video_requires_file_or_url(self):
        banner = Banner(title_uz="x", media_type=Banner.MEDIA_VIDEO)
        with self.assertRaises(ValidationError) as ctx:
            banner.clean()
        self.assertIn("video", ctx.exception.message_dict)
        banner.video_url = "https://example.com/v.mp4"
        banner.clean()  # xato bo'lmasligi kerak

    def test_clean_ends_before_starts(self):
        now = timezone.now()
        banner = Banner(title_uz="x", starts_at=now, ends_at=now - timedelta(minutes=1))
        with self.assertRaises(ValidationError) as ctx:
            banner.clean()
        self.assertIn("ends_at", ctx.exception.message_dict)

    def test_media_src(self):
        self.assertIsNone(Banner(title_uz="x").media_src)
        video = Banner(title_uz="x", media_type=Banner.MEDIA_VIDEO, video_url="https://e.com/v.mp4")
        self.assertEqual(video.media_src, "https://e.com/v.mp4")
        # rasm turi tanlangan, lekin rasm yo'q — None
        self.assertIsNone(Banner(title_uz="x", media_type=Banner.MEDIA_IMAGE).media_src)

    def test_tr_fallback_to_uz(self):
        banner = Banner(title_uz="Salom", title_ru="Привет", title_en="   ")
        self.assertEqual(banner.tr("title", "ru"), "Привет")
        self.assertEqual(banner.tr("title", "en"), "Salom")
        self.assertEqual(banner.tr("title", "uz"), "Salom")
        self.assertEqual(banner.tr("subtitle", "ru"), "")


class ResolveLanguageTests(TestCase):
    def _request(self, lang=None, header=None):
        from rest_framework.test import APIRequestFactory
        from rest_framework.request import Request

        factory = APIRequestFactory()
        params = {"lang": lang} if lang else {}
        extra = {"HTTP_ACCEPT_LANGUAGE": header} if header else {}
        return Request(factory.get("/", params, **extra))

    def test_query_param_wins(self):
        self.assertEqual(resolve_language(self._request(lang="RU", header="en-US")), "ru")

    def test_accept_language_header(self):
        self.assertEqual(resolve_language(self._request(header="en-US,en;q=0.9")), "en")
        self.assertEqual(resolve_language(self._request(header="ru")), "ru")

    def test_fallback_uz(self):
        self.assertEqual(resolve_language(None), "uz")
        self.assertEqual(resolve_language(self._request(lang="fr")), "uz")
        self.assertEqual(resolve_language(self._request(header="de-DE")), "uz")


# ---------------------------------------------------------------------------
# Ommaviy bannerlar
# ---------------------------------------------------------------------------
class PublicBannerTests(ContentTestCase):
    URL = "/api/banners/"

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.hero = make_banner(title_uz="Bosh", title_ru="Главный", order=2)
        self.hero_first = make_banner(title_uz="Birinchi", order=1)
        self.sidebar = make_banner(title_uz="Yon", placement=Banner.PLACEMENT_SIDEBAR)
        make_banner(title_uz="Nofaol", is_active=False)
        make_banner(title_uz="Kelajak", starts_at=now + timedelta(days=1))
        make_banner(title_uz="Tugagan", ends_at=now - timedelta(days=1))

    def test_only_live_banners_ordered(self):
        response = self.anon.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        titles = [b["title"] for b in response.data]
        self.assertEqual(titles, ["Yon", "Birinchi", "Bosh"])  # order: 0, 1, 2
        self.assertNotIn("title_uz", response.data[0])
        self.assertIn("media_src", response.data[0])

    def test_placement_filter(self):
        response = self.anon.get(self.URL, {"placement": "sidebar"})
        self.assertEqual([b["title"] for b in response.data], ["Yon"])
        response = self.anon.get(self.URL, {"placement": "auth"})
        self.assertEqual(response.data, [])

    def test_language_query_and_header_and_fallback(self):
        response = self.anon.get(self.URL, {"lang": "ru", "placement": "hero"})
        self.assertEqual([b["title"] for b in response.data], ["Birinchi", "Главный"])

        response = self.anon.get(self.URL, {"placement": "hero"}, HTTP_ACCEPT_LANGUAGE="ru-RU,ru;q=0.9")
        self.assertEqual(response.data[1]["title"], "Главный")

        response = self.anon.get(self.URL, {"lang": "en", "placement": "hero"})
        self.assertEqual(response.data[1]["title"], "Bosh")  # en bo'sh — uz ga qaytadi

    def test_video_url_media_src(self):
        make_banner(
            title_uz="Video", media_type=Banner.MEDIA_VIDEO, video_url="https://cdn.example.com/v.mp4",
            placement=Banner.PLACEMENT_INLINE,
        )
        response = self.anon.get(self.URL, {"placement": "inline"})
        self.assertEqual(response.data[0]["media_type"], "video")
        self.assertEqual(response.data[0]["media_src"], "https://cdn.example.com/v.mp4")


# ---------------------------------------------------------------------------
# Admin: bannerlar CRUD
# ---------------------------------------------------------------------------
class AdminBannerTests(ContentTestCase):
    URL = "/api/admin/banners/"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def test_create_text_banner_201(self):
        payload = {
            "title_uz": "Aksiya", "subtitle_uz": "50% chegirma", "cta_label_uz": "Ko'rish",
            "cta_url": "/aksiya/", "placement": "inline", "accent_color": "#C9A227",
        }
        response = self.admin_client.post(self.URL, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["media_type"], "none")
        self.assertIn("title_ru", response.data)  # admin serializer barcha maydonlarni qaytaradi
        self.assertTrue(Banner.objects.filter(title_uz="Aksiya").exists())

    def test_create_image_type_without_image_400(self):
        response = self.admin_client.post(self.URL, {"title_uz": "x", "media_type": "image"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(response.data["success"])
        self.assertIn("image", response.data["error"]["details"])

    def test_create_video_type_without_source_400_and_with_url_201(self):
        response = self.admin_client.post(self.URL, {"title_uz": "x", "media_type": "video"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("video", response.data["error"]["details"])

        response = self.admin_client.post(
            self.URL, {"title_uz": "x", "media_type": "video", "video_url": "https://e.com/v.mp4"}, format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_create_ends_before_starts_400(self):
        now = timezone.now()
        payload = {
            "title_uz": "x", "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
        }
        response = self.admin_client.post(self.URL, payload, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("ends_at", response.data["error"]["details"])

    def test_create_missing_title_400(self):
        response = self.admin_client.post(self.URL, {"placement": "hero"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("title_uz", response.data["error"]["details"])

    def test_create_with_image_upload_201(self):
        # Eslatma: multipart'da is_active yuborilmasa DRF uni False deb oladi (default_empty_html),
        # shuning uchun aniq yuboramiz.
        payload = {"title_uz": "Rasmli", "media_type": "image", "image": png_upload(), "is_active": "true"}
        response = self.admin_client.post(self.URL, payload, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        banner = Banner.objects.get(pk=response.data["id"])
        self.assertTrue(banner.image.name.startswith("banners/"))

        public = self.anon.get("/api/banners/")
        self.assertEqual(public.status_code, 200)
        self.assertTrue(public.data[0]["media_src"].startswith("http://testserver/"))
        self.assertIn(banner.image.url, public.data[0]["media_src"])

    def test_multipart_without_is_active_stays_active(self):
        # form-data'da yuborilmagan boolean model defaultini saqlaydi (True)
        response = self.admin_client.post(self.URL, {"title_uz": "Formadan"}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_active"])

    def test_create_image_wrong_extension_400(self):
        upload = SimpleUploadedFile("a.gif", b"GIF89a", content_type="image/gif")
        response = self.admin_client.post(
            self.URL, {"title_uz": "x", "media_type": "image", "image": upload}, format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("image", response.data["error"]["details"])

    def test_regular_user_403_and_anon_401(self):
        self.assertEqual(self.user_client.get(self.URL).status_code, 403)
        response = self.user_client.post(self.URL, {"title_uz": "x"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertEqual(self.anon.post(self.URL, {"title_uz": "x"}, format="json").status_code, 401)

    def test_admin_list_includes_inactive_and_filters(self):
        make_banner(title_uz="faol")
        make_banner(title_uz="nofaol", is_active=False, placement=Banner.PLACEMENT_AUTH)
        response = self.admin_client.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertIn("results", response.data)

        response = self.admin_client.get(self.URL, {"is_active": "false"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title_uz"], "nofaol")

        response = self.admin_client.get(self.URL, {"placement": "auth"})
        self.assertEqual(response.data["count"], 1)

    def test_detail_patch_delete(self):
        banner = make_banner(title_uz="Eski")
        url = f"{self.URL}{banner.pk}/"

        response = self.admin_client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["title_uz"], "Eski")

        response = self.admin_client.patch(url, {"title_uz": "Yangi", "is_active": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        banner.refresh_from_db()
        self.assertEqual(banner.title_uz, "Yangi")
        self.assertFalse(banner.is_active)
        self.assertEqual(self.anon.get("/api/banners/").data, [])

        response = self.admin_client.delete(url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Banner.objects.filter(pk=banner.pk).exists())

    def test_patch_media_type_image_without_image_400(self):
        banner = make_banner(title_uz="Matn")
        response = self.admin_client.patch(f"{self.URL}{banner.pk}/", {"media_type": "image"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("image", response.data["error"]["details"])

    def test_detail_404(self):
        response = self.admin_client.get(f"{self.URL}{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(self.admin_client.delete(f"{self.URL}{uuid.uuid4()}/").status_code, 404)


# ---------------------------------------------------------------------------
# Yangiliklar: ommaviy
# ---------------------------------------------------------------------------
class PublicNewsTests(ContentTestCase):
    URL = "/api/news/"

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.old = make_news(title_uz="Eski", title_ru="Старая", category=News.CATEGORY_TIP)
        self.new = make_news(title_uz="Yangi", category=News.CATEGORY_EVENT)
        self.pinned = make_news(title_uz="Muhim", is_pinned=True, category=News.CATEGORY_UPDATE)
        self.inactive = make_news(title_uz="Nofaol", is_active=False)
        self.expired = make_news(title_uz="Tugagan", ends_at=now - timedelta(days=1))
        self.future = make_news(title_uz="Kelajak", starts_at=now + timedelta(days=1))

    def test_list_live_pinned_first(self):
        response = self.anon.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)
        titles = [n["title"] for n in response.data["results"]]
        self.assertEqual(titles, ["Muhim", "Yangi", "Eski"])
        self.assertTrue(response.data["results"][0]["is_pinned"])
        self.assertEqual(response.data["results"][0]["category_display"], "Platforma yangilanishi")

    def test_category_filter(self):
        response = self.anon.get(self.URL, {"category": "tip"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Eski")

    def test_language(self):
        response = self.anon.get(self.URL, {"lang": "ru", "category": "tip"})
        self.assertEqual(response.data["results"][0]["title"], "Старая")
        response = self.anon.get(self.URL, {"category": "tip"}, HTTP_ACCEPT_LANGUAGE="en")
        self.assertEqual(response.data["results"][0]["title"], "Eski")

    def test_detail_live_200(self):
        response = self.anon.get(f"{self.URL}{self.pinned.pk}/", {"lang": "uz"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["title"], "Muhim")
        self.assertEqual(response.data["id"], str(self.pinned.pk))

    def test_detail_404_for_inactive_expired_future_or_missing(self):
        for item in (self.inactive, self.expired, self.future):
            response = self.anon.get(f"{self.URL}{item.pk}/")
            self.assertEqual(response.status_code, 404, response.data)
            self.assertEqual(response.data["error"]["code"], "not_found")
        self.assertEqual(self.anon.get(f"{self.URL}{uuid.uuid4()}/").status_code, 404)

    def test_pagination_page_size(self):
        response = self.anon.get(self.URL, {"page_size": 2})
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertIsNotNone(response.data["next"])


# ---------------------------------------------------------------------------
# Yangiliklar: admin CRUD
# ---------------------------------------------------------------------------
class AdminNewsTests(ContentTestCase):
    URL = "/api/admin/news/"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls._media_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def test_create_201(self):
        payload = {
            "title_uz": "Yangi funksiya", "excerpt_uz": "Qisqacha", "body_uz": "Batafsil",
            "category": "update", "is_pinned": True, "link_url": "/yangiliklar/1/",
        }
        response = self.admin_client.post(self.URL, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["is_pinned"])
        self.assertEqual(News.objects.count(), 1)

    def test_create_with_cover_multipart(self):
        response = self.admin_client.post(
            self.URL, {"title_uz": "Rasmli", "cover": png_upload("c.png"), "is_active": "true"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(News.objects.get(pk=response.data["id"]).cover.name.startswith("news/"))
        public = self.anon.get(f"/api/news/{response.data['id']}/")
        self.assertIsNotNone(public.data["cover"])

    def test_create_invalid_category_and_missing_title_400(self):
        response = self.admin_client.post(self.URL, {"title_uz": "x", "category": "spam"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("category", response.data["error"]["details"])
        response = self.admin_client.post(self.URL, {"body_uz": "x"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("title_uz", response.data["error"]["details"])

    def test_regular_user_403_anon_401(self):
        self.assertEqual(self.user_client.get(self.URL).status_code, 403)
        self.assertEqual(self.user_client.post(self.URL, {"title_uz": "x"}, format="json").status_code, 403)
        self.assertEqual(self.anon.get(self.URL).status_code, 401)

    def test_admin_list_includes_inactive_and_filters(self):
        make_news(title_uz="faol", category="tip")
        make_news(title_uz="nofaol", is_active=False, is_pinned=True)
        response = self.admin_client.get(self.URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["title_uz"], "nofaol")  # pinned birinchi

        self.assertEqual(self.admin_client.get(self.URL, {"is_active": "false"}).data["count"], 1)
        self.assertEqual(self.admin_client.get(self.URL, {"is_pinned": "true"}).data["count"], 1)
        self.assertEqual(self.admin_client.get(self.URL, {"category": "tip"}).data["count"], 1)

    def test_detail_patch_delete(self):
        item = make_news(title_uz="Eski")
        url = f"{self.URL}{item.pk}/"
        self.assertEqual(self.admin_client.get(url).data["title_uz"], "Eski")

        response = self.admin_client.patch(url, {"title_uz": "Yangi", "is_active": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertEqual(item.title_uz, "Yangi")
        self.assertEqual(self.anon.get(f"/api/news/{item.pk}/").status_code, 404)

        self.assertEqual(self.admin_client.delete(url).status_code, 204)
        self.assertFalse(News.objects.filter(pk=item.pk).exists())
        self.assertEqual(self.admin_client.get(url).status_code, 404)
