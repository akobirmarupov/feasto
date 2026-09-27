import io
import shutil
import tempfile
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from account.models import User
from account.routes.helpers import BLOCKED_MESSAGE
from account.services import GoogleAuthError, get_or_create_google_user, username_from_email
from account.trust import TRUST_MAX, TRUST_MIN, TRUST_START, clamp_bits, describe, level_for

PASSWORD = "StrongPass123!"

LOGIN_URL = "/api/auth/login/"
REFRESH_URL = "/api/auth/refresh/"
LOGOUT_URL = "/api/auth/logout/"
ME_URL = "/api/auth/me/"
AVATAR_URL = "/api/auth/me/avatar/"
GOOGLE_URL = "/api/auth/google/"
GOOGLE_START_URL = "/api/auth/google/start/"
GOOGLE_CALLBACK_URL = "/api/auth/google/callback/"
ADMIN_USERS_URL = "/api/admin/users/"


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


def login(client, username, password=PASSWORD):
    return client.post(LOGIN_URL, {"username": username, "password": password}, format="json")


def png_file(name="a.png"):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def google_payload(sub="sub-1", email="yangi@gmail.com", name="Yangi Odam"):
    return {"sub": sub, "email": email, "name": name, "iss": "accounts.google.com"}


# ---------------------------------------------------------------- login

