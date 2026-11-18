"""Test new signal ideas on the 5-minute archive, fast and with the same costs as live.

    ARCHIVE_DIR=/var/lib/intraday-history python -m engine.signals_lab [--cache trades_5m.json]

Each idea is a small, fixed rule (parameters fixed in docs/IMPROVEMENT_LOG.md on 2026-11-18, before any result).
Trades: entry at the close of the signal bar, stop checked on every later bar (stop first if a bar touches both
stop and target), exit at the 15:00 bar's close (= 15:05), one trade per stock per day. Charges: Zerodha intraday
(engine/charges.py) at the order server's sizing (Rs 20,000, 5% risk, 5x margin cap). Sessions split 60/40 by date.

Pass mark (pre-registered): mean net R > 0 in TRAIN and in TEST, TEST 95% CI lower bound > 0, at least 150 trades.
"""
import argparse
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from . import archive, charges, evaluate
from .config import COST_CAPITAL, COST_RISK_PCT, INDEX_SYMBOLS, UNIVERSE

EXIT_BAR = (15, 0)  # close of the 15:00 bar = 15:05


def _days(df):
    """{date: DataFrame of that session's 5-minute bars, 09:15..15:25}"""
    df = df[(df.index.hour * 60 + df.index.minute >= 9 * 60 + 15) & (df.index.hour * 60 + df.index.minute <= 15 * 60 + 25)]
    return {d: g for d, g in df.groupby(df.index.date) if len(g) >= 60}


def _daily_atr(days, n=14):
    rows = sorted(days)
    hi = np.array([days[d]["high"].max() for d in rows]); lo = np.array([days[d]["low"].min() for d in rows])
    cl = np.array([days[d]["close"].iloc[-1] for d in rows])
    prev = np.r_[np.nan, cl[:-1]]
    tr = np.nanmax(np.vstack([hi - lo, np.abs(hi - prev), np.abs(lo - prev)]), axis=0)
    atr = pd.Series(tr).rolling(n).mean().shift(1).to_numpy()   # known before the session opens
    return {d: (a, p) for d, a, p in zip(rows, atr, prev)}


def _hm(ts):
    return ts.hour * 60 + ts.minute


def simulate(day, i, long_, sd, target_r=None, target_px=None):
    """Trade entered at the close of bar i. Returns (exit_px, reason) or None if no bar after i."""
    entry = float(day["close"].iloc[i])
    stop = entry - sd if long_ else entry + sd
    tgt = target_px if target_px is not None else (None if target_r is None else (entry + target_r * sd if long_ else entry - target_r * sd))
    last = EXIT_BAR[0] * 60 + EXIT_BAR[1]
    hi, lo, cl = day["high"].to_numpy(), day["low"].to_numpy(), day["close"].to_numpy()
    mins = day.index.hour * 60 + day.index.minute
    for j in range(i + 1, len(day)):
        if mins[j] > last:
            break
        if (lo[j] <= stop) if long_ else (hi[j] >= stop):
            return stop, "STOP"
        if tgt is not None and ((hi[j] >= tgt) if long_ else (lo[j] <= tgt)):
            return tgt, "TARGET"
        if mins[j] == last:
            return float(cl[j]), "EOD"
    return None


def trade(date, sym, day, i, long_, sd, idea, **kw):
    if not sd or sd <= 0 or i >= len(day) - 1:
        return None
    res = simulate(day, i, long_, sd, **kw)
    if res is None:
        return None
    entry = float(day["close"].iloc[i])
    exit_px, reason = res
    per_share = (exit_px - entry) if long_ else (entry - exit_px)
    q = charges.sized_qty(entry, sd, COST_CAPITAL, COST_RISK_PCT)
    if q <= 0:
        return None
    cost_r = charges.intraday(entry, exit_px, q, long_) / (q * sd)
    return {"idea": idea, "date": date.isoformat(), "symbol": sym, "direction": "LONG" if long_ else "SHORT",
            "signal_time": day.index[i].strftime("%H:%M"), "reason": reason,
            "r_multiple": round(per_share / sd, 3), "cost_r": round(cost_r, 3), "net_r": round(per_share / sd - cost_r, 3)}


def _idx(day, hhmm):
    m = hhmm[0] * 60 + hhmm[1]
    hits = [k for k, ts in enumerate(day.index) if _hm(ts) == m]
    return hits[0] if hits else None


