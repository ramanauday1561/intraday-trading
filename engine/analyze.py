"""Where does the strategy lose? Break the backtest's live-rule trades down by setup, time, stock and more.

    ARCHIVE_DIR=/var/lib/intraday-history python -m engine.analyze --offline --cache trades.json

Runs the backtest once (current settings, live selection: first signal per stock per day + day rules) and saves the
selected trades to --cache; later runs with the same --cache reuse them (seconds instead of hours).
For every group it prints trades, win rate, mean net R (after real charges) with a session-bootstrap 95% CI, and the
mean on the first 60% of sessions (TRAIN) and the last 40% (TEST). A group is only a real pattern if it points the
same way in both halves: with dozens of groups, some will look extreme by chance.
"""
import argparse
import contextlib
import json
import os
import sys
from collections import defaultdict
from datetime import date

import numpy as np

from . import evaluate

KEEP = ("date", "signal_time", "symbol", "direction", "setup", "conviction", "composite", "regime", "reason",
        "exit_time", "net_r", "r_multiple", "cost_r", "stop_distance", "entry", "news_score")


def load_trades(offline, hourly, cache):
    if cache and os.path.exists(cache):
        with open(cache) as fh:
            return json.load(fh)
    from . import backtest, market
    if offline:
        market.fetch_many = lambda *a, **k: {}
    res = backtest.run(hourly=hourly, fetch_news=not offline, keep_candidates=True)
    sel = evaluate.apply_filters(res["_cands"], day_rules=True)
    days = [d.isoformat() for d in res["_days"]]
    out = {"days": days, "trades": [{k: t.get(k) for k in KEEP} for t in sel]}
    if cache:
        with open(cache, "w") as fh:
            json.dump(out, fh)
    return out


def _bucket(v, edges, fmt="{:g}"):
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{fmt.format(lo)}..{fmt.format(hi)}"
    return f">={fmt.format(edges[-1])}" if v >= edges[-1] else f"<{fmt.format(edges[0])}"


GROUPS = {
    "direction": lambda t: t["direction"],
    "setup": lambda t: t["setup"],
    "regime": lambda t: t["regime"],
    "conviction": lambda t: t["conviction"],
    "signal time": lambda t: t["signal_time"][:2] + ":" + ("00" if int(t["signal_time"][3:]) < 30 else "30"),
    "|composite|": lambda t: _bucket(abs(t["composite"] or 0), [0, 0.3, 0.4, 0.5, 0.6, 0.8]),
    "exit reason": lambda t: t["reason"],
    "cost (R)": lambda t: _bucket(t["cost_r"], [0, 0.04, 0.06, 0.08, 0.12, 0.2]),
    "stop % of price": lambda t: _bucket(100 * t["stop_distance"] / t["entry"], [0, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0]),
    "weekday": lambda t: date.fromisoformat(t["date"]).strftime("%a"),
    "year": lambda t: t["date"][:4],
    "trade # of day": None,  # filled in main (order within the day)
    "symbol": lambda t: t["symbol"].replace(".NS", ""),
}


def row(label, ts, train):
    r = np.array([t["net_r"] for t in ts])
    tr = [t["net_r"] for t in ts if t["date"] in train]
    te = [t["net_r"] for t in ts if t["date"] not in train]
    ci = evaluate.bootstrap_ci(ts)
    m = lambda x: f"{np.mean(x):+.3f}" if x else "   -  "
    flag = ""
    if ci and ci[1] < 0:
        flag = "  LOSS"
    elif ci and ci[0] > 0:
        flag = "  GAIN"
    if tr and te and np.sign(np.mean(tr)) != np.sign(np.mean(te)):
        flag += " (halves disagree)"
    gross = np.mean([t["r_multiple"] for t in ts])
    cost = np.mean([t["cost_r"] for t in ts])
    ci_s = f"[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else "[   n/a   ]"
    return (f"  {label[:14]:14s} n={len(ts):5d} win={100 * (r > 0).mean():4.1f}% net={r.mean():+.3f} {ci_s} "
            f"gross={gross:+.3f} cost={cost:.3f} | train={m(tr)} test={m(te)}{flag}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--cache", help="JSON file for the selected trades (written once, then reused)")
    ap.add_argument("--min-trades", type=int, default=30, help="hide groups smaller than this (symbol table)")
    a = ap.parse_args()
    with contextlib.redirect_stdout(sys.stderr):  # the backtest's per-day progress goes to the log, not the report
        data = load_trades(a.offline, a.hourly, a.cache)
    days, trades = data["days"], data["trades"]
    train = set(days[:int(len(days) * evaluate.TRAIN_FRACTION)])
    by_day = defaultdict(list)
    for t in sorted(trades, key=lambda t: (t["date"], t["signal_time"])):
        by_day[t["date"]].append(t)
        t["_nth"] = str(min(len(by_day[t["date"]]), 4)) + ("+" if len(by_day[t["date"]]) >= 4 else "")
    GROUPS["trade # of day"] = lambda t: t["_nth"]

    print(f"ANALYSIS · {'1h' if a.hourly else '5m'} · {days[0]}..{days[-1]} · {len(days)} sessions · {len(trades)} trades")
    print("  (net = mean R per trade after real charges, [95% CI]; gross = before charges; LOSS/GAIN = CI excludes 0)")
    print(row("ALL", trades, train))
    for name, key in GROUPS.items():
        groups = defaultdict(list)
        for t in trades:
            groups[key(t)].append(t)
        print(f"BY {name.upper()}")
        items = sorted(groups.items(), key=lambda kv: kv[0])
        if name == "symbol":  # long table: only sizeable groups, worst and best first
            items = sorted(((k, v) for k, v in groups.items() if len(v) >= a.min_trades),
                           key=lambda kv: np.mean([t["net_r"] for t in kv[1]]))
            items = items[:8] + [("...", [])] + items[-8:] if len(items) > 16 else items
        for k, v in items:
            print(row(str(k), v, train) if v else "  ...")


if __name__ == "__main__":
    main()
