import json
import logging
import re
import unicodedata
import urllib.parse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from account.models import User

logger = logging.getLogger("account")

AVATAR_SIZE = 256
AVATAR_TIMEOUT = 10

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile"


class GoogleAuthError(Exception):
    pass


def build_auth_url(*, redirect_uri, state):
    if not settings.GOOGLE_CLIENT_ID:
        raise GoogleAuthError(
            "Google orqali kirish sozlanmagan. GOOGLE_CLIENT_ID ni .env ga qo'shing."
        )
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(*, code, redirect_uri):
    if not settings.GOOGLE_CLIENT_SECRET:
        raise GoogleAuthError(
            "GOOGLE_CLIENT_SECRET .env da yo'q. Google Console → Clients → "
            "OAuth client → Client secrets → Add secret."
        )

    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    request = Request(
        TOKEN_URL, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read())
    except Exception as error:
        logger.warning(f"Google token almashinuvi ishlamadi: {error}")
        raise GoogleAuthError(
            "Google bilan bog'lanib bo'lmadi. Qaytadan urinib ko'ring."
        ) from error

    id_token_value = data.get("id_token")
    if not id_token_value:
        raise GoogleAuthError("Google javobida id_token yo'q.")
    return id_token_value


def verify_google_token(credential):
    client_id = settings.GOOGLE_CLIENT_ID
    if not client_id:
        raise GoogleAuthError(
            "Google orqali kirish sozlanmagan. GOOGLE_CLIENT_ID ni .env ga qo'shing."
        )

    try:
        payload = google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id
        )
    except ValueError as error:
        logger.warning(f"Google token rad etildi: {error}")
        raise GoogleAuthError("Google tasdig'i qabul qilinmadi. Qaytadan urinib ko'ring.") from error

    if payload.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleAuthError("Token Google tomonidan berilmagan.")
    if not payload.get("email"):
        raise GoogleAuthError("Google hisobida pochta manzili topilmadi.")

    return payload


def username_from_email(email):
    local = (email or "").split("@")[0]

    local = unicodedata.normalize("NFKD", local).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9_]", "_", local.lower()).strip("_")
    base = re.sub(r"_{2,}", "_", base)[:24] or "user"
    if len(base) < 3:
        base = f"{base}_user"[:24]

    candidate = base
    suffix = 2
    while User.objects.filter(username=candidate).exists():
        tail = str(suffix)
        candidate = f"{base[:24 - len(tail)]}{tail}"
        suffix += 1
    return candidate


def _download_avatar(url):
    if not url:
        return None
    try:
        sized = re.sub(r"=s\d+(-c)?$", f"=s{AVATAR_SIZE}-c", url)
        request = Request(sized, headers={"User-Agent": "Feasto/1.0"})
        with urlopen(request, timeout=AVATAR_TIMEOUT) as response:
            return ContentFile(response.read())
    except Exception as error:
        logger.warning(f"Google avatarini yuklab bo'lmadi: {error}")
        return None


@transaction.atomic
def get_or_create_google_user(payload):
    sub = payload["sub"]
    email = payload["email"].lower()

    user = User.objects.filter(google_sub=sub).first()
    created = False

    if user is None:
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            user.google_sub = sub
            user.save(update_fields=["google_sub"])
            logger.info(f"Existing account linked to Google: user_id={user.id}")

    if user is None:
        user = User(
            username=username_from_email(email),
            email=email,
            google_sub=sub,
            full_name=(payload.get("name") or "").strip() or email.split("@")[0],
            is_confirmed=True,
        )
        user.set_unusable_password()
        user.save()
        created = True
        logger.info(f"User created via Google: id={user.id}, username={user.username}")

    fields = []
    if not user.is_confirmed:
        user.is_confirmed = True
        fields.append("is_confirmed")
    if not user.email:
        user.email = email
        fields.append("email")
    if not user.full_name and payload.get("name"):
        user.full_name = payload["name"].strip()
        fields.append("full_name")
    if fields:
        user.save(update_fields=fields)

    if not user.avatar:
        picture = _download_avatar(payload.get("picture"))
        if picture is not None:
            user.avatar.save(f"google-{user.pk}.jpg", picture, save=True)

    return user, created
