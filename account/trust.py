TRUST_START = 100
TRUST_MIN = 1
TRUST_MAX = 100

TRUST_CANCEL_PENALTY = 5

TRUST_LEVELS = (
    (80, "excellent", "A'lo", "ok"),
    (65, "good", "Yaxshi", "info"),
    (50, "fair", "Qoniqarli", "warn"),
    (30, "poor", "Yomon", "warn"),
    (TRUST_MIN, "bad", "O'ta yomon", "danger"),
)


def clamp_bits(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = TRUST_START
    return max(TRUST_MIN, min(TRUST_MAX, value))


def level_for(bits: int) -> tuple[str, str, str]:
    """Baldan daraja: `(kod, nom, ohang)`."""
    bits = clamp_bits(bits)
    for threshold, code, label, tone in TRUST_LEVELS:
        if bits >= threshold:
            return code, label, tone
    return "bad", "O'ta yomon", "danger"


def describe(bits: int) -> dict:
    bits = clamp_bits(bits)
    code, label, tone = level_for(bits)
    return {"bits": bits, "level": code, "level_display": label, "tone": tone}
