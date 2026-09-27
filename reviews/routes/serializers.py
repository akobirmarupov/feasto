from rest_framework import serializers

from reviews.models import Review, ReviewPhoto


class ReviewPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewPhoto
        fields = ["id", "review", "image", "created_at"]
        read_only_fields = ["id", "review", "created_at"]


class ReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    business_name = serializers.CharField(source="business.name", read_only=True)
    photos = ReviewPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = Review
        fields = [
            "id", "user", "user_name", "user_username",
            "business", "business_name", "reservation",
            "rating", "comment", "photos", "created_at",
        ]
        read_only_fields = fields


class ReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["reservation", "rating", "comment"]

    def validate_reservation(self, reservation):
        user = self.context["request"].user
        if reservation.user_id != user.id:
            raise serializers.ValidationError("Bu bron sizga tegishli emas.")
        if reservation.status != "completed":
            raise serializers.ValidationError("Sharh faqat yakunlangan bron uchun qoldiriladi.")
        if Review.objects.filter(reservation=reservation).exists():
            raise serializers.ValidationError("Bu bron uchun sharh allaqachon qoldirilgan.")
        return reservation


class ReviewUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["rating", "comment"]
        extra_kwargs = {"rating": {"required": False}, "comment": {"required": False}}
