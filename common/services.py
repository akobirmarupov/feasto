from rest_framework.exceptions import NotFound


def get_owner_business(user):
    business = user.businesses.select_related("application", "application__plan").first()
    if business is None:
        raise NotFound("Sizda hali biznes profili yo'q. Avval ariza yuboring.")
    return business