# ---------------- the ideas (parameters fixed 2026-11-18) ----------------
def orb_trend(data, nifty):
    """A. 30-minute opening-range breakout in the direction of NIFTY.
    Range = 09:15-09:40 bars. From the 09:45 bar to the 13:00 bar, the first close above the range high (long) or
    below the range low (short), taken only if NIFTY is up (long) / down (short) since its open at that bar.
    Stop = range width (skip if the range is < 0.4% or > 2.5% of price). Target 2R, else exit 15:05."""
    out = []
    for sym, (days, _) in data.items():
        for d, day in days.items():
            nd = nifty.get(d)
            i0, i1 = _idx(day, (9, 45)), _idx(day, (13, 0))
            if nd is None or i0 is None or i1 is None:
                continue
            orh, orl = day["high"].iloc[:i0].max(), day["low"].iloc[:i0].min()
            width = orh - orl
            px = day["close"].iloc[i0]
            if not (0.004 <= width / px <= 0.025):
                continue
            n_open = nd["open"].iloc[0]
            n_close = nd["close"]
            for i in range(i0, i1 + 1):
                c = day["close"].iloc[i]
                ni = n_close.get(day.index[i], np.nan)
                if not np.isfinite(ni):
                    continue
                up, dn = c > orh, c < orl
                if (up and ni > n_open) or (dn and ni < n_open):
                    t = trade(d, sym, day, i, up, width, "A_orb_trend", target_r=2.0)
                    if t:
                        out.append(t)
                    break
                if up or dn:
                    break  # first breakout went against NIFTY: no trade this stock today
    return out


def rel_strength(data, nifty):
    """B. Market intraday momentum + relative strength. At the 10:15 bar close: NIFTY return since its open r.
    If |r| >= 0.25%: r > 0 -> long the 3 stocks with the highest return since their open; r < 0 -> short the 3
    weakest. Stop = 0.5 x daily ATR (14-day, known at the open). No target; exit 15:05."""
    out = []
    by_day = defaultdict(list)
    for sym, (days, atr) in data.items():
        for d, day in days.items():
            i = _idx(day, (10, 15))
            if i is None:
                continue
            by_day[d].append((float(day["close"].iloc[i] / day["open"].iloc[0] - 1), sym, day, i, atr.get(d, (np.nan,))[0]))
    for d, rows in sorted(by_day.items()):
        nd = nifty.get(d)
        if nd is None:
            continue
        j = _idx(nd, (10, 15))
        if j is None:
            continue
        r = nd["close"].iloc[j] / nd["open"].iloc[0] - 1
        if abs(r) < 0.0025:
            continue
        rows = [x for x in rows if np.isfinite(x[4])]
        pick = sorted(rows, key=lambda x: x[0], reverse=r > 0)[:3]
        for ret, sym, day, i, a in pick:
            t = trade(d, sym, day, i, r > 0, 0.5 * a, "B_rel_strength")
            if t:
                out.append(t)
    return out


def gap_fade(data, nifty):
    """C. Fade a large gap that fails. Gap = open / previous close - 1, |gap| >= 1.5%. At the 09:45 bar close, if
    price is back beyond the open (gap up: close < open; gap down: close > open), trade toward the previous close.
    Stop = distance to the session's extreme so far (high for a short, low for a long), at least 0.4% of price.
    Target = previous close (gap filled), else exit 15:05."""
    out = []
    for sym, (days, atr) in data.items():
        for d, day in days.items():
            prev = atr.get(d, (None, None))[1]
            i = _idx(day, (9, 45))
            if not prev or i is None or not np.isfinite(prev):
                continue
            opn = day["open"].iloc[0]
            gap = opn / prev - 1
            if abs(gap) < 0.015:
                continue
            c = day["close"].iloc[i]
            if gap > 0 and c < opn:
                sd = max(day["high"].iloc[:i + 1].max() - c, 0.004 * c)
                t = trade(d, sym, day, i, False, sd, "C_gap_fade", target_px=prev)
            elif gap < 0 and c > opn:
                sd = max(c - day["low"].iloc[:i + 1].min(), 0.004 * c)
                t = trade(d, sym, day, i, True, sd, "C_gap_fade", target_px=prev)
            else:
                continue
            if t:
                out.append(t)
    return out


def current_with_nifty(cache, nifty):
    """D. The current engine's trades (from engine.analyze's cache), kept only when they agree with NIFTY's
    direction since its open at the signal bar (long only if NIFTY is up, short only if down)."""
    out = []
    if not cache or not os.path.exists(cache):
        return out
    with open(cache) as fh:
        trades = json.load(fh)["trades"]
    for t in trades:
        from datetime import date as _date
        nd = nifty.get(_date.fromisoformat(t["date"]))
        if nd is None:
            continue
        hh, mm = map(int, t["signal_time"].split(":"))
        j = _idx(nd, (hh, mm))
        if j is None:
            continue
        up = nd["close"].iloc[j] > nd["open"].iloc[0]
        if (t["direction"] == "LONG") == up:
            out.append({**t, "idea": "D_current_nifty_aligned"})
    return out


