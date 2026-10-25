"""Causal multi-day replay over all 5-minute history Yahoo provides (~60 sessions).

python -m engine.backtest [--date YYYY-MM-DD] [--no-news-fetch]

At each 15-min checkpoint (10:00-14:30 IST) the engine sees only bars up to that moment and only
headlines published BEFORE that moment (Google News RSS items carry their own pubDate). Entry is the
next bar's open; stop/target are walked bar by bar (stop wins ties); open trades exit at 15:15.
Each stock takes at most one trade per day. Two variants run on identical data: WITH news and
WITHOUT news, so the contribution of the fundamental pillar is measurable."""
import argparse
import json
import math
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import demo as demomod
from . import news as newsmod
from .config import COST_PCT, INDEX_SYMBOLS, IST, LEVERAGE, UNIVERSE
from .pipeline import _index_corr, to_json
from .scoring import analyse_stock

NEUTRAL_NEWS = {"score": 0.0, "items": [], "event": None, "tier1_earnings": False, "positive_catalyst": 0.0,
                "sue_proxy": 0.0, "divergence": 0.0, "engine": "none"}
RISK_LEVELS = (1.0, 50.0)


def _frames(demo, date=None):
    syms = [u[0] for u in UNIVERSE]
    idx_syms = sorted(set(INDEX_SYMBOLS.values()) | {u[3] for u in UNIVERSE})
    out = {}
    if demo:
        now = datetime(2026, 10, 23, 12, 0, tzinfo=IST)
        for s in syms + idx_syms:
            m = demomod.make_symbol(s, now, "breakout" if s in idx_syms else None)
            out[s] = (pd.concat([m["hist"], m["today"]]), m["daily"])
        return out
    from .market import fetch_many
    i5 = fetch_many([s + ".NS" for s in syms] + idx_syms, "60d", "5m")
    d1 = fetch_many([s + ".NS" for s in syms] + idx_syms, "1y", "1d")
    for s in syms:
        out[s] = (i5.get(s + ".NS"), d1.get(s + ".NS"))
    for s in idx_syms:
        out[s] = (i5.get(s), d1.get(s))
    return out


def _fetch_news(start, end, workers=4):
    """Weekly windows per symbol -> list of items (each with its own publication timestamp)."""
    windows, d = [], start
    while d <= end:
        windows.append((d, min(d + timedelta(days=7), end + timedelta(days=1)))); d += timedelta(days=7)
    jobs = [(sym, name, a, b) for sym, name, *_ in UNIVERSE for a, b in windows]
    def one(j):
        sym, name, a, b = j
        return sym, newsmod.fetch_symbol_news_range(name, sym, a, b)
    items = defaultdict(list)
    with ThreadPoolExecutor(workers) as ex:
        for sym, got in ex.map(one, jobs):
            items[sym] += got
    return {k: list({i["title"]: i for i in v}.values()) for k, v in items.items()}


def simulate(sig, bars_after, exit_hhmm=(15, 15)):
    """Walk bars after the signal. Entry = next bar open. Returns trade dict or None."""
    if bars_after is None or len(bars_after) < 2:
        return None
    long_ = sig["direction"] == "LONG"
    sd, rr = sig["execution"]["stop_distance"], sig["execution"]["rr"]
    entry = float(bars_after["open"].iloc[0])
    stop, target = (entry - sd, entry + rr * sd) if long_ else (entry + sd, entry - rr * sd)
    exit_px, reason = float(bars_after["close"].iloc[-1]), "EOD"
    for ts, b in bars_after.iterrows():
        if (ts.hour, ts.minute) >= exit_hhmm:
            exit_px, reason = float(b["open"]), "EOD"
            break
        hit_stop = b["low"] <= stop if long_ else b["high"] >= stop
        hit_tgt = b["high"] >= target if long_ else b["low"] <= target
        if hit_stop:
            exit_px, reason = stop, "STOP"; break
        if hit_tgt:
            exit_px, reason = target, "TARGET"; break
    per_share = (exit_px - entry) if long_ else (entry - exit_px)
    qty = sig["execution"]["qty"]
    return {"symbol": sig["symbol"], "direction": sig["direction"], "setup": sig["setup"], "conviction": sig["conviction"],
            "regime": sig["regime"], "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
            "exit": round(exit_px, 2), "reason": reason, "qty": qty, "stop_distance": sd, "size_mult": sig["risk"]["size_multiplier"],
            "r_multiple": round(per_share / sd, 3), "per_share": per_share,
            "gross_pnl": round(per_share * qty, 0), "costs": round(entry * qty * COST_PCT / 100, 0),
            "net_pnl": round(per_share * qty - entry * qty * COST_PCT / 100, 0)}