class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = APIClient()

    def test_login_success_returns_tokens_and_user(self):
        response = login(self.client, "ali")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        user = response.data["user"]
        self.assertEqual(user["username"], "ali")
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["trust"]["bits"], TRUST_START)
        self.assertIsNone(user["business"])

    def test_login_wrong_password_401(self):
        response = login(self.client, "ali", "notThePassword1")
        self.assertEqual(response.status_code, 401, response.data)
        self.assertFalse(response.data["success"])
        self.assertIn("request_id", response.data)

    def test_login_blocked_account_401(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = login(self.client, "ali")
        self.assertEqual(response.status_code, 401, response.data)

    def test_login_missing_password_400(self):
        response = self.client.post(LOGIN_URL, {"username": "ali"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertIn("password", response.data["error"]["details"])

    def test_login_throttle_after_ten_attempts_429(self):
        for _ in range(10):
            response = login(self.client, "ali", "wrong-password-1")
            self.assertEqual(response.status_code, 401, response.data)
        response = login(self.client, "ali")
        self.assertEqual(response.status_code, 429, response.data)
        self.assertEqual(response.data["error"]["code"], "too_many_requests")

    def test_login_throttle_is_per_username(self):
        make_user(username="vali", full_name="Vali")
        for _ in range(10):
            login(self.client, "ali", "wrong-password-1")
        response = login(self.client, "vali")
        self.assertEqual(response.status_code, 200, response.data)


# ---------------------------------------------------------------- refresh / logout

class RefreshTokenTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = APIClient()
        self.tokens = login(self.client, "ali").data

    def test_refresh_rotates_and_blacklists_old_token(self):
        response = self.client.post(REFRESH_URL, {"refresh": self.tokens["refresh"]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("access", response.data)
        self.assertNotEqual(response.data["refresh"], self.tokens["refresh"])

        # eski refresh qora ro'yxatda
        again = self.client.post(REFRESH_URL, {"refresh": self.tokens["refresh"]}, format="json")
        self.assertEqual(again.status_code, 401, again.data)
        self.assertTrue(BlacklistedToken.objects.filter(token__user=self.user).exists())

        # yangi refresh ishlaydi
        new = self.client.post(REFRESH_URL, {"refresh": response.data["refresh"]}, format="json")
        self.assertEqual(new.status_code, 200, new.data)

    def test_refresh_with_garbage_401(self):
        response = self.client.post(REFRESH_URL, {"refresh": "abc.def.ghi"}, format="json")
        self.assertEqual(response.status_code, 401, response.data)


class LogoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.other = make_user(username="vali", full_name="Vali", phone="+998907777777")
        self.client = auth_client(self.user)
        self.refresh = str(RefreshToken.for_user(self.user))

    def test_logout_anonymous_401(self):
        response = APIClient().post(LOGOUT_URL, {"refresh": self.refresh}, format="json")
        self.assertEqual(response.status_code, 401, response.data)
        self.assertEqual(response.data["error"]["code"], "unauthenticated")

    def test_logout_own_token_205(self):
        response = self.client.post(LOGOUT_URL, {"refresh": self.refresh}, format="json")
        self.assertEqual(response.status_code, 205, response.data)
        self.assertTrue(BlacklistedToken.objects.filter(token__user=self.user).exists())
        # bekor qilingan refresh bilan yangilab bo'lmaydi
        again = APIClient().post(REFRESH_URL, {"refresh": self.refresh}, format="json")
        self.assertEqual(again.status_code, 401, again.data)

    def test_logout_twice_400(self):
        self.client.post(LOGOUT_URL, {"refresh": self.refresh}, format="json")
        response = self.client.post(LOGOUT_URL, {"refresh": self.refresh}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")

    def test_logout_foreign_token_400(self):
        foreign = str(RefreshToken.for_user(self.other))
        response = self.client.post(LOGOUT_URL, {"refresh": foreign}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(BlacklistedToken.objects.filter(token__user=self.other).exists())

    def test_logout_missing_refresh_400(self):
        response = self.client.post(LOGOUT_URL, {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("refresh", response.data["error"]["details"])


# ---------------------------------------------------------------- /me/

class MeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = auth_client(self.user)

    def test_me_anonymous_401(self):
        response = APIClient().get(ME_URL)
        self.assertEqual(response.status_code, 401, response.data)

    def test_me_get_fields(self):
        response = self.client.get(ME_URL)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        self.assertEqual(data["username"], "ali")
        self.assertEqual(data["initials"], "AV")
        self.assertEqual(data["trust"]["level"], "excellent")
        self.assertEqual(data["trust_bits"], TRUST_START)
        self.assertEqual(data["stats"], {"total": 0, "completed": 0, "upcoming": 0, "cancelled": 0, "reviews": 0})
        self.assertIsNone(data["business"])

    def test_me_patch_full_name_and_bio(self):
        response = self.client.patch(ME_URL, {"full_name": "  Ali   Valiyev ", "bio": "Salom"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["full_name"], "Ali Valiyev")
        self.assertEqual(response.data["bio"], "Salom")

    def test_me_patch_empty_full_name_400(self):
        response = self.client.patch(ME_URL, {"full_name": "   "}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("full_name", response.data["error"]["details"])

    def test_me_patch_phone_bad_format_400(self):
        response = self.client.patch(ME_URL, {"phone_number": "901234567"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("phone_number", response.data["error"]["details"])

    def test_me_patch_phone_sets_verified_flag(self):
        user = make_user(username="nophone", full_name="No Phone", phone=None)
        client = auth_client(user)
        response = client.patch(ME_URL, {"phone_number": "+998909999999"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_phone_verified"])
        user.refresh_from_db()
        self.assertEqual(user.phone_number, "+998909999999")

    def test_me_patch_remove_phone_with_empty_string(self):
        response = self.client.patch(ME_URL, {"phone_number": ""}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["phone_number"])
        self.assertFalse(response.data["is_phone_verified"])
        self.user.refresh_from_db()
        self.assertIsNone(self.user.phone_number)

    def test_me_patch_remove_phone_with_null(self):
        response = self.client.patch(ME_URL, {"phone_number": None}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["phone_number"])

    def test_me_patch_read_only_fields_ignored(self):
        response = self.client.patch(
            ME_URL,
            {"username": "hacker", "role": "admin", "is_staff": True, "trust_bits": 1,
             "email": "x@y.z", "has_used_trial": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "ali")
        self.assertEqual(self.user.role, "user")
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.user.trust_bits, TRUST_START)
        self.assertEqual(self.user.email, "")
        self.assertFalse(self.user.has_used_trial)

    def test_me_patch_invalid_language_400(self):
        response = self.client.patch(ME_URL, {"preferred_language": "fr"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)


class DeleteMeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user(email="ali@example.com", bio="salom")
        self.tokens = login(APIClient(), "ali").data
        self.client = APIClient(HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}")

    def test_delete_me_anonymizes_and_revokes_tokens(self):
        response = self.client.delete(ME_URL)
        self.assertEqual(response.status_code, 204)

        user = User.objects.get(pk=self.user.pk)
        self.assertFalse(user.is_active)
        self.assertEqual(user.username, f"deleted_{user.pk}")
        self.assertEqual(user.full_name, "O'chirilgan foydalanuvchi")
        self.assertEqual(user.email, "")
        self.assertIsNone(user.phone_number)
        self.assertFalse(user.is_phone_verified)
        self.assertEqual(user.bio, "")
        self.assertFalse(user.has_usable_password())

        # refresh qora ro'yxatda, access bilan ham kirib bo'lmaydi
        refreshed = APIClient().post(REFRESH_URL, {"refresh": self.tokens["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 401, refreshed.data)
        me = self.client.get(ME_URL)
        self.assertEqual(me.status_code, 401, me.data)
        self.assertTrue(
            BlacklistedToken.objects.filter(token__user=self.user).count()
            >= OutstandingToken.objects.filter(user=self.user).count()
        )

    def test_delete_me_then_login_fails(self):
        self.client.delete(ME_URL)
        response = login(APIClient(), "ali")
        self.assertEqual(response.status_code, 401, response.data)


# ---------------------------------------------------------------- avatar

class AvatarTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.override = override_settings(MEDIA_ROOT=cls.media_root)
        cls.override.enable()

    @classmethod
    def tearDownClass(cls):
        cls.override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = auth_client(self.user)

    def test_avatar_anonymous_401(self):
        response = APIClient().post(AVATAR_URL, {"avatar": png_file()}, format="multipart")
        self.assertEqual(response.status_code, 401, response.data)

    def test_avatar_upload_and_delete(self):
        response = self.client.post(AVATAR_URL, {"avatar": png_file()}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["avatar"].startswith("http"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.avatar)
        storage, name = self.user.avatar.storage, self.user.avatar.name
        self.assertTrue(storage.exists(name))

        response = self.client.delete(AVATAR_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["avatar"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.avatar)
        self.assertFalse(storage.exists(name))

    def test_avatar_reupload_removes_old_file(self):
        self.client.post(AVATAR_URL, {"avatar": png_file("first.png")}, format="multipart")
        self.user.refresh_from_db()
        old_name = self.user.avatar.name
        storage = self.user.avatar.storage

        response = self.client.post(AVATAR_URL, {"avatar": png_file("second.png")}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertNotEqual(self.user.avatar.name, old_name)
        self.assertFalse(storage.exists(old_name))

    def test_avatar_missing_file_400(self):
        response = self.client.post(AVATAR_URL, {}, format="multipart")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("avatar", response.data["error"]["details"])

    def test_avatar_bad_extension_400(self):
        response = self.client.post(AVATAR_URL, {"avatar": png_file("a.gif")}, format="multipart")
        self.assertEqual(response.status_code, 400, response.data)

    def test_avatar_delete_when_empty_is_noop(self):
        response = self.client.delete(AVATAR_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["avatar"])


# ---------------------------------------------------------------- Google POST

@patch("account.routes.google_api.verify_google_token")
class GoogleAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def post(self, credential="tok"):
        return self.client.post(GOOGLE_URL, {"credential": credential}, format="json")

    def test_google_creates_new_user(self, verify):
        verify.return_value = google_payload(email="Yangi.Odam@Gmail.com")
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["created"])
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        verify.assert_called_once_with("tok")

        user = User.objects.get(google_sub="sub-1")
        self.assertEqual(user.email, "yangi.odam@gmail.com")
        self.assertEqual(user.username, "yangi_odam")
        self.assertEqual(user.full_name, "Yangi Odam")
        self.assertTrue(user.is_confirmed)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(response.data["user"]["username"], "yangi_odam")

    def test_google_links_existing_user_by_email(self, verify):
        existing = make_user(email="ali@example.com")
        verify.return_value = google_payload(sub="sub-ali", email="ALI@example.com")
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["created"])
        existing.refresh_from_db()
        self.assertEqual(existing.google_sub, "sub-ali")
        self.assertTrue(existing.is_confirmed)
        self.assertEqual(User.objects.count(), 1)

    def test_google_finds_user_by_sub_even_if_email_changed(self, verify):
        existing = make_user(email="old@example.com", google_sub="sub-ali")
        verify.return_value = google_payload(sub="sub-ali", email="new@example.com")
        response = self.post()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["created"])
        self.assertEqual(response.data["user"]["id"], str(existing.id))
        existing.refresh_from_db()
        self.assertEqual(existing.email, "old@example.com")
        self.assertEqual(User.objects.count(), 1)

    def test_google_blocked_user_403(self, verify):
        make_user(email="ali@example.com", google_sub="sub-ali", is_active=False)
        verify.return_value = google_payload(sub="sub-ali", email="ali@example.com")
        response = self.post()
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")
        self.assertEqual(response.data["error"]["message"], BLOCKED_MESSAGE)

    def test_google_bad_token_400(self, verify):
        verify.side_effect = GoogleAuthError("Google tasdig'i qabul qilinmadi.")
        response = self.post("bad")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "bad_request")
        self.assertEqual(response.data["error"]["message"], "Google tasdig'i qabul qilinmadi.")
        self.assertEqual(User.objects.count(), 0)

    def test_google_missing_credential_400(self, verify):
        response = self.client.post(GOOGLE_URL, {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("credential", response.data["error"]["details"])
        verify.assert_not_called()


# ---------------------------------------------------------------- Google redirect oqimi

@override_settings(
    GOOGLE_CLIENT_ID="x.apps.googleusercontent.com",
    GOOGLE_CLIENT_SECRET="secret",
    FRONTEND_LOGIN_URL="/kirish/",
)
class GoogleRedirectFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def start(self, next_url=None):
        params = {"next": next_url} if next_url is not None else {}
        response = self.client.get(GOOGLE_START_URL, params)
        self.assertEqual(response.status_code, 302, getattr(response, "data", None))
        query = parse_qs(urlsplit(response["Location"]).query)
        return response, query

    def test_start_redirects_to_google_with_state(self):
        response, query = self.start(next_url="/profil/")
        self.assertTrue(response["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertEqual(query["client_id"], ["x.apps.googleusercontent.com"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertTrue(query["redirect_uri"][0].endswith(GOOGLE_CALLBACK_URL))
        state = query["state"][0]
        self.assertEqual(self.client.session["google_state"], state)
        self.assertEqual(self.client.session["google_next"], "/profil/")

    def test_start_rejects_external_next(self):
        self.start(next_url="https://evil.example/steal")
        self.assertEqual(self.client.session["google_next"], "/")
        self.start(next_url="//evil.example")
        self.assertEqual(self.client.session["google_next"], "/")

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_start_without_client_id_400(self):
        response = self.client.get(GOOGLE_START_URL)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("GOOGLE_CLIENT_ID", response.data["error"]["message"])

    def test_callback_cancelled(self):
        response = self.client.get(GOOGLE_CALLBACK_URL, {"error": "access_denied"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/kirish/?google_error=cancelled")

    def test_callback_bad_state(self):
        self.start()
        response = self.client.get(GOOGLE_CALLBACK_URL, {"code": "abc", "state": "wrong"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/kirish/?google_error=state")
        # state bir martalik — haqiqiy state bilan ham endi o'tmaydi
        self.assertNotIn("google_state", self.client.session)

    def test_callback_without_code(self):
        _, query = self.start()
        response = self.client.get(GOOGLE_CALLBACK_URL, {"state": query["state"][0]})
        self.assertEqual(response["Location"], "/kirish/?google_error=nocode")

    @patch("account.routes.google_api.verify_google_token")
    @patch("account.routes.google_api.exchange_code")
    def test_callback_success_fragment(self, exchange, verify):
        exchange.return_value = "id-token"
        verify.return_value = google_payload()
        _, query = self.start(next_url="/profil/")

        response = self.client.get(GOOGLE_CALLBACK_URL, {"code": "abc", "state": query["state"][0]})
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith("/kirish/#"), location)
        fragment = parse_qs(urlsplit(location).fragment)
        self.assertEqual(fragment["created"], ["1"])
        self.assertEqual(fragment["next"], ["/profil/"])
        self.assertIn("access", fragment)
        exchange.assert_called_once()
        self.assertEqual(exchange.call_args.kwargs["code"], "abc")
        verify.assert_called_once_with("id-token")

        # berilgan refresh haqiqiy
        refreshed = APIClient().post(REFRESH_URL, {"refresh": fragment["refresh"][0]}, format="json")
        self.assertEqual(refreshed.status_code, 200, refreshed.data)
        self.assertTrue(User.objects.filter(google_sub="sub-1").exists())
        self.assertNotIn("google_state", self.client.session)
        self.assertNotIn("google_next", self.client.session)

    @patch("account.routes.google_api.verify_google_token")
    @patch("account.routes.google_api.exchange_code")
    def test_callback_existing_user_created_0(self, exchange, verify):
        make_user(email="ali@example.com")
        exchange.return_value = "id-token"
        verify.return_value = google_payload(sub="s", email="ali@example.com")
        _, query = self.start()
        response = self.client.get(GOOGLE_CALLBACK_URL, {"code": "abc", "state": query["state"][0]})
        fragment = parse_qs(urlsplit(response["Location"]).fragment)
        self.assertEqual(fragment["created"], ["0"])
        self.assertEqual(fragment["next"], ["/"])

    @patch("account.routes.google_api.verify_google_token")
    @patch("account.routes.google_api.exchange_code")
    def test_callback_blocked_user(self, exchange, verify):
        make_user(email="ali@example.com", google_sub="s", is_active=False)
        exchange.return_value = "id-token"
        verify.return_value = google_payload(sub="s", email="ali@example.com")
        _, query = self.start()
        response = self.client.get(GOOGLE_CALLBACK_URL, {"code": "abc", "state": query["state"][0]})
        self.assertEqual(response["Location"], "/kirish/?google_error=blocked")

    @patch("account.routes.google_api.exchange_code")
    def test_callback_google_error(self, exchange):
        exchange.side_effect = GoogleAuthError("bog'lanib bo'lmadi")
        _, query = self.start()
        response = self.client.get(GOOGLE_CALLBACK_URL, {"code": "abc", "state": query["state"][0]})
        self.assertEqual(response["Location"], "/kirish/?google_error=google")


# ---------------------------------------------------------------- services

class UsernameFromEmailTests(TestCase):
    def test_basic_normalization(self):
        self.assertEqual(username_from_email("Ali.Valiyev@gmail.com"), "ali_valiyev")

    def test_unicode_is_transliterated(self):
        self.assertEqual(username_from_email("Élodie-Ö@x.com"), "elodie_o")

    def test_too_short_gets_suffix(self):
        self.assertEqual(username_from_email("ab@x.com"), "ab_user")

    def test_only_symbols_becomes_user(self):
        self.assertEqual(username_from_email("___@x.com"), "user")
        self.assertEqual(username_from_email(""), "user")

    def test_collision_adds_number(self):
        make_user(username="ali_valiyev")
        self.assertEqual(username_from_email("ali.valiyev@x.com"), "ali_valiyev2")
        make_user(username="ali_valiyev2", phone=None)
        self.assertEqual(username_from_email("ali.valiyev@x.com"), "ali_valiyev3")

    def test_long_local_part_truncated_to_24(self):
        name = username_from_email("a" * 40 + "@x.com")
        self.assertEqual(name, "a" * 24)
        make_user(username=name)
        self.assertEqual(username_from_email("a" * 40 + "@x.com"), "a" * 23 + "2")

    def test_result_passes_username_validator(self):
        from account.validators import validate_username
        validate_username(username_from_email("John Doe+tag@mail.com"))


class GetOrCreateGoogleUserTests(TestCase):
    def test_creates_confirmed_user_without_password(self):
        user, created = get_or_create_google_user(google_payload(email="A@B.com", name="  Ism  "))
        self.assertTrue(created)
        self.assertEqual(user.email, "a@b.com")
        self.assertEqual(user.full_name, "Ism")
        self.assertTrue(user.is_confirmed)
        self.assertFalse(user.has_usable_password())

    def test_full_name_falls_back_to_email_local_part(self):
        user, _ = get_or_create_google_user(google_payload(email="someone@b.com", name=""))
        self.assertEqual(user.full_name, "someone")

    def test_links_by_email_and_fills_missing_fields(self):
        existing = make_user(email="ali@example.com", is_confirmed=False)
        user, created = get_or_create_google_user(google_payload(sub="s", email="ali@example.com"))
        self.assertFalse(created)
        self.assertEqual(user.pk, existing.pk)
        self.assertEqual(user.google_sub, "s")
        self.assertTrue(user.is_confirmed)

    def test_fills_empty_email_for_sub_match(self):
        existing = make_user(google_sub="s", email="")
        user, created = get_or_create_google_user(google_payload(sub="s", email="new@example.com"))
        self.assertFalse(created)
        self.assertEqual(user.pk, existing.pk)
        self.assertEqual(user.email, "new@example.com")


# ---------------------------------------------------------------- trust

class TrustTests(TestCase):
    def test_level_thresholds(self):
        self.assertEqual(level_for(100)[0], "excellent")
        self.assertEqual(level_for(80)[0], "excellent")
        self.assertEqual(level_for(79)[0], "good")
        self.assertEqual(level_for(65)[0], "good")
        self.assertEqual(level_for(64)[0], "fair")
        self.assertEqual(level_for(50)[0], "fair")
        self.assertEqual(level_for(49)[0], "poor")
        self.assertEqual(level_for(30)[0], "poor")
        self.assertEqual(level_for(29)[0], "bad")
        self.assertEqual(level_for(1), ("bad", "O'ta yomon", "danger"))

    def test_describe_shape(self):
        self.assertEqual(
            describe(72),
            {"bits": 72, "level": "good", "level_display": "Yaxshi", "tone": "info"},
        )

    def test_clamp_bits(self):
        self.assertEqual(clamp_bits(500), TRUST_MAX)
        self.assertEqual(clamp_bits(-5), TRUST_MIN)
        self.assertEqual(clamp_bits(0), TRUST_MIN)
        self.assertEqual(clamp_bits("abc"), TRUST_START)
        self.assertEqual(clamp_bits(None), TRUST_START)
        self.assertEqual(describe(999)["bits"], TRUST_MAX)

    def test_user_trust_property(self):
        user = make_user(trust_bits=40)
        self.assertEqual(user.trust["level"], "poor")
        self.assertEqual(user.trust["tone"], "warn")

    def test_penalize_trust_default_penalty(self):
        user = make_user()
        result = user.penalize_trust(reason="test")
        self.assertEqual(result, 95)
        user.refresh_from_db()
        self.assertEqual(user.trust_bits, 95)
        self.assertEqual(user.cancelled_reservations_count, 1)

    def test_penalize_trust_clamps_at_min(self):
        user = make_user(trust_bits=3)
        self.assertEqual(user.penalize_trust(), TRUST_MIN)
        self.assertEqual(user.penalize_trust(points=50), TRUST_MIN)
        user.refresh_from_db()
        self.assertEqual(user.trust_bits, TRUST_MIN)
        self.assertEqual(user.cancelled_reservations_count, 2)

    def test_initials_and_platform_admin(self):
        self.assertEqual(make_user(full_name="ali valiyev g'ani").initials, "AV")
        self.assertEqual(make_user(username="solo", full_name="Solo", phone=None).initials, "S")
        self.assertTrue(make_admin().is_platform_admin)
        self.assertTrue(make_user(username="staff", phone=None, is_staff=True).is_platform_admin)


# ---------------------------------------------------------------- admin users

class AdminUserListTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.ali = make_user()
        self.vali = make_user(username="vali", full_name="Vali Karimov", phone="+998907777777", role="business")
        self.blocked = make_user(username="blocked", full_name="Blok Blokov", phone=None, is_active=False)
        self.client = auth_client(self.admin)

    def test_anonymous_401(self):
        response = APIClient().get(ADMIN_USERS_URL)
        self.assertEqual(response.status_code, 401, response.data)

    def test_regular_user_403(self):
        response = auth_client(self.ali).get(ADMIN_USERS_URL)
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["error"]["code"], "permission_denied")

    def test_list_count_total_and_shape(self):
        response = self.client.get(ADMIN_USERS_URL)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 4)
        self.assertEqual(response.data["total"], 4)
        self.assertEqual(response.data["total_pages"], 1)
        self.assertEqual(response.data["current_page"], 1)
        row = next(r for r in response.data["results"] if r["username"] == "vali")
        self.assertEqual(row["role_display"], "Biznes admin")
        self.assertFalse(row["has_business"])
        self.assertEqual(row["trust"]["bits"], TRUST_START)
        self.assertNotIn("password", row)

    def test_filter_by_role_keeps_total(self):
        response = self.client.get(ADMIN_USERS_URL, {"role": "business"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["total"], 4)
        self.assertEqual(response.data["results"][0]["username"], "vali")

    def test_filter_by_is_active(self):
        response = self.client.get(ADMIN_USERS_URL, {"is_active": "false"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["username"], "blocked")

    def test_search_by_name_username_phone(self):
        response = self.client.get(ADMIN_USERS_URL, {"search": "Karimov"})
        self.assertEqual([r["username"] for r in response.data["results"]], ["vali"])
        response = self.client.get(ADMIN_USERS_URL, {"search": "blok"})
        self.assertEqual(response.data["count"], 1)
        response = self.client.get(ADMIN_USERS_URL, {"search": "+99890777"})
        self.assertEqual([r["username"] for r in response.data["results"]], ["vali"])

    def test_has_business_annotation(self):
        from businesses.services import submit_application
        submit_application(applicant=self.vali, business_type="restaurant", business_name="Shoxona")
        response = self.client.get(ADMIN_USERS_URL, {"search": "vali"})
        self.assertTrue(response.data["results"][0]["has_business"])

    def test_pagination_page_size(self):
        response = self.client.get(ADMIN_USERS_URL, {"page_size": 2})
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["total_pages"], 2)
        self.assertIsNotNone(response.data["next"])


class AdminUserDetailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = make_admin()
        self.user = make_user()
        self.client = auth_client(self.admin)
        self.url = f"{ADMIN_USERS_URL}{self.user.pk}/"

    def test_regular_user_403(self):
        response = auth_client(self.user).get(self.url)
        self.assertEqual(response.status_code, 403, response.data)

    def test_detail_404(self):
        response = self.client.get(f"{ADMIN_USERS_URL}999999/")
        self.assertEqual(response.status_code, 404, response.data)
        self.assertEqual(response.data["error"]["code"], "not_found")

    def test_detail_get(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["username"], "ali")
        self.assertTrue(response.data["is_active"])

    def test_patch_role(self):
        response = self.client.patch(self.url, {"role": "business"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["role"], "business")
        self.assertEqual(response.data["role_display"], "Biznes admin")
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "business")

    def test_patch_invalid_role_400(self):
        response = self.client.patch(self.url, {"role": "king"}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("role", response.data["error"]["details"])

    def test_patch_trust_bits_bounds(self):
        for bad in (0, 101, -1):
            response = self.client.patch(self.url, {"trust_bits": bad}, format="json")
            self.assertEqual(response.status_code, 400, (bad, response.data))
            self.assertIn("trust_bits", response.data["error"]["details"])
        response = self.client.patch(self.url, {"trust_bits": 55}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["trust"], describe(55))

    def test_patch_is_active_false_revokes_tokens(self):
        tokens = login(APIClient(), "ali").data
        response = self.client.patch(self.url, {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["is_active"])
        self.assertTrue(BlacklistedToken.objects.filter(token__user=self.user).exists())
        refreshed = APIClient().post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 401, refreshed.data)

    def test_patch_is_active_true_does_not_revoke(self):
        login(APIClient(), "ali")
        response = self.client.patch(self.url, {"is_active": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(BlacklistedToken.objects.filter(token__user=self.user).exists())

    def test_cannot_block_self(self):
        response = self.client.patch(f"{ADMIN_USERS_URL}{self.admin.pk}/", {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("O'z hisobingizni", response.data["error"]["message"])
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_is_staff_cannot_be_changed(self):
        response = self.client.patch(self.url, {"is_staff": True}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Tahrirlash mumkin", response.data["error"]["message"])

        response = self.client.patch(self.url, {"is_staff": True, "is_confirmed": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertTrue(self.user.is_confirmed)

    def test_empty_patch_400(self):
        response = self.client.patch(self.url, {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)

    def test_staff_cannot_change_superuser_role_or_active(self):
        staff = make_user(username="staff", full_name="Staff", phone=None, is_staff=True)
        client = auth_client(staff)
        url = f"{ADMIN_USERS_URL}{self.admin.pk}/"
        response = client.patch(url, {"role": "user"}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        response = client.patch(url, {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 403, response.data)
        # boshqa maydonlar mumkin
        response = client.patch(url, {"is_confirmed": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_staff_can_edit_regular_user(self):
        staff = make_user(username="staff", full_name="Staff", phone=None, is_staff=True)
        response = auth_client(staff).patch(self.url, {"trust_bits": 70}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["trust_bits"], 70)


class MiscTests(TestCase):
    def test_unknown_uuid_style_pk_is_404(self):
        client = auth_client(make_admin())
        response = client.get(f"{ADMIN_USERS_URL}{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404)