# ---------------- round 2 (parameters fixed 2026-11-19, before any result) ----------------
def _prev_days(days, d, n):
    """The n sessions before d (oldest first), from this stock's own history."""
    keys = sorted(days)
    k = keys.index(d)
    return [days[x] for x in keys[max(0, k - n):k]]


def idio_reversal(data, nifty):
    """E. Fade a stock-specific overreaction. At the 11:00, 11:30 ... 14:00 bar closes, NIFTY within 0.3% of its
    open and the stock at least 1.0 daily ATR away from its own open: trade back toward the session VWAP (target),
    stop 0.5 daily ATR, else exit 15:05. First qualifying checkpoint per stock per day."""
    out = []
    checks = [(11, 0), (11, 30), (12, 0), (12, 30), (13, 0), (13, 30), (14, 0)]
    for sym, (days, atr) in data.items():
        for d, day in days.items():
            nd, a = nifty.get(d), atr.get(d, (np.nan,))[0]
            if nd is None or not np.isfinite(a):
                continue
            tp = (day["high"] + day["low"] + day["close"]) / 3
            vwap = (tp * day["volume"]).cumsum() / day["volume"].cumsum().replace(0, np.nan)
            opn, n_open = day["open"].iloc[0], nd["open"].iloc[0]
            for hm in checks:
                i, j = _idx(day, hm), _idx(nd, hm)
                if i is None or j is None or abs(nd["close"].iloc[j] / n_open - 1) >= 0.003:
                    continue
                c, v = day["close"].iloc[i], vwap.iloc[i]
                m = (c - opn) / a
                if m <= -1.0 and v > c:
                    t = trade(d, sym, day, i, True, 0.5 * a, "E_idio_reversal", target_px=float(v))
                elif m >= 1.0 and v < c:
                    t = trade(d, sym, day, i, False, 0.5 * a, "E_idio_reversal", target_px=float(v))
                else:
                    continue
                if t:
                    out.append(t)
                break
    return out


def nr7_breakout(data, nifty):
    """F. Volatility expansion after contraction. Yesterday's range (high - low) is the narrowest of the last 7
    sessions. From the 09:30 bar to the 13:00 bar, the first close above yesterday's high (long) or below
    yesterday's low (short). Stop = yesterday's range (skip if < 0.5% of price). No target; exit 15:05."""
    out = []
    for sym, (days, _) in data.items():
        for d, day in days.items():
            prev = _prev_days(days, d, 7)
            if len(prev) < 7:
                continue
            ranges = [p["high"].max() - p["low"].min() for p in prev]
            if ranges[-1] > min(ranges):
                continue
            ph, pl, rng = prev[-1]["high"].max(), prev[-1]["low"].min(), ranges[-1]
            i0, i1 = _idx(day, (9, 30)), _idx(day, (13, 0))
            if i0 is None or i1 is None or rng < 0.005 * day["close"].iloc[i0]:
                continue
            for i in range(i0, i1 + 1):
                c = day["close"].iloc[i]
                if c > ph or c < pl:
                    t = trade(d, sym, day, i, c > ph, rng, "F_nr7_breakout")
                    if t:
                        out.append(t)
                    break
    return out


def gap_and_go(data, nifty):
    """G. Gap continuation with the market. Stock gap >= 1% and NIFTY gapped the same way. At the 09:25 bar close,
    if price is still beyond the open in the gap's direction, trade with the gap. Stop = distance to the session's
    opposite extreme so far (at least 0.5% of price). No target; exit 15:05."""
    out = []
    for sym, (days, atr) in data.items():
        for d, day in days.items():
            prev, nd = atr.get(d, (None, None))[1], nifty.get(d)
            i = _idx(day, (9, 25))
            if not prev or not np.isfinite(prev) or nd is None or i is None:
                continue
            nprev = _prev_days(nifty, d, 1)
            if not nprev:
                continue
            gap, ngap = day["open"].iloc[0] / prev - 1, nd["open"].iloc[0] / nprev[0]["close"].iloc[-1] - 1
            if abs(gap) < 0.01 or gap * ngap <= 0:
                continue
            c, opn = day["close"].iloc[i], day["open"].iloc[0]
            long_ = gap > 0
            if (c <= opn) if long_ else (c >= opn):
                continue
            ext = c - day["low"].iloc[:i + 1].min() if long_ else day["high"].iloc[:i + 1].max() - c
            t = trade(d, sym, day, i, long_, max(ext, 0.005 * c), "G_gap_and_go")
            if t:
                out.append(t)
    return out


