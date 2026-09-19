import re
from urllib.parse import quote



_MIN_COORD = 0.000001


def has_coordinates(latitude, longitude) -> bool:
    try:
        return abs(float(latitude or 0)) > _MIN_COORD and abs(float(longitude or 0)) > _MIN_COORD
    except (TypeError, ValueError):
        return False


def build_map_links(*, latitude=None, longitude=None, address="", name="") -> dict:
    if has_coordinates(latitude, longitude):
        point = f"{float(latitude):.6f},{float(longitude):.6f}"
        lng_lat = f"{float(longitude):.6f},{float(latitude):.6f}"
        return {
            "google": f"https://www.google.com/maps/search/?api=1&query={point}",
            "google_directions": f"https://www.google.com/maps/dir/?api=1&destination={point}",
            "yandex": f"https://yandex.uz/maps/?pt={lng_lat},pm2rdm&z=17&l=map",
            "yandex_directions": f"https://yandex.uz/maps/?rtext=~{point}&rtt=auto",
        }

    query = " ".join(part for part in (name, address) if part).strip()
    if not query:
        return {}

    encoded = quote(query)
    return {
        "google": f"https://www.google.com/maps/search/?api=1&query={encoded}",
        "google_directions": f"https://www.google.com/maps/dir/?api=1&destination={encoded}",
        "yandex": f"https://yandex.uz/maps/?text={encoded}",
        "yandex_directions": f"https://yandex.uz/maps/?text={encoded}&rtt=auto",
    }



_NUM = r"(-?\d{1,3}\.\d{3,})"

_PATTERNS = (
    # Google: .../@41.311081,69.240562,17z
    (re.compile(rf"@{_NUM},{_NUM}"), False),
    # Google: !3d41.311081!4d69.240562
    (re.compile(rf"!3d{_NUM}!4d{_NUM}"), False),
    # Yandex: ?ll=69.240562,41.311081  /  &pt=69.240562,41.311081
    (re.compile(rf"[?&](?:ll|pt|whatshere%5Bpoint%5D)={_NUM}(?:%2C|,){_NUM}"), True),
    # Yandex marshrut: ?rtext=~41.311081,69.240562
    (re.compile(rf"rtext=[^&]*?{_NUM}(?:%2C|,){_NUM}"), False),
    # Google: ?q=41.311081,69.240562  /  ?query=...  /  ?destination=...
    (re.compile(rf"[?&](?:q|query|daddr|destination|center)={_NUM}(?:%2C|,)\s*{_NUM}"), False),
    # 2GIS: /geo/.../69.240562,41.311081
    (re.compile(rf"2gis\.[a-z]+/.*?/{_NUM},{_NUM}"), True),
    # Oddiy matn: "41.311081, 69.240562"
    (re.compile(rf"^\s*{_NUM}\s*,\s*{_NUM}\s*$"), False),
)


def coordinates_from_link(value: str):
    if not value:
        return None

    text = str(value).strip()
    for pattern, lng_first in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        first, second = float(match.group(1)), float(match.group(2))
        latitude, longitude = (second, first) if lng_first else (first, second)
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            return latitude, longitude
    return None
