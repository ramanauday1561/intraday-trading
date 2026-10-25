"""Causal single-day replay: python -m engine.backtest --date 2026-10-23 --time 10:00

At the as-of time only bars up to that moment are visible to the engine. Entry is the NEXT bar's
open (no look-ahead); stop/target are walked bar by bar (stop wins ties); anything still open
exits at the 15:15 bar close. Historical news cannot be replayed point-in-time from free feeds,
so the fundamental pillar is neutral (0) in every backtest."""
import argparse
import json
import os
from datetime import datetime

import pandas as pd

from . import demo as demomod
from .config import COST_PCT, IST, INDEX_SYMBOLS, UNIVERSE
from .pipeline import _index_corr, to_json
from .scoring import analyse_stock

NEUTRAL_NEWS = {"score": 0.0, "items": [], "event": None, "tier1_earnings": False, "positive_catalyst": 0.0,
                "sue_proxy": 0.0, "divergence": 0.0, "engine": "none (not replayable)"}


def _frames(date, demo):
    syms = [u[0] for u in UNIVERSE]
    idx_syms = sorted(set(INDEX_SYMBOLS.values()) | {u[3] for u in UNIVERSE})
    out = {}
    if demo:
        now = datetime(date.year, date.month, date.day, 12, 0, tzinfo=IST)
        for s in syms + idx_syms:
            m = demomod.make_symbol(s, now, "breakout" if s in idx_syms else None)
            out[s] = (pd.concat([m["hist"], m["today"]]), m["daily"])
        return out
    from .market import fetch_many
    i5 = fetch_many([s + ".NS" for s in syms] + idx_syms, "1mo", "5m")
    d1 = fetch_many([s + ".NS" for s in syms] + idx_syms, "3mo", "1d")
    for s in syms:
        out[s] = (i5.get(s + ".NS"), d1.get(s + ".NS"))
    for s in idx_syms:
        out[s] = (i5.get(s), d1.get(s))
    return out


def simulate(sig, bars_after, exit_hhmm=(15, 15)):
    """Walk bars after entry. Returns trade dict or None if no bars."""
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
    gross = per_share * qty
    cost = entry * qty * COST_PCT / 100
    return {"symbol": sig["symbol"], "direction": sig["direction"], "setup": sig["setup"], "conviction": sig["conviction"],
            "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2), "exit": round(exit_px, 2),
            "reason": reason, "qty": qty, "r_multiple": round(per_share / sd, 2), "gross_pnl": round(gross, 0),
            "costs": round(cost, 0), "net_pnl": round(gross - cost, 0)}


