"""Cost-aware filtering and chronological walk-forward evaluation of backtest candidates.

Rules are fixed in advance: a small, declared grid of filters is scored on the TRAIN sessions
(first 60%); the single best eligible config is then judged once on the TEST sessions (last 40%).
Every config's train and test numbers are reported, along with how many were tried, so selection
bias is visible rather than hidden."""
import itertools
from collections import defaultdict

import numpy as np

CONV_MIN = (0, 50, 65, 80)
MAX_COST_R = (None, 0.25, 0.15)      # skip trades whose round-trip cost exceeds this many R
MAX_PER_DAY = (None, 3)
MIN_TRAIN_TRADES = 30
TRAIN_FRACTION = 0.6


def apply_filters(cands, conv_min=0, max_cost_r=None, max_per_day=None):
    """Time-ordered selection: first qualifying signal per stock per day, optional daily cap."""
    by_day = defaultdict(list)
    for t in cands:
        by_day[t["date"]].append(t)
    out = []
    for date in sorted(by_day):
        taken, n = set(), 0
        for t in sorted(by_day[date], key=lambda x: (x["signal_time"], -x["conviction"])):
            if t["conviction"] < conv_min or (max_cost_r is not None and t["cost_r"] > max_cost_r):
                continue
            if t["symbol"] in taken:
                continue
            if max_per_day is not None and n >= max_per_day:
                break
            out.append(t); taken.add(t["symbol"]); n += 1
    return out


def metrics(trades):
    if not trades:
        return {"trades": 0}
    r = np.array([t["net_r"] for t in trades])
    win, loss = r[r > 0], r[r <= 0]
    return {"trades": len(r), "win_rate_pct": round(100 * float((r > 0).mean()), 1), "avg_net_r": round(float(r.mean()), 3),
            "total_net_r": round(float(r.sum()), 1),
            "profit_factor": round(float(win.sum() / -loss.sum()), 2) if loss.sum() < 0 else None}


def bootstrap_ci(trades, n=2000, seed=0):
    """95% CI of mean net R, resampling whole sessions (trades within a day are correlated)."""
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t["net_r"])
    days = list(by_day.values())
    if len(days) < 5:
        return None
    rng = np.random.default_rng(seed)
    sums = np.array([sum(d) for d in days]); cnt = np.array([len(d) for d in days])
    means = []
    for _ in range(n):
        idx = rng.integers(0, len(days), len(days))
        c = cnt[idx].sum()
        if c:
            means.append(sums[idx].sum() / c)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return [round(float(lo), 3), round(float(hi), 3)]


def walk_forward(cands, sessions):
    """sessions: sorted list of date objects used in the test."""
    cut = int(len(sessions) * TRAIN_FRACTION)
    train_days = {d.isoformat() for d in sessions[:cut]}
    test_days = {d.isoformat() for d in sessions[cut:]}
    if not train_days or not test_days:
        return None
    rows = []
    for cm, mc, md in itertools.product(CONV_MIN, MAX_COST_R, MAX_PER_DAY):
        sel = apply_filters(cands, cm, mc, md)
        tr = [t for t in sel if t["date"] in train_days]
        te = [t for t in sel if t["date"] in test_days]
        rows.append({"config": {"min_conviction": cm, "max_cost_r": mc, "max_per_day": md},
                     "train": metrics(tr), "test": metrics(te), "_te": te})
    eligible = [r for r in rows if r["train"]["trades"] >= MIN_TRAIN_TRADES]
    best = max(eligible, key=lambda r: r["train"]["avg_net_r"]) if eligible else None
    base = next(r for r in rows if r["config"] == {"min_conviction": 0, "max_cost_r": None, "max_per_day": None})
    out = {"train_sessions": [min(train_days), max(train_days), len(train_days)],
           "test_sessions": [min(test_days), max(test_days), len(test_days)],
           "configs_tried": len(rows), "baseline_test": base["test"], "baseline_test_ci95": bootstrap_ci(base["_te"]),
           "selected": None, "all": [{k: v for k, v in r.items() if k != "_te"} for r in rows]}
    if best:
        ci = bootstrap_ci(best["_te"])
        verdict = "NO_EDGE_DEMONSTRATED"
        if ci and ci[0] > 0:
            verdict = "EDGE_SUPPORTED_OUT_OF_SAMPLE"
        elif best["test"]["trades"] and best["test"]["avg_net_r"] > 0:
            verdict = "POSITIVE_BUT_NOT_SIGNIFICANT"
        out["selected"] = {"config": best["config"], "train": best["train"], "test": best["test"], "test_ci95": ci, "verdict": verdict}
    return out


HYPOTHESIS_MIN_TRADES = 100


def hypothesis_report(trades, in_sample):
    """Pre-registered hypothesis H1 (see preregistration.md): trades that OPPOSE a material news signal
    (|score| > 0.2) have mean net R > 0. SUPPORTED needs >=100 trades and a 95% session-bootstrap CI above 0."""
    opp = [t for t in trades if t.get("news_bucket") == "news_opposed"]
    rest = [t for t in trades if t.get("news_bucket") != "news_opposed"]
    ci = bootstrap_ci(opp)
    if in_sample:
        verdict = "IN_SAMPLE_DEVELOPMENT_DATA_NOT_A_TEST"
    elif len(opp) < HYPOTHESIS_MIN_TRADES:
        verdict = "INSUFFICIENT_DATA"
    else:
        verdict = "SUPPORTED" if ci and ci[0] > 0 else "NOT_SUPPORTED"
    return {"in_sample": in_sample, "min_trades_required": HYPOTHESIS_MIN_TRADES, "opposed": metrics(opp), "opposed_ci95": ci,
            "other_trades": metrics(rest), "verdict": verdict}
