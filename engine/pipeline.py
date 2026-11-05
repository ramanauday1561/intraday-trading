"""Orchestrates data ingestion -> regime -> per-ticker analysis -> JSON payload."""
import json
import re
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import demo as demomod
from . import news as newsmod
from .config import INDEX_SYMBOLS, IST, REGIME_WEIGHTS, UNIVERSE
from . import archive
from . import kite as kitemod
import os
from .market import NSE, daily_from_intraday, drop_incomplete, fetch_many, options_structure, parse_derivatives
from .scoring import analyse_stock, daily_sigma_pct, detect_regime, session_phase
from . import indicators as ind

DISCLAIMER = ("Educational research output, not investment advice. Data from free public sources may be delayed or "
              "incomplete. Verify before trading; you are solely responsible for orders.")


def _split(df5, daily):
    last = df5.index[-1].date()
    today = df5[[d == last for d in df5.index.date]]
    hist = df5[[d != last for d in df5.index.date]]
    prior = daily[[d < last for d in daily.index.date]]
    return today, hist, prior


def _chg(today, prev_close):
    return 100 * (float(today["close"].iloc[-1]) / prev_close - 1)


def _index_corr(daily_map, nifty_daily):
    rn = nifty_daily["close"].pct_change().dropna().tail(20)
    cs = []
    for d in daily_map.values():
        r = d["close"].pct_change().dropna().tail(20)
        j = pd.concat([r.reset_index(drop=True), rn.reset_index(drop=True)], axis=1).dropna()
        if len(j) > 10:
            cs.append(j.corr().iloc[0, 1])
    return float(np.nanmean(cs)) if cs else 0.5


def _load_live(now, kc=None, source=None):
    """5-minute and daily bars: Zerodha Kite when today's session exists, Yahoo for anything Kite did not return."""
    syms = [u[0] + ".NS" for u in UNIVERSE]
    idx = list(INDEX_SYMBOLS.values()) + sorted({u[3] for u in UNIVERSE} - set(INDEX_SYMBOLS.values()))
    allsyms = syms + idx
    i5, d1 = {}, {}
    if kc:
        try:
            i5, d1 = kitemod.fetch_bars(kc, allsyms, now)
        except (kitemod.KiteError, OSError) as e:
            print("Kite bars unavailable, using Yahoo:", e, file=sys.stderr)
    if source is not None:
        source.update({"kite": sum(v is not None for v in i5.values()), "yahoo": 0})
    miss5 = [s for s in allsyms if i5.get(s) is None]
    missd = [s for s in allsyms if d1.get(s) is None]
    if miss5:
        i5.update(fetch_many(miss5, "5d", "5m"))
        if source is not None:
            source["yahoo"] = sum(i5.get(s) is not None for s in miss5)
    if missd:
        d1.update(fetch_many(missd, "1y", "1d"))  # same long history the backtests use, so ATR/ADX match
    i5 = {k: drop_incomplete(v, now) for k, v in i5.items()}  # completed bars only
    if any(i5.get(INDEX_SYMBOLS[k]) is None or d1.get(INDEX_SYMBOLS[k]) is None for k in ("NIFTY", "VIX")):
        return None
    return i5, d1