def run(date, asof=(9, 30), capital=100000.0, risk_pct=50.0, demo=False):
    frames = _frames(date, demo)
    nifty5, nifty_d = frames["^NSEI"]
    if nifty5 is None or nifty_d is None:
        from . import market
        print("data fetch errors:", {k: len(v) for k, v in market.ERRORS.items()})
        raise SystemExit("No Nifty data for that date (Yahoo unreachable or date outside 5-minute history).")
    cut = pd.Timestamp(datetime(date.year, date.month, date.day, *asof, tzinfo=IST))

    def split(df5, daily):
        today = df5[[d == date for d in df5.index.date]]
        hist = df5[[d < date for d in df5.index.date]]
        prior = daily[[d < date for d in daily.index.date]]
        return today, hist, prior

    nt, nh, nd = split(nifty5, nifty_d)
    if nt.empty:
        raise SystemExit(f"No intraday bars for {date} (market holiday or data unavailable).")
    stocks = {}
    for sym, name, sector, sidx, mcap in UNIVERSE:
        a, b = frames[sym]
        if a is None or b is None:
            continue
        t, h, dd = split(a, b)
        if len(t) > 6 and len(dd) >= 20 and len(h) > 30:
            stocks[sym] = (t, h, dd)
    corr = _index_corr({s: v[2] for s, v in stocks.items()}, nd)
    vt, vd = frames["^INDIAVIX"]
    vix_day = vt[[d == date for d in vt.index.date]] if vt is not None else None
    vix_prev = float(vd["close"][[d < date for d in vd.index.date]].iloc[-1]) if vd is not None else 14.0
    # Scan every 15 min from `asof` to 14:30; each stock takes its FIRST valid signal of the day.
    base = datetime(date.year, date.month, date.day, tzinfo=IST)
    cuts, t_ = [], asof[0] * 60 + asof[1]
    while t_ <= 14 * 60 + 30:
        cuts.append(pd.Timestamp(base + pd.Timedelta(minutes=t_))); t_ += 15
    trades, taken, blockers, n_eval = [], set(), {}, 0
    for cut in cuts:
        ntc = nt[nt.index <= cut]
        if len(ntc) < 3:
            continue
        vix = float(vix_day[vix_day.index <= cut]["close"].iloc[-1]) if vix_day is not None and len(vix_day[vix_day.index <= cut]) else 14.0
        nifty_chg = 100 * (float(ntc["close"].iloc[-1]) / float(nd["close"].iloc[-1]) - 1)
        for sym, name, sector, sidx, mcap in UNIVERSE:
            if sym not in stocks or sym in taken:
                continue
            t, h, dd = stocks[sym]
            seen, later = t[t.index <= cut], t[t.index > cut]
            if len(seen) < 3:
                continue
            sec_chg = nifty_chg
            st = frames.get(sidx, (None, None))
            if st[0] is not None and st[1] is not None:
                s_t, _, s_d = split(*st)
                s_seen = s_t[s_t.index <= cut]
                if len(s_seen) and len(s_d):
                    sec_chg = 100 * (float(s_seen["close"].iloc[-1]) / float(s_d["close"].iloc[-1]) - 1)
            ctx = {"symbol": sym, "name": name, "sector": sector, "today": seen, "hist": h, "daily": dd,
                   "prev_close": float(dd["close"].iloc[-1]), "now": cut.to_pydatetime(), "phase": "ACTIVE", "vix": vix,
                   "vix_chg": 100 * (vix / vix_prev - 1), "index_corr": corr, "index_gex": None,
                   "nifty": float(ntc["close"].iloc[-1]), "nifty_open": float(nt["open"].iloc[0]), "nifty_chg": nifty_chg,
                   "sector_chg": sec_chg, "news": NEUTRAL_NEWS, "options": None, "fut_oi_chg_pct": None,
                   "capital": capital, "risk_pct": risk_pct}
            sig = analyse_stock(ctx)
            n_eval += 1
            for bk in sig["risk"]["blockers"]:
                blockers[bk["code"]] = blockers.get(bk["code"], 0) + 1
            if sig["direction"] == "NO_TRADE":
                continue
            tr = simulate(sig, later)
            if tr:
                tr["signal_time"] = cut.strftime("%H:%M")
                trades.append(tr); taken.add(sym)
    skipped = len(stocks) - len(taken)
    all_sigs = [{"evaluations": n_eval, "blocker_counts": blockers, "stocks": len(stocks)}]
    net = sum(t["net_pnl"] for t in trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    eq, peak, mdd = capital, capital, 0.0
    for t in sorted(trades, key=lambda x: x["symbol"]):  # sequential approximation; trades overlap in reality
        eq += t["net_pnl"]; peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak * 100)
    summary = {"trades": len(trades), "no_trade": skipped, "win_rate_pct": round(100 * len(wins) / len(trades), 1) if trades else None,
               "avg_r": round(sum(t["r_multiple"] for t in trades) / len(trades), 2) if trades else None,
               "net_pnl": net, "net_return_pct_on_capital": round(100 * net / capital, 2),
               "worst_trade": min((t["net_pnl"] for t in trades), default=0), "best_trade": max((t["net_pnl"] for t in trades), default=0),
               "max_drawdown_pct": round(mdd, 1)}
    return {"date": date.isoformat(), "asof": f"{asof[0]:02d}:{asof[1]:02d} IST", "mode": "demo" if demo else "live",
            "capital": capital, "risk_pct": risk_pct, "vix_open_prev_close": round(vix_prev, 2), "scan": f"every 15 min {asof[0]:02d}:{asof[1]:02d}-14:30, first signal per stock",
            "summary": summary, "trades": sorted(trades, key=lambda t: -t["conviction"]), "signals": all_sigs,
            "caveats": ["News/fundamental pillar is neutral: free feeds cannot be replayed point-in-time.",
                        "NSE derivatives (GEX, OI, IV skew) are not replayable; those inputs are omitted.",
                        "Single day, ~28 stocks: statistically meaningless as proof of edge.",
                        "Entry = next 5-min bar open; stop wins ties within a bar; exit by 15:15; costs assumed %.2f%% round trip." % COST_PCT,
                        "Position size follows the risk setting, capped at 5x capital notional."]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--time", default="10:00")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--risk-pct", type=float, default=50.0)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--out", default="data/backtest.json")
    a = ap.parse_args()
    d = datetime.strptime(a.date, "%Y-%m-%d").date()
    hh, mm = map(int, a.time.split(":"))
    res = run(d, (hh, mm), a.capital, a.risk_pct, a.demo)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write(to_json(res))
    print(json.dumps(res["summary"], indent=1))


if __name__ == "__main__":
    main()
