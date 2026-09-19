import hashlib
import logging

from django.core.cache import cache

logger = logging.getLogger("common")

BUSINESS_VERSION_KEY = "business:version"
DEFAULT_VERSION = 1


def get_business_version() -> int:
    version = cache.get(BUSINESS_VERSION_KEY)
    if version is None:
        cache.set(BUSINESS_VERSION_KEY, DEFAULT_VERSION, None)
        return DEFAULT_VERSION
    return version


def invalidate_business_cache():
    try:
        cache.incr(BUSINESS_VERSION_KEY)
    except ValueError:
        cache.set(BUSINESS_VERSION_KEY, DEFAULT_VERSION + 1, None)
    logger.debug("Business kesh versiyasi oshirildi")


def build_cache_key(prefix: str, *parts) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:v{get_business_version()}:{digest}"


def cached_response(key: str, ttl: int, producer):
    cached = cache.get(key)
    if cached is not None:
        return cached
    value = producer()
    cache.set(key, value, ttl)
    return value