def build(now=None, force_demo=False, capital=100000.0, risk_pct=5.0, return_bars=False):
    now = (now or datetime.now(IST)).astimezone(IST)
    fallbacks = []
    kc = None if force_demo else kitemod.load_client()
    source = {}
    live = None if force_demo else _load_live(now, kc, source)
    mode = "live" if live else "demo"
    stocks = {}
    sources = ["Zerodha Kite Connect (prices, option chains; when connected)", "Yahoo Finance chart API (prices, India VIX)", "Google News RSS + ET/Moneycontrol/Mint RSS",
               "NSE public derivatives API (best effort)", "FinBERT (ProsusAI/finbert) or finance lexicon", "NumPy/pandas"]
    if mode == "live":
        i5, d1 = live
        idx_data = {}
        for k in set(INDEX_SYMBOLS.values()) | {u[3] for u in UNIVERSE}:
            if i5.get(k) is not None and d1.get(k) is not None:
                idx_data[k] = _split(i5[k], d1[k])
        for sym, name, sector, sidx, mcap in UNIVERSE:
            a, b = i5.get(sym + ".NS"), d1.get(sym + ".NS")
            if a is not None and (b is None or len(b) < 25):  # daily series failed: rebuild it from the archive
                b = daily_from_intraday(archive.merge_bars(archive.load_bars(sym + ".NS"), a))
                fallbacks.append(sym)
            if a is None or b is None or len(a) < 30 or len(b) < 25:
                continue
            t, h, dd = _split(a, b)
            if len(t) < 3 or len(dd) < 20:
                continue
            stocks[sym] = {"today": t, "hist": h, "daily": dd, "prev_close": float(dd["close"].iloc[-1]), "kind": None}
    else:
        idx_data = {}
        base = {"^NSEI": "range", "^NSEBANK": "breakout", "^INDIAVIX": "range"}
        for k in {u[3] for u in UNIVERSE} | set(INDEX_SYMBOLS.values()):
            m = demomod.make_symbol(k, now, base.get(k, "breakout"))
            idx_data[k] = (m["today"], m["hist"], m["daily"])
        for sym, *_ in UNIVERSE:
            m = demomod.make_symbol(sym, now)
            stocks[sym] = m
    nt, nh, nd = idx_data["^NSEI"]
    nifty_prev = float(nd["close"].iloc[-1])
    nifty_chg = _chg(nt, nifty_prev)
    if mode == "demo":
        vix, vix_chg = 14.2, -1.5
    else:
        vt, _, vd = idx_data["^INDIAVIX"]
        vix, vix_chg = float(vt["close"].iloc[-1]), 100 * (float(vt["close"].iloc[-1]) / float(vd["close"].iloc[-1]) - 1)
    last_ts = nt.index[-1]
    if mode == "demo":
        now = last_ts.to_pydatetime() + pd.Timedelta(minutes=5)
    phase = session_phase(now) if mode == "live" and last_ts.date() == now.date() else ("ACTIVE" if mode == "demo" else "CLOSED")
    corr = _index_corr({s: v["daily"] for s, v in stocks.items()}, nd)

    # derivatives (index GEX + per-stock structures)
    chains, kite_err = {}, None
    if kc and mode == "live":  # real option chains, futures OI and lot sizes from Kite
        try:
            spots = {u[0]: float(stocks[u[0]]["today"]["close"].iloc[-1]) for u in UNIVERSE if u[0] in stocks}
            chains = kitemod.option_chains(kc, spots, prev_oi=archive.load_oi(), min_days=3)
            chains.update(kitemod.option_chains(kc, {"NIFTY": float(nt["close"].iloc[-1])}, width=0.04))
        except (kitemod.KiteError, OSError) as e:
            kite_err = str(e)
            print("Kite option chains unavailable:", e, file=sys.stderr)
    nse = NSE() if mode == "live" and not chains else None
    index_gex, deriv = None, {}
    if chains.get("NIFTY"):
        index_gex = options_structure(chains["NIFTY"]["options"], float(nt["close"].iloc[-1]), chains["NIFTY"]["expiry"])
    elif nse and nse.ok:
        js = nse.derivatives("NIFTY")
        pr = parse_derivatives(js, float(nt["close"].iloc[-1]))
        index_gex = options_structure(pr["options"], float(nt["close"].iloc[-1]), pr.get("expiry"))
    if mode == "demo":
        index_gex = {"net_gex": -1.0, "gex_norm": -0.2, "call_wall": round(float(nt["close"].iloc[-1]) * 1.01, -1),
                     "put_wall": round(float(nt["close"].iloc[-1]) * 0.99, -1), "gamma_flip": None, "iv_skew": None,
                     "call_writing_otm": False, "demo": True}

    # news
    general = []
    if mode == "live":
        for src, url in newsmod.GENERAL_FEEDS:
            general += newsmod.fetch_feed(url, src)
    signals = []
    health = {"prices_ok": len(stocks), "universe": len(UNIVERSE), "news_items": 0, "daily_from_archive": fallbacks,
              "missing": [u[0] for u in UNIVERSE if u[0] not in stocks],
              "nse_derivatives": bool(index_gex is not None), "general_feed_items": len(general),
              "kite": {"connected": bool(kc), "bars_from_kite": source.get("kite", 0), "bars_from_yahoo": source.get("yahoo", 0),
                       "option_chains": len(chains), "error": kite_err}}
    for sym, name, sector, sidx, mcap in UNIVERSE:
        m = stocks.get(sym)
        if not m:
            continue
        if mode == "live":
            items = newsmod.fetch_symbol_news(name, sym)
            key = re.compile(r"\b(%s|%s)\b" % (re.escape(name.split()[0]), re.escape(sym)), re.I)
            items += [i for i in general if key.search(i["title"])]
            opt = None
            fut = None
            ch = chains.get(sym)
            if ch:
                spot = float(m["today"]["close"].iloc[-1])
                fut = ch["fut_oi_chg_pct"]
                opt = options_structure(ch["options"], spot, ch["expiry"])
                if opt:
                    opt["_rows"], opt["lot"] = ch["options"], ch["lot"]
            elif nse and nse.ok:
                pr = parse_derivatives(nse.derivatives(sym), float(m["today"]["close"].iloc[-1]))
                fut = pr["fut_oi_chg_pct"]
                opt = options_structure(pr["options"], float(m["today"]["close"].iloc[-1]), pr.get("expiry"))
                if opt:
                    opt["_rows"] = pr["options"]
        else:
            items, opt, fut = demomod.demo_news(sym, m["kind"], now), None, None
        health["news_items"] += len(items)
        news = newsmod.analyse_symbol(sym, name, mcap, items, now=datetime.now(timezone.utc))
        st, sh, sd = idx_data.get(sidx, idx_data["^NSEI"])
        sec_chg = _chg(st, float(sd["close"].iloc[-1]))
        ctx = {"symbol": sym, "name": name, "sector": sector, "today": m["today"], "hist": m["hist"], "daily": m["daily"],
               "prev_close": m["prev_close"], "now": now, "phase": phase, "vix": vix, "vix_chg": vix_chg,
               "index_corr": corr, "index_gex": index_gex, "nifty": float(nt["close"].iloc[-1]), "nifty_open": float(nt["open"].iloc[0]),
               "nifty_chg": nifty_chg, "sector_chg": sec_chg, "news": news, "options": opt, "fut_oi_chg_pct": fut,
               "capital": capital, "risk_pct": risk_pct}
        try:
            signals.append(analyse_stock(ctx))
        except Exception as e:  # noqa: BLE001 - one bad ticker must not sink the run
            print(f"{sym}: analysis failed: {e!r}", file=sys.stderr)
    signals.sort(key=lambda s: (s["direction"] == "NO_TRADE", -s["conviction"]))
    bank_t = idx_data.get("^NSEBANK")
    regime = detect_regime(vix, ind.adx(nd), index_gex, corr, False)
    macro = {"nifty": {"ltp": round(float(nt["close"].iloc[-1]), 2), "chg_pct": round(nifty_chg, 2)},
             "banknifty": {"ltp": round(float(bank_t[0]["close"].iloc[-1]), 2),
                           "chg_pct": round(_chg(bank_t[0], float(bank_t[2]["close"].iloc[-1])), 2)} if bank_t else None,
             "india_vix": round(vix, 2), "vix_chg_pct": round(vix_chg, 2),
             "expected_daily_move_pct": round(daily_sigma_pct(vix), 2), "index_correlation": round(corr, 2),
             "regime": regime, "weights": REGIME_WEIGHTS[regime],
             "gex": None if not index_gex else {k: (round(v, 2) if isinstance(v, float) else v) for k, v in index_gex.items() if k != "_rows"}}
    payload = {"schema_version": 1,
            "meta": {"generated_at": datetime.now(IST).isoformat(timespec="seconds"), "data_as_of": last_ts.isoformat(),
                     "mode": mode, "session_phase": phase, "execution_allowed": phase == "ACTIVE" or mode == "demo",
                     "health": health, "universe_size": len(UNIVERSE),
                     "kite_api_key": os.environ.get("KITE_API_KEY") or None, "analysed": len(signals), "sources": sources,
                     "capital": capital, "risk_pct": risk_pct, "disclaimer": DISCLAIMER,
                     "notes": ["Hawkes branching ratio is a Fano-factor approximation on 5-minute bars.",
                               "GEX/IV-skew/OI need NSE derivatives data; when blocked these inputs are omitted and weights renormalised.",
                               "LPP bands are modelled, not transmitted; no orders are ever placed."] +
                              (["DEMO MODE: synthetic data - live sources unreachable."] if mode == "demo" else [])},
            "macro": macro, "signals": signals}
    if return_bars:
        return payload, {sym: m["today"] for sym, m in stocks.items()}
    return payload


def to_json(payload):
    def default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, (np.bool_,)):
            return bool(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        raise TypeError(type(o))
    return json.dumps(payload, default=default, indent=1, allow_nan=False)
