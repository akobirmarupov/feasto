"""Sharhlarga bog'liq yordamchi mantiq."""

import logging

from django.db.models import Avg, Count, Sum

logger = logging.getLogger("reviews")


def recalculate_business_rating(business):
    result = business.reviews.aggregate(
        avg=Avg("rating"), total=Count("id"), points=Sum("rating")
    )
    business.rating_avg = round(result["avg"] or 0, 2)
    business.reviews_count = result["total"] or 0
    business.rating_points = result["points"] or 0
    business.save(update_fields=["rating_avg", "reviews_count", "rating_points"])

    logger.info(
        f"Business rating recalculated: business_id={business.id}, "
        f"avg={business.rating_avg}, count={business.reviews_count}, "
        f"points={business.rating_points}"
    )
    return business.rating_avg


def recalculate_ranks(business_type):
    from businesses.models import Business

    Business.objects.filter(business_type=business_type, is_visible=False).exclude(
        rank=0
    ).update(rank=0)

    rows = list(
        Business.objects.filter(business_type=business_type, is_visible=True)
        .order_by("-rating_points", "-reviews_count", "-rating_avg", "created_at")
        .only("id", "rank")
    )

    changed = []
    for position, business in enumerate(rows, start=1):
        if business.rank != position:
            business.rank = position
            changed.append(business)

    if changed:
        Business.objects.bulk_update(changed, ["rank"])
        logger.info(f"Ranks recalculated: type={business_type}, changed={len(changed)}")
    return len(changed)
