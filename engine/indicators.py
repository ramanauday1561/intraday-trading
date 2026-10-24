"""Microstructure and technical indicators on OHLCV frames (index = tz-aware timestamps)."""
import math

import numpy as np
import pandas as pd


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    v = df["volume"].replace(0, np.nan)
    return float((tp * v).sum() / v.sum()) if v.sum() else float(df["close"].iloc[-1])


def atr(daily, n=14):
    h, l, c = daily["high"], daily["low"], daily["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1.0 / n, adjust=False).mean().iloc[-1])


def adx(daily, n=14):
    h, l, c = daily["high"], daily["low"], daily["close"]
    up, dn = h.diff(), -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1.0 / n
    atr_ = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=daily.index).ewm(alpha=a, adjust=False).mean() / atr_
    mdi = 100 * pd.Series(minus, index=daily.index).ewm(alpha=a, adjust=False).mean() / atr_
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return float(dx.ewm(alpha=a, adjust=False).mean().iloc[-1])


def volume_profile(df, bins=30, area=0.70):
    """Return (poc, val, vah): point of control and the 70% value area."""
    lo, hi = float(df["low"].min()), float(df["high"].max())
    if hi <= lo:
        p = float(df["close"].iloc[-1])
        return p, p, p
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    tp = ((df["high"] + df["low"] + df["close"]) / 3.0).to_numpy()
    idx = np.clip(np.digitize(tp, edges) - 1, 0, bins - 1)
    for i, v in zip(idx, df["volume"].to_numpy()):
        vol[i] += v
    mid = (edges[:-1] + edges[1:]) / 2
    p = int(vol.argmax())
    lo_i = hi_i = p
    total, acc = vol.sum(), vol[p]
    while acc < area * total and (lo_i > 0 or hi_i < bins - 1):
        below = vol[lo_i - 1] if lo_i > 0 else -1
        above = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if above >= below:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    return float(mid[p]), float(edges[lo_i]), float(edges[hi_i + 1])


def signed_volume(df):
    """Bulk Volume Classification: buy fraction = Phi(dP / sigma). Returns signed volume series."""
    dp = df["close"].diff().fillna(0.0)
    sigma = dp.expanding(min_periods=3).std().bfill().replace(0, np.nan).fillna(1e-9)
    frac = (dp / sigma).map(_phi)
    return df["volume"] * (2 * frac - 1)


def ofi(df, window=12):
    """Normalised order-flow imbalance in [-1, 1] over the last `window` bars."""
    sv = signed_volume(df).iloc[-window:]
    tot = df["volume"].iloc[-window:].sum()
    return float(sv.sum() / tot) if tot else 0.0


def vpin(df, buckets=20, lookback=10):
    """Volume-synchronised probability of informed trading on a volume clock."""
    tot = float(df["volume"].sum())
    if tot <= 0 or len(df) < 6:
        return 0.0
    size = tot / buckets
    sv = signed_volume(df)
    out, buy, sell, filled = [], 0.0, 0.0, 0.0
    for v, s in zip(df["volume"].to_numpy(), sv.to_numpy()):
        b_frac = 0.5 * (1 + (s / v if v else 0))
        rem = float(v)
        while rem > 0:
            take = min(rem, size - filled)
            buy += take * b_frac; sell += take * (1 - b_frac)
            filled += take; rem -= take
            if filled >= size - 1e-9:
                out.append(abs(buy - sell) / size)
                buy = sell = filled = 0.0
    return float(np.mean(out[-lookback:])) if out else 0.0


def _fano_branching(events, window):
    ev = np.asarray(events, dtype=float)
    ac = 0.0
    if len(ev) > 12 and ev.std() > 0:  # lag-1 autocorrelation of events ~ branching ratio for a discrete Hawkes
        ac = float(np.corrcoef(ev[:-1], ev[1:])[0, 1])
    n = len(events) // window
    if n < 4:
        return float(min(max(ac, 0.0), 0.95))
    counts = np.asarray(events[: n * window]).reshape(n, window).sum(axis=1)
    m = counts.mean()
    if m <= 0:
        return 0.0
    fano = max(counts.var(ddof=1) / m, 1.0)
    # Hawkes with branching ratio n has long-window Fano factor 1/(1-n)^2
    return float(min(max(1.0 - 1.0 / math.sqrt(fano), ac, 0.0), 0.95))


def hawkes_branching(df_hist, df_today, thr=0.3):
    """Fano-factor / autocorrelation estimate of the Hawkes branching ratio for aggressive buy / sell bars.

    Returns dict(buy, sell, base_buy, base_sell, surge_buy, surge_sell). A surge means the
    session's self-excitation exceeds its multi-day baseline."""
    def imb(d):
        sv = signed_volume(d)
        return (sv / d["volume"].replace(0, np.nan)).fillna(0.0).to_numpy()
    h, t = imb(df_hist), imb(df_today)
    res = {}
    for side, sign in (("buy", 1), ("sell", -1)):
        base = _fano_branching((sign * h > thr).astype(int), 6)
        cur = _fano_branching((sign * t > thr).astype(int), 3) if len(t) >= 12 else base
        res[side] = cur
        res["base_" + side] = base
        res["surge_" + side] = bool(cur > 0.35 and cur > base * 1.25)
    return res


def volume_expanding(df, recent=3):
    if len(df) < recent + 6:
        return False
    return bool(df["volume"].iloc[-recent:].mean() > 1.2 * df["volume"].iloc[:-recent].mean())
