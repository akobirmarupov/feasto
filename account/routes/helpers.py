from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from account.routes.serializers import CustomTokenObtainPairSerializer
from common.exceptions import BadRequest  # noqa: F401  (routes shu yerdan import qiladi)

BLOCKED_MESSAGE = "Hisobingiz bloklangan. Administrator bilan bog'laning."


def issue_tokens(user):
    refresh = CustomTokenObtainPairSerializer.get_token(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def revoke_all_tokens(user):
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