def rvol_drive(data, nifty):
    """H. Opening drive on unusual volume. First 15 minutes (09:15-09:25 bars): volume >= 2x the average of the same
    window over the previous 20 sessions, and the move from the open to the 09:25 close >= 0.5%. Trade in that
    direction at the 09:25 close; stop at the opposite end of the first 15 minutes (at least 0.4%). Exit 15:05."""
    out = []
    for sym, (days, _) in data.items():
        for d, day in days.items():
            i = _idx(day, (9, 25))
            prev = _prev_days(days, d, 20)
            if i is None or len(prev) < 20:
                continue
            base = np.mean([p["volume"].iloc[:3].sum() for p in prev])
            vol = day["volume"].iloc[:i + 1].sum()
            c, opn = day["close"].iloc[i], day["open"].iloc[0]
            mv = c / opn - 1
            if base <= 0 or vol < 2 * base or abs(mv) < 0.005:
                continue
            long_ = mv > 0
            ext = c - day["low"].iloc[:i + 1].min() if long_ else day["high"].iloc[:i + 1].max() - c
            t = trade(d, sym, day, i, long_, max(ext, 0.004 * c), "H_rvol_drive")
            if t:
                out.append(t)
    return out


def report(name, trades, train):
    if not trades:
        print(f"  {name:24s} no trades"); return
    tr = [t for t in trades if t["date"] in train]; te = [t for t in trades if t["date"] not in train]
    m = lambda x: np.mean([t["net_r"] for t in x]) if x else float("nan")
    ci = evaluate.bootstrap_ci(te) or [float("nan")] * 2
    gross = np.mean([t["r_multiple"] for t in trades]); cost = np.mean([t["cost_r"] for t in trades])
    win = 100 * np.mean([t["net_r"] > 0 for t in trades])
    ok = len(trades) >= 150 and m(tr) > 0 and m(te) > 0 and ci[0] > 0
    years = defaultdict(list)
    for t in trades:
        years[t["date"][:4]].append(t["net_r"])
    ys = " ".join(f"{y}:{np.mean(v):+.2f}" for y, v in sorted(years.items()))
    print(f"  {name:24s} n={len(trades):5d} win={win:4.1f}% gross={gross:+.3f} cost={cost:.3f} | "
          f"TRAIN n={len(tr)} {m(tr):+.3f} | TEST n={len(te)} {m(te):+.3f} ci=[{ci[0]:+.3f},{ci[1]:+.3f}] | {ys} | {'PASS' if ok else 'fail'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", help="engine.analyze trades cache, for idea D")
    ap.add_argument("--only", help="letters of the ideas to run, e.g. EFGH (default: all)")
    a = ap.parse_args()
    nifty_raw = archive.load_bars(INDEX_SYMBOLS["NIFTY"])
    if nifty_raw is None:
        raise SystemExit("No NIFTY bars in ARCHIVE_DIR.")
    nifty = _days(nifty_raw)
    data = {}
    for u in UNIVERSE:
        df = archive.load_bars(u[0] + ".NS")
        if df is not None:
            days = _days(df)
            data[u[0]] = (days, _daily_atr(days))
    sessions = sorted(nifty)
    train = {d.isoformat() for d in sessions[:int(len(sessions) * evaluate.TRAIN_FRACTION)]}
    print(f"SIGNALS LAB · {sessions[0]}..{sessions[-1]} · {len(sessions)} sessions · {len(data)} stocks · "
          f"TRAIN to {max(train)} · pass = TRAIN>0, TEST>0, TEST CI low>0, n>=150")
    ideas = {"A_orb_trend": orb_trend, "B_rel_strength": rel_strength, "C_gap_fade": gap_fade,
             "E_idio_reversal": idio_reversal, "F_nr7_breakout": nr7_breakout, "G_gap_and_go": gap_and_go,
             "H_rvol_drive": rvol_drive}
    for name, fn in ideas.items():
        if not a.only or name[0] in a.only:
            report(name, fn(data, nifty), train)
    if not a.only or "D" in a.only:
        report("D_current_nifty_aligned", current_with_nifty(a.cache, nifty), train)


if __name__ == "__main__":
    main()
