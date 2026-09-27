from rest_framework import serializers

from common.models import Feedback, PlatformSettings


class PlatformSettingsSerializer(serializers.ModelSerializer):
    admin_telegram = serializers.SerializerMethodField()

    class Meta:
        model = PlatformSettings
        fields = [
            "admin_telegram_username", "admin_telegram", "support_phone",
            "room_deposit_premium", "room_deposit_pro", "venue_deposit",
            "trial_days", "subscription_days",
        ]

    def get_admin_telegram(self, obj) -> str:
        return f"@{obj.admin_telegram_username}"


class PublicSettingsSerializer(serializers.Serializer):
    admin_telegram = serializers.CharField(read_only=True)
    support_phone = serializers.CharField(read_only=True)
    trial_days = serializers.IntegerField(read_only=True)
    subscription_days = serializers.IntegerField(read_only=True)
    deposits = serializers.DictField(read_only=True)
    plans = serializers.ListField(read_only=True)


class FeedbackCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feedback
        fields = ["kind", "message", "contact"]

    def validate_message(self, value):
        value = value.strip()
        if len(value) < 10:
            raise serializers.ValidationError(
                "Fikringizni biroz to'liqroq yozing — kamida 10 ta belgi."
            )
        return value


class FeedbackSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    author_name = serializers.SerializerMethodField()
    author_phone = serializers.CharField(source="user.phone_number", read_only=True)
    author_role = serializers.CharField(source="user.role", read_only=True)

    class Meta:
        model = Feedback
        fields = [
            "id", "kind", "kind_display", "message", "page", "contact",
            "status", "status_display", "admin_note",
            "user", "author_name", "author_phone", "author_role", "created_at",
        ]
        read_only_fields = ["id", "kind", "message", "page", "contact", "user", "created_at"]

    def get_author_name(self, obj) -> str:
        return obj.user.full_name if obj.user_id else "Mehmon"
