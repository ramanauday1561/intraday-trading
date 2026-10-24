"""Limit Price Protection modelling: +/-40% of a rolling 30-second reference price (premium > Rs 50)."""
from .config import LPP_MIN_PREMIUM, LPP_PCT


def reference_price(trades, now_ts, window_s=30):
    """Simple moving average of executed trade prices inside the trailing window.

    `trades` is a list of (epoch_seconds, price). Falls back to the latest price."""
    recent = [p for t, p in trades if now_ts - t <= window_s]
    if recent:
        return sum(recent) / len(recent)
    return trades[-1][1] if trades else None


def lpp_range(ref, premium):
    if ref is None or premium <= LPP_MIN_PREMIUM:
        return None
    return ref * (1 - LPP_PCT), ref * (1 + LPP_PCT)


def validate_order(price, ref, premium):
    """True when the order price sits inside the active LPP band (else NSE error 17070)."""
    rng = lpp_range(ref, premium)
    return True if rng is None else rng[0] <= price <= rng[1]


def clamp(price, ref, premium, buffer=0.02):
    rng = lpp_range(ref, premium)
    if rng is None:
        return price
    lo, hi = rng[0] * (1 + buffer), rng[1] * (1 - buffer)
    return min(max(price, lo), hi)


def headroom_pct(price, ref, premium):
    """Distance to the nearest LPP boundary as a % of the band half-width (100 = at reference)."""
    rng = lpp_range(ref, premium)
    if rng is None:
        return None
    half = (rng[1] - rng[0]) / 2
    return round(100 * min(price - rng[0], rng[1] - price) / half, 1)
