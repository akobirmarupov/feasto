from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsSuperAdmin(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )


class HasRole(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        required_role = getattr(view, "required_role", None)
        if required_role is None:
            return True
        return request.user.role == required_role


class IsBusinessOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        business = obj if hasattr(obj, "owner") else getattr(obj, "business", None)
        return bool(business and business.owner_id == request.user.id)


class IsReservationOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id


class IsReviewOwnerOrReadOnly(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.user_id == request.user.id


class HasContactPhone(BasePermission):
    message = "Davom etish uchun aloqa raqamingizni kiriting."
    code = "phone_required"

    def has_permission(self, request, view):
        self.message = getattr(view, "phone_message", self.message)
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.phone_number
        )

def unapproved_application_message(application):
    if application is not None and application.status == "rejected":
        return (
            "Arizangiz rad etilgan. Sababini administrator bilan "
            "aniqlashtiring va \"Obuna va Premium\" bo'limidan arizani "
            "qayta yuboring."
        )

    applied_plan = application.plan if application else None
    if applied_plan is None:
        from common.models import PlatformSettings
        days = PlatformSettings.get_solo().trial_days
        return (
            "Arizangiz hali tasdiqlanmagan. Administrator tekshirgach, "
            f"{days} kunlik bepul sinov boshlanadi va barcha bo'limlar ochiladi."
        )
    return (
        "Arizangiz hali tasdiqlanmagan. Administrator to'lovingizni "
        f"tasdiqlagach obunangiz {applied_plan.duration_label}ga faollashadi "
        "va barcha bo'limlar ochiladi."
    )


class IsBusinessRole(BasePermission):
    message = "Bu bo'lim faqat biznes egalari uchun."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False

        if request.user.is_staff or request.user.is_superuser:
            self.message = (
                "Platforma egasi biznes panelidan foydalanmaydi — "
                "boshqaruv paneliga o'ting."
            )
            return False

        if request.user.role == "business":
            return True

        business = request.user.businesses.select_related(
            "application", "application__plan"
        ).first()
        if business is not None:
            self.message = unapproved_application_message(
                getattr(business, "application", None)
            )
        return False


class IsBusinessOwnerOrApplicant(IsBusinessRole):
    message = "Bu bo'lim biznes egalari va ariza yuborganlar uchun."

    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        user = request.user
        if not (user and user.is_authenticated) or user.is_staff or user.is_superuser:
            return False
        return user.businesses.exists()


class IsOwnerOfBusinessType(IsBusinessRole):

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        required = getattr(view, "required_business_type", None)
        if required is None:
            return True
        business = request.user.businesses.first()
        if business is None:
            self.message = "Sizda hali biznes profili yo'q."
            return False
        if business.business_type != required:
            self.message = (
                "Bu bo'lim restoran egalari uchun."
                if required == "restaurant"
                else "Bu bo'lim to'yxona egalari uchun."
            )
            return False
        return True


class HasActiveSubscription(BasePermission):
    message = (
        "Obunangiz muddati tugagan. Davom ettirish uchun administrator bilan "
        "Telegram orqali bog'laning."
    )

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_staff:
            return True

        business = request.user.businesses.select_related("application", "subscription").first()
        if business is None:
            return False

        subscription = getattr(business, "subscription", None)
        if subscription is None:
            application = getattr(business, "application", None)
            approved = application is not None and application.status == "approved"

            if approved:
                self.message = (
                    "Obunangiz hali ochilmagan. Davom ettirish uchun tarif tanlab, "
                    "administrator bilan Telegram orqali bog'laning."
                )
            else:
                self.message = unapproved_application_message(application)

            return False
        return subscription.status in ("trial", "active")


class IsCustomer(BasePermission):
    message = (
        "Platforma egasi bron qila olmaydi — bu bo'lim mijozlar uchun. "
        "Bronlarni boshqarish uchun boshqaruv panelidan foydalaning."
    )

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return not (request.user.is_staff or request.user.is_superuser)