def _pnl_at(t, risk_pct, capital):
    """Re-size a trade for another risk % (same leverage cap)."""
    q = min(int(capital * risk_pct / 100 * t["size_mult"] / t["stop_distance"]), int(capital * LEVERAGE / t["entry"]))
    return t["per_share"] * q - t["entry"] * q * COST_PCT / 100


def _stats(trades, capital, days):
    if not trades:
        return {"trades": 0}
    r = np.array([t["r_multiple"] for t in trades])
    win, loss = r[r > 0], r[r <= 0]
    daily = defaultdict(lambda: {"trades": 0, "r": 0.0, **{f"pnl_{int(x)}": 0.0 for x in RISK_LEVELS}})
    for t in trades:
        d = daily[t["date"]]
        d["trades"] += 1; d["r"] += t["r_multiple"]
        for x in RISK_LEVELS:
            d[f"pnl_{int(x)}"] += _pnl_at(t, x, capital)
    cum = np.cumsum([daily[k]["r"] for k in sorted(daily)])
    mdd_r = float((np.maximum.accumulate(cum) - cum).max()) if len(cum) else 0.0
    day_r = np.array([daily[k]["r"] for k in sorted(daily)])
    out = {"trades": len(trades), "trading_days_with_trades": len(daily), "days_tested": days,
           "win_rate_pct": round(100 * len(win) / len(r), 1), "avg_r": round(float(r.mean()), 3), "total_r": round(float(r.sum()), 1),
           "profit_factor": round(float(win.sum() / -loss.sum()), 2) if loss.sum() < 0 else None,
           "max_drawdown_r": round(mdd_r, 1), "daily_r_sharpe": round(float(day_r.mean() / day_r.std() * math.sqrt(252)), 2) if len(day_r) > 2 and day_r.std() > 0 else None,
           "avg_r_se": round(float(r.std(ddof=1) / math.sqrt(len(r))), 3) if len(r) > 1 else None}
    for x in RISK_LEVELS:
        pn = [daily[k][f"pnl_{int(x)}"] for k in sorted(daily)]
        eq = np.cumsum(pn)
        out[f"net_pnl_risk{int(x)}"] = round(float(eq[-1]), 0)
        out[f"return_pct_risk{int(x)}"] = round(100 * float(eq[-1]) / capital, 1)
        out[f"worst_day_pct_risk{int(x)}"] = round(100 * float(min(pn)) / capital, 1)
    by = lambda key: {k: {"trades": len(v), "avg_r": round(float(np.mean([t["r_multiple"] for t in v])), 3),
                          "win_rate_pct": round(100 * sum(t["r_multiple"] > 0 for t in v) / len(v), 1)}
                      for k, v in _group(trades, key).items()}
    out["by_direction"], out["by_setup"], out["by_regime"] = by("direction"), by("setup"), by("regime")
    out["by_news"] = by("news_bucket")
    out["daily"] = [{"date": k, **{kk: round(vv, 2) for kk, vv in daily[k].items()}} for k in sorted(daily)]
    return out


def _group(trades, key):
    g = defaultdict(list)
    for t in trades:
        g[t[key]].append(t)
    return g


