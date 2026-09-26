from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle,UserRateThrottle


class BurstUserThrottle(UserRateThrottle):
    scope = "burst_user"


class SustainedUserThrottle(UserRateThrottle):
    scope = "sustained_user"


class BurstAnonThrottle(AnonRateThrottle):
    scope = "burst_anon"


class SustainedAnonThrottle(AnonRateThrottle):
    scope = "sustained_anon"


class LoginThrottle(SimpleRateThrottle):

    scope = "login"

    def get_cache_key(self, request, view):
        username = request.data.get("username", "anonymous")
        ident = f"{self.get_ident(request)}:{username}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ReservationCreateThrottle(UserRateThrottle):
    scope = "reservation_create"


class BusinessApplicationThrottle(UserRateThrottle):
    scope = "business_application"


class ReviewCreateThrottle(UserRateThrottle):
    scope = "review_create"


class FeedbackThrottle(UserRateThrottle):
    scope = "feedback"
