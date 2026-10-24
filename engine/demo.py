"""Deterministic synthetic market used when live sources are unreachable (clearly flagged DEMO)."""
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import IST

# Scripted scenarios so the demo exercises the contradiction matrix.
SCENARIOS = {"RELIANCE": "trap", "TCS": "breakout", "HDFCBANK": "range", "SUNPHARMA": "breakdown", "HAL": "genuine", "INFY": "breakout"}


def last_session_date(now):
    d = now.date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _session_index(date, n=75, start=(9, 15)):
    t0 = datetime(date.year, date.month, date.day, *start, tzinfo=IST)
    return pd.DatetimeIndex([t0 + timedelta(minutes=5 * i) for i in range(n)])


def _bars(rng, idx, start_price, drift, sigma, base_vol, persistence=0.9, vol_burst=1.0):
    n, px, rows, state = len(idx), start_price, [], 0
    u = np.linspace(-1, 1, n)
    for i in range(n):
        if rng.random() > persistence:
            state = rng.choice([-1, 0, 1])
        r = drift + 0.5 * state * sigma + rng.normal(0, sigma)
        o, c = px, px * (1 + r)
        hi, lo = max(o, c) * (1 + abs(rng.normal(0, sigma / 3))), min(o, c) * (1 - abs(rng.normal(0, sigma / 3)))
        v = base_vol * (1 + 1.2 * u[i] ** 2) * (1 + vol_burst * abs(r) / sigma * 0.4) * rng.uniform(0.8, 1.2)
        rows.append((o, hi, lo, c, v)); px = c
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


INDEX_BASE = {"^NSEI": 24500.0, "^NSEBANK": 53000.0, "^INDIAVIX": 14.0}


def make_symbol(symbol, now, kind=None):
    seed = zlib.crc32(symbol.encode())
    rng = np.random.default_rng(seed)
    kind = kind or SCENARIOS.get(symbol) or rng.choice(["range", "breakout", "breakdown", "range"])
    today = last_session_date(now)
    base = INDEX_BASE.get(symbol) or float(rng.uniform(300, 4000))
    days, d, hist_bars = [], today - timedelta(days=1), []
    # 60 daily bars (oldest first), ending the day before `today`
    dates = []
    while len(dates) < 60:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    dates.reverse()
    px, drows = base * 0.9, []
    trend_up = kind in ("trap", "genuine") and kind != "genuine"
    for dt in dates:
        r = rng.normal(0.0004 + (0.0035 if (trap_idx := dt >= dates[-4]) and kind == "trap" else 0), 0.011)
        if kind == "genuine" and dt >= dates[-3]:
            r = rng.normal(-0.006, 0.006)
        o, c = px, px * (1 + r)
        h, l = max(o, c) * (1 + abs(rng.normal(0, 0.006))), min(o, c) * (1 - abs(rng.normal(0, 0.006)))
        drows.append((o, h, l, c, rng.uniform(2e6, 6e6)))
        px = c
    daily = pd.DataFrame(drows, columns=["open", "high", "low", "close", "volume"],
                         index=pd.DatetimeIndex([datetime(x.year, x.month, x.day, 15, 30, tzinfo=IST) for x in dates]))
    prev_close = float(daily["close"].iloc[-1])
    # 5 historical sessions of 5m bars
    hist = pd.concat([_bars(rng, _session_index(x), float(daily["close"].iloc[-5 + i]), 0.0, 0.0016, 6e4)
                      for i, x in enumerate(dates[-5:])])
    n_today = 28  # through ~11:30 IST
    idx = _session_index(today, n_today)
    gap = {"trap": 0.022, "genuine": 0.018, "breakout": 0.009, "breakdown": -0.009, "range": 0.0005}[kind]
    start = prev_close * (1 + gap)
    drift = {"trap": -0.0011, "genuine": 0.0011, "breakout": 0.0009, "breakdown": -0.0010, "range": 0.0}[kind]
    td = _bars(rng, idx, start, drift, 0.0016, 7e4, persistence=0.93, vol_burst=1.6)
    if kind == "trap":  # make the latest bar a clear bearish rejection
        td.iloc[-1, td.columns.get_loc("open")] = td["close"].iloc[-1] * 1.002
        td.iloc[-1, td.columns.get_loc("high")] = td["open"].iloc[-1] * 1.0005
    if kind == "range":  # mean reverting around start
        c = start * (1 + 0.003 * np.sin(np.linspace(0, 6, n_today)) + rng.normal(0, 0.0005, n_today))
        td["close"] = c; td["open"] = np.r_[start, c[:-1]]
        td["high"] = td[["open", "close"]].max(axis=1) * 1.0006; td["low"] = td[["open", "close"]].min(axis=1) * 0.9994
    return {"kind": kind, "today": td, "hist": hist, "daily": daily, "prev_close": prev_close}


def make_index(name, now, base, kind):
    return make_symbol(name, now, kind)


NEWS = {
    "trap": [("Reliance bags record order worth Rs 9,000 crore, shares jump", "Economic Times"),
             ("Reliance Q2 net profit beats estimates, revenue surges", "Moneycontrol")],
    "genuine": [("HAL wins Rs 30,000 crore defence contract; stock soars on strong order book", "Livemint"),
                ("HAL secures record order, analysts upgrade", "Economic Times")],
    "breakout": [("Strong growth outlook, brokerages upgrade stock", "Moneycontrol")],
    "breakdown": [("USFDA issues warning letter, observations at plant", "Economic Times"),
                  ("Shares slump on regulatory concern", "Moneycontrol")],
    "range": [("Company signs MoU for partnership", "Livemint")],
}


def demo_news(symbol, kind, now):
    ts = datetime.now(timezone.utc) - timedelta(hours=3)
    return [{"title": t, "source": s, "url": "", "ts": ts} for t, s in NEWS.get(kind, [])]