def run(asof=(10, 0), capital=100000.0, demo=False, only_date=None, fetch_news=True):
    frames = _frames(demo)
    n5, nd_all = frames["^NSEI"]
    if n5 is None or nd_all is None:
        from . import market
        print("data fetch errors:", {k: len(v) for k, v in market.ERRORS.items()})
        raise SystemExit("No Nifty data (Yahoo unreachable).")
    by_day = lambda df: {d: g for d, g in df.groupby(df.index.date)} if df is not None else {}
    stocks5 = {s: by_day(frames[s][0]) for s, *_ in UNIVERSE if frames[s][0] is not None and frames[s][1] is not None}
    idx5 = {k: by_day(v[0]) for k, v in frames.items() if k not in stocks5}
    all_days = sorted(d for d, g in idx5["^NSEI"].items() if len(g) >= 60)  # full sessions only
    days = [d for d in all_days[1:]]  # need >=1 prior session for history
    if only_date:
        days = [d for d in days if d == only_date]
        if not days:
            raise SystemExit(f"{only_date} not available in Yahoo 5-minute history.")
    news_items = {}
    if fetch_news and not demo:
        news_items = _fetch_news(days[0] - timedelta(days=2), days[-1])
        print("news headlines fetched:", sum(len(v) for v in news_items.values()))
    vd = frames["^INDIAVIX"][1]
    trades = {"with_news": [], "without_news": []}
    blockers = defaultdict(int)
    news_used = 0
    for date in days:
        prior_days = [d for d in all_days if d < date][-5:]
        nt = idx5["^NSEI"][date]
        nd = frames["^NSEI"][1]; nd = nd[[d < date for d in nd.index.date]]
        if len(nd) < 20:
            continue
        sdaily = {s: frames[s][1][[d < date for d in frames[s][1].index.date]] for s in stocks5}
        corr = _index_corr({s: v for s, v in sdaily.items() if len(v) >= 20}, nd)
        vix_prev = float(vd["close"][[d < date for d in vd.index.date]].iloc[-1]) if vd is not None and len(vd[[d < date for d in vd.index.date]]) else 14.0
        vix_day = idx5["^INDIAVIX"].get(date)
        base = datetime(date.year, date.month, date.day, tzinfo=IST)
        taken = {"with_news": set(), "without_news": set()}
        for t_ in range(asof[0] * 60 + asof[1], 14 * 60 + 31, 15):
            cut = pd.Timestamp(base + timedelta(minutes=t_))
            ntc = nt[nt.index <= cut]
            if len(ntc) < 3:
                continue
            vix = float(vix_day[vix_day.index <= cut]["close"].iloc[-1]) if vix_day is not None and len(vix_day[vix_day.index <= cut]) else vix_prev
            nifty_chg = 100 * (float(ntc["close"].iloc[-1]) / float(nd["close"].iloc[-1]) - 1)
            for sym, name, sector, sidx, mcap in UNIVERSE:
                if sym not in stocks5 or date not in stocks5[sym] or len(sdaily[sym]) < 20:
                    continue
                seen_all = stocks5[sym][date]
                seen, later = seen_all[seen_all.index <= cut], seen_all[seen_all.index > cut]
                if len(seen) < 3 or not len(prior_days):
                    continue
                hist = pd.concat([stocks5[sym][d] for d in prior_days if d in stocks5[sym]])
                if len(hist) < 30:
                    continue
                sec_chg = nifty_chg
                s_day = idx5.get(sidx, {}).get(date)
                sd_ = frames[sidx][1] if sidx in frames else None
                if s_day is not None and sd_ is not None:
                    s_seen = s_day[s_day.index <= cut]; sd_prior = sd_[[d < date for d in sd_.index.date]]
                    if len(s_seen) and len(sd_prior):
                        sec_chg = 100 * (float(s_seen["close"].iloc[-1]) / float(sd_prior["close"].iloc[-1]) - 1)
                nw = newsmod.analyse_symbol(sym, name, mcap, news_items.get(sym, []), now=cut.tz_convert("UTC").to_pydatetime()) \
                    if news_items.get(sym) else NEUTRAL_NEWS
                for variant, nws in (("with_news", nw), ("without_news", NEUTRAL_NEWS)):
                    if sym in taken[variant] or (variant == "without_news" and nws is nw and False):
                        continue
                    ctx = {"symbol": sym, "name": name, "sector": sector, "today": seen, "hist": hist, "daily": sdaily[sym],
                           "prev_close": float(sdaily[sym]["close"].iloc[-1]), "now": cut.to_pydatetime(), "phase": "ACTIVE", "vix": vix,
                           "vix_chg": 100 * (vix / vix_prev - 1), "index_corr": corr, "index_gex": None,
                           "nifty": float(ntc["close"].iloc[-1]), "nifty_open": float(nt["open"].iloc[0]), "nifty_chg": nifty_chg,
                           "sector_chg": sec_chg, "news": nws, "options": None, "fut_oi_chg_pct": None, "capital": capital, "risk_pct": 50.0}
                    sig = analyse_stock(ctx)
                    if variant == "with_news":
                        for bk in sig["risk"]["blockers"]:
                            blockers[bk["code"]] += 1
                    if sig["direction"] == "NO_TRADE":
                        continue
                    tr = simulate(sig, later)
                    if tr:
                        tr.update({"date": date.isoformat(), "signal_time": cut.strftime("%H:%M"), "news_score": nws["score"],
                                   "news_bucket": "news_aligned" if (abs(nws["score"]) > 0.2 and (nws["score"] > 0) == (tr["direction"] == "LONG"))
                                   else "news_opposed" if abs(nws["score"]) > 0.2 else "no_news",
                                   "contradiction": (sig["contradiction"] or {}).get("type")})
                        trades[variant].append(tr); taken[variant].add(sym)
                        if variant == "with_news" and abs(nws["score"]) > 0.2:
                            news_used += 1
        print(date, {k: len(v) for k, v in trades.items()}, flush=True)
    res = {"mode": "demo" if demo else "live", "period": {"from": days[0].isoformat(), "to": days[-1].isoformat(), "sessions": len(days)},
           "scan": f"every 15 min {asof[0]:02d}:{asof[1]:02d}-14:30 IST, first signal per stock per day",
           "capital": capital, "risk_levels_pct": list(RISK_LEVELS), "news_headlines": sum(len(v) for v in news_items.values()),
           "trades_using_news": news_used, "blocker_counts": dict(blockers)}
    for k in trades:
        res[k] = _stats(trades[k], capital, len(days))
    res["trades"] = [{kk: vv for kk, vv in t.items() if kk not in ("per_share",)} for t in sorted(trades["with_news"], key=lambda t: (t["date"], t["signal_time"]))][-400:]
    res["caveats"] = [
        "Headlines come from Google News RSS filtered by each item's own publication time; its historical archive can be incomplete, so news coverage on old days is thinner than live.",
        "Sentiment uses a finance lexicon (not FinBERT) unless USE_FINBERT=1. Earnings surprise is a headline beat/miss proxy.",
        "NSE derivatives (GEX, OI, IV skew) and pre-open depth are not replayable; those inputs are omitted.",
        "Yahoo serves only ~60 sessions of 5-minute data: a small sample. Treat avg R +/- its standard error, not the P&L, as the evidence.",
        "Entry = next 5-min bar open; stop wins ties inside a bar; exit by 15:15; costs %.2f%% round trip; max one trade per stock per day." % COST_PCT,
        "Parameters were not tuned on this data, but the same rules produced these results, so beware over-reading any lucky streak.",
        "P&L at 50%% risk is shown only for comparison: it is capped at %dx notional and is not a recommended setting." % LEVERAGE]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--time", default="10:00")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-news-fetch", action="store_true")
    ap.add_argument("--out", default="data/backtest.json")
    a = ap.parse_args()
    hh, mm = map(int, a.time.split(":"))
    only = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else None
    res = run((hh, mm), a.capital, a.demo, only, not a.no_news_fetch)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write(to_json(res))
    for k in ("with_news", "without_news"):
        s = res[k]
        print(k, {x: s.get(x) for x in ("trades", "win_rate_pct", "avg_r", "avg_r_se", "total_r", "profit_factor", "max_drawdown_r",
                                         "net_pnl_risk1", "return_pct_risk1", "net_pnl_risk50", "return_pct_risk50")})


if __name__ == "__main__":
    main()
