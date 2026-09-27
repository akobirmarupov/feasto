from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from account.trust import TRUST_MAX, TRUST_MIN
from businesses.models import BusinessApplication

User = get_user_model()


class GoogleAuthSerializer(serializers.Serializer):
    credential = serializers.CharField(
        write_only=True, help_text="Google Identity Services bergan id_token.",
    )


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class BusinessBriefSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    type = serializers.CharField(source="business_type", read_only=True)
    is_visible = serializers.BooleanField(read_only=True)
    application_status = serializers.SerializerMethodField()
    is_approved = serializers.SerializerMethodField()
    subscription_status = serializers.SerializerMethodField()

    def get_application_status(self, obj) -> str | None:
        application = getattr(obj, "application", None)
        return application.status if application else None

    def get_is_approved(self, obj) -> bool:
        application = getattr(obj, "application", None)
        if application is not None:
            return application.status == BusinessApplication.STATUS_APPROVED
        return True

    def get_subscription_status(self, obj) -> str | None:
        subscription = getattr(obj, "subscription", None)
        return subscription.status if subscription else None


def user_business(user):
    return user.businesses.select_related("application", "subscription").first()


def build_user_payload(user, request=None):
    business = user_business(user)

    avatar = user.avatar.url if user.avatar else None
    if avatar and request is not None:
        avatar = request.build_absolute_uri(avatar)

    return {
        "id": str(user.id),
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "phone_number": user.phone_number,
        "role": user.role,
        "is_staff": user.is_staff,
        "is_phone_verified": user.is_phone_verified,
        "is_confirmed": user.is_confirmed,
        "has_used_trial": user.has_used_trial,
        "avatar": avatar,
        "initials": user.initials,
        "preferred_language": user.preferred_language,
        "trust": user.trust,
        "business": BusinessBriefSerializer(business).data if business else None,
    }


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = build_user_payload(self.user, self.context.get("request"))
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["full_name"] = user.full_name
        token["username"] = user.username
        token["role"] = user.role
        token["is_confirmed"] = user.is_confirmed
        return token


class UserSerializer(serializers.ModelSerializer):
    business = serializers.SerializerMethodField()
    initials = serializers.CharField(read_only=True)
    stats = serializers.SerializerMethodField()
    trust = serializers.DictField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "full_name", "email", "phone_number",
            "avatar", "initials", "bio", "birth_date", "preferred_language",
            "role", "is_staff", "is_phone_verified", "is_confirmed",
            "has_used_trial", "trust", "trust_bits",
            "cancelled_reservations_count",
            "business", "stats", "date_joined",
        ]
        read_only_fields = [
            "id", "username", "email", "role", "is_staff",
            "is_phone_verified", "is_confirmed", "has_used_trial",
            "trust", "trust_bits", "cancelled_reservations_count",
            "business", "stats", "initials", "date_joined",
        ]

    def validate_full_name(self, value):
        value = " ".join((value or "").split())
        if not value:
            raise serializers.ValidationError("Ism-familiya bo'sh bo'lishi mumkin emas.")
        return value

    def validate_phone_number(self, value):
        return value or None

    def update(self, instance, validated_data):
        if "phone_number" in validated_data:
            instance.is_phone_verified = bool(validated_data["phone_number"])
        old_avatar = instance.avatar if "avatar" in validated_data else None
        instance = super().update(instance, validated_data)
        if old_avatar and old_avatar.name != (instance.avatar.name if instance.avatar else None):
            old_avatar.delete(save=False)
        return instance

    def get_business(self, obj) -> dict | None:
        business = user_business(obj)
        return BusinessBriefSerializer(business).data if business else None

    def get_stats(self, obj) -> dict:
        stats = obj.reservations.aggregate(
            total=Count("id"),
            completed=Count("id", filter=Q(status="completed")),
            upcoming=Count("id", filter=Q(status__in=["pending", "confirmed"])),
            cancelled=Count("id", filter=Q(status="cancelled")),
        )
        stats["reviews"] = obj.reviews.count()
        return stats


class AvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["avatar"]
        extra_kwargs = {"avatar": {"required": True, "allow_null": False}}

    def validate_avatar(self, value):
        if not value:
            raise serializers.ValidationError("Rasm tanlanmadi.")
        return value


class UserAdminSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)
    has_business = serializers.BooleanField(read_only=True, default=False)
    trust = serializers.DictField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "username", "full_name", "email", "phone_number",
            "role", "role_display", "is_phone_verified", "is_confirmed",
            "is_active", "is_staff", "has_business",
            "trust", "trust_bits", "cancelled_reservations_count",
            "date_joined", "last_login",
        ]
        read_only_fields = fields


class UserAdminUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["is_active", "is_confirmed", "is_phone_verified", "role", "trust_bits"]
        extra_kwargs = {field: {"required": False} for field in fields}

    def validate_trust_bits(self, value):
        if not TRUST_MIN <= value <= TRUST_MAX:
            raise serializers.ValidationError(
                f"Ishonchlilik bali {TRUST_MIN}–{TRUST_MAX} oralig'ida bo'lishi kerak."
            )
        return value
