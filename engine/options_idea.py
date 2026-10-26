"""Turn a directional stock signal into a plain options idea (ATM call/put, monthly expiry).

Strike steps and expiry are APPROXIMATIONS of NSE conventions (stock options expire on the
last Tuesday of the month); the user must confirm both on the live option chain."""
import calendar
from datetime import date, timedelta

STEPS = [(250, 2.5), (700, 5), (2000, 10), (4000, 20), (8000, 50)]


def strike_step(price):
    for limit, step in STEPS:
        if price < limit:
            return step
    return 100


def atm_strike(price):
    step = strike_step(price)
    k = round(price / step) * step
    return int(k) if float(k).is_integer() else k


def last_tuesday(year, month):
    d = date(year, month, calendar.monthrange(year, month)[1])
    while d.weekday() != 1:
        d -= timedelta(days=1)
    return d


def monthly_expiry(today, min_days=3):
    """Current-month expiry, rolled to next month when fewer than `min_days` remain (fast time decay)."""
    exp = last_tuesday(today.year, today.month)
    if (exp - today).days < min_days:
        y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        exp = last_tuesday(y, m)
    return exp


def idea(direction, ltp, today, premium, stop_dist, rr, delta=0.5):
    """Premium stop/target use delta x the stock's stop/target distance (ignores time decay and IV changes)."""
    if direction not in ("LONG", "SHORT"):
        return None
    kind = "CE" if direction == "LONG" else "PE"
    exp = monthly_expiry(today)
    return {"action": f"BUY {kind}", "strike": atm_strike(ltp), "type": kind, "expiry": exp.isoformat(),
            "days_to_expiry": (exp - today).days, "premium_est": round(premium, 2),
            "premium_stop_est": round(max(premium - delta * stop_dist, 0.05), 2),
            "premium_target_est": round(premium + delta * rr * stop_dist, 2),
            "note": "Approximate strike step and expiry; confirm on the NSE option chain. Options trade in lots; "
                    "premium estimates ignore time decay and IV changes."}
