"""Regime detection, four-pillar scoring, contradiction matrix and execution/risk construction."""
import math

import numpy as np
import pandas as pd

from . import indicators as ind
from . import lpp as lppmod
from . import options_idea
from .config import (LEVERAGE, CIRCUIT_WARN_PCT, DEFAULT_BAND_PCT, IST, MIN_TURNOVER_CR, OVERRIDE_MIN_CONVICTION, REGIME_WEIGHTS,
                     SCORE_THRESHOLD, VPIN_TOXIC)
from .market import oi_quadrant

QUAD_BIAS = {"LONG_BUILDUP": 1.0, "SHORT_BUILDUP": -1.0, "SHORT_COVERING": 0.6, "LONG_UNWINDING": -0.3, "UNAVAILABLE": 0.0}


def clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, float(x)))


def session_phase(now):
    m = now.hour * 60 + now.minute
    if now.weekday() >= 5 or m < 9 * 60 or m >= 15 * 60 + 30:
        return "CLOSED"
    if m < 9 * 60 + 15:
        return "PRE_OPEN"
    if m < 9 * 60 + 30:
        return "MORATORIUM"
    return "ACTIVE" if m < 15 * 60 else "LATE"


def daily_sigma_pct(vix):
    return vix / math.sqrt(252.0)


def detect_regime(vix, adx_v, gex, index_corr, event_driven):
    """gex = dict(gex_norm) or None. Missing GEX relaxes that criterion (flagged by caller)."""
    g = None if gex is None else gex["gex_norm"]
    if vix > 20 and index_corr > 0.85:
        return "MACRO_SHOCK"
    if event_driven:
        return "EVENT_DRIVEN"
    if vix > 15 and adx_v > 25 and (g is None or g < 0):
        return "TRENDING"
    if vix < 12 and adx_v < 20 and (g is None or g > 0.3):
        return "RANGE_BOUND"
    return "TRANSITIONAL"


def classify_contradiction(pos_news, gap, ofi_v, last_bar_bear, ltp, vw, prior_rally, extension, opt, surge_buy):
    """Contradiction matrix. Returns (type, note, forced_direction, setup, neutralise_fundamental).

    Priced-in exhaustion only forces a short once the reversal is CONFIRMED (price below VWAP and
    order flow turning negative); before that it is reported as context, never as a trade."""
    if pos_news and gap > 0.5 and ofi_v < -0.1 and last_bar_bear and ltp < vw:
        return ("DISTRIBUTION_TRAP", "Positive catalyst + gap-up met negative OFI and a VWAP breach: news used as exit liquidity. "
                "Fundamental pillar invalidated; targeting gap closure.", "SHORT", "TRAP_FADE", True)
    if pos_news and prior_rally > 4 and extension > 1.5 and ((opt and opt["call_writing_otm"]) or (not opt and ofi_v < 0.1)):
        why = "OTM call writing" if opt else "fading order flow (options data unavailable)"
        if ltp < vw and ofi_v < -0.05:
            return ("PRICED_IN_EXHAUSTION", f"Positive news after a multi-day rally with {why}, now confirmed by price below VWAP "
                    "and negative order flow: news priced in. Fundamental score neutralised; fading via mean reversion.",
                    "SHORT", "MEAN_REVERSION", True)
        return ("PRICED_IN_WATCH", f"Positive news after a multi-day rally with {why} may be priced in, but the reversal is NOT "
                "confirmed (price still above VWAP or order flow not negative). No short until it is.", None, "MOMENTUM", True)
    if pos_news and gap > 0.8 and surge_buy and ofi_v > 0.1 and prior_rally < 0:
        return ("GENUINE_OVERRIDE", "Breakaway gap with self-exciting buy flow (Hawkes surge) overrides prior-session weakness.",
                "LONG", "BREAKAWAY", False)
    return None


def vix_multiplier(vix):
    return 0.8 if vix < 12 else 1.0 if vix < 16 else 1.25 if vix < 20 else 1.5


def analyse_stock(c):
    """c: context dict (see pipeline). Returns the per-ticker payload of the JSON schema."""
    sym, today, hist, daily = c["symbol"], c["today"], c["hist"], c["daily"]
    now, vix = c["now"], c["vix"]
    ltp = float(today["close"].iloc[-1])
    prev_close = float(c["prev_close"])
    opn = float(today["open"].iloc[0])
    chg = 100 * (ltp / prev_close - 1)
    gap = 100 * (opn / prev_close - 1)
    vw = ind.vwap(today)
    atr_d = ind.atr(daily)
    adx_v = ind.adx(daily)
    hr = bool(c.get("hourly"))  # coarse hourly bars: shorter windows, flow measured on recent multi-day context
    ctx_bars = pd.concat([hist.tail(21), today]) if hr else today
    poc, val_, vah = ind.volume_profile(today, bins=12 if hr else 30)
    ofi_v, vpin_v = (ind.ofi(ctx_bars, window=2) if hr else ind.ofi(today)), ind.vpin(ctx_bars)
    hk = ind.hawkes_branching(hist, today, base_w=3, cur_w=2, min_cur=3) if hr else ind.hawkes_branching(hist, today)
    vol_exp = ind.volume_expanding(today, recent=1, min_prior=2) if hr else ind.volume_expanding(today)
    sig_d = daily_sigma_pct(vix)
    turnover_cr = float((daily["close"] * daily["volume"]).tail(20).mean() / 1e7)
    opt, fut_oi = c.get("options"), c.get("fut_oi_chg_pct")
    quad = oi_quadrant(chg, fut_oi)
    news = c["news"]
    daily_ret = daily["close"].pct_change().dropna()
    prior_rally = 100 * (daily["close"].iloc[-1] / daily["close"].iloc[-4] - 1) if len(daily) > 4 else 0.0
    extension = (ltp - daily["close"].tail(20).mean()) / atr_d if atr_d else 0.0

    regime = detect_regime(vix, adx_v, c.get("index_gex"), c["index_corr"], news["tier1_earnings"])
    w = REGIME_WEIGHTS[regime]

    # ---- pillars (each in [-1, 1]) ----
    s_fund = news["score"]
    vwap_s = clip((ltp - vw) / max(0.5 * atr_d * 0.35, 1e-9))
    trend_s = clip(math.tanh((chg / max(sig_d, 0.3)) / 2))
    prof_s = 1.0 if ltp > vah else -1.0 if ltp < val_ else clip((ltp - poc) / max(vah - val_, 1e-9))
    s_price = clip(0.4 * vwap_s + 0.35 * trend_s + 0.25 * prof_s)
    if regime == "RANGE_BOUND":
        s_price = -s_price  # fade the extremes of the implied range
    parts = [(0.35, ofi_v * 2), (0.2, clip((hk["buy"] - hk["sell"]) * 3))]
    if quad != "UNAVAILABLE":
        parts.append((0.25, QUAD_BIAS[quad]))
    skew = opt["iv_skew"] if opt else None
    if skew is not None:  # steepening put skew into a rally is bearish; flat skew in a sell-off is bullish
        parts.append((0.10, clip(-skew / 10) if chg > 0 else clip(0.5 - skew / 10)))
    wsum = sum(p[0] for p in parts)
    s_inst = clip(sum(p[0] * clip(p[1]) for p in parts) / wsum)
    nifty_chg, sector_chg = c["nifty_chg"], c["sector_chg"]
    s_macro = clip(0.4 * math.tanh(nifty_chg / max(sig_d, 0.3)) + 0.4 * math.tanh(sector_chg / max(sig_d, 0.3))
                   - 0.2 * math.tanh(c["vix_chg"] / 5))
    pillars = {"fundamental": s_fund, "price_action": s_price, "institutional": s_inst, "macro": s_macro}
    composite = sum(w[k] * pillars[k] for k in w)

    # ---- contradiction matrix ----
    contradiction, setup, forced = None, "MOMENTUM", None
    pos_news = news["positive_catalyst"] > 0.25 or s_fund > 0.5
    last_bar_bear = today["close"].iloc[-1] < today["open"].iloc[-1]
    cm = classify_contradiction(pos_news, gap, ofi_v, last_bar_bear, ltp, vw, prior_rally, extension, opt, hk["surge_buy"])
    if cm:
        contradiction = cm[:2]
        forced, setup = cm[2], cm[3]
        if cm[4]:
            pillars["fundamental"] = 0.0
        composite = sum(w[k] * pillars[k] for k in w)

    direction = forced or ("LONG" if composite > SCORE_THRESHOLD else "SHORT" if composite < -SCORE_THRESHOLD else "NO_TRADE")
    if regime == "RANGE_BOUND" and not forced and direction != "NO_TRADE":
        setup = "MEAN_REVERSION"

    # ---- risk / execution geometry ----
    k = vix_multiplier(vix) * (1.15 if news["divergence"] > 0.4 else 1.0)
    stop_dist = max(k * 0.35 * atr_d, 0.25 * sig_d / 100 * ltp)
    rr = 3.0 if (regime == "TRENDING" and abs(composite) >= 0.45) else 2.0
    if vix < 12:
        rr = 2.0
    toxic = vpin_v > VPIN_TOXIC
    size_mult = 0.5 if toxic else 1.0
    if news["divergence"] > 0.4:
        size_mult *= 0.75
    band = c.get("band_pct", DEFAULT_BAND_PCT)
    upper, lower = prev_close * (1 + band / 100), prev_close * (1 - band / 100)
    d_up, d_dn = 100 * (upper - ltp) / ltp, 100 * (ltp - lower) / ltp

    blockers, warnings = [], []
    phase = c["phase"]
    if phase != "ACTIVE":
        blockers.append({"code": "SESSION_" + phase, "message": {
            "MORATORIUM": "15-minute opening moratorium: no automated execution before 09:30 IST.",
            "PRE_OPEN": "Pre-open call auction in progress; engine inactive until 09:30 IST.",
            "LATE": "No fresh entries after 15:00 IST.", "CLOSED": "Market closed; showing the last completed session."}[phase]})
    if turnover_cr < MIN_TURNOVER_CR:
        blockers.append({"code": "LIQUIDITY", "message": f"Avg turnover Rs {turnover_cr:.0f} cr below the Rs {MIN_TURNOVER_CR:.0f} cr F&O criterion."})
    target_dist = rr * stop_dist
    if direction != "NO_TRADE" and target_dist > 0.9 * atr_d:
        blockers.append({"code": "ATR_RANGE", "message": f"Required {rr:.0f}R target ({target_dist:.1f}) exceeds 90% of daily ATR ({atr_d:.1f}); insufficient range."})
    if direction == "LONG" and d_up < CIRCUIT_WARN_PCT:
        blockers.append({"code": "UPPER_CIRCUIT", "message": f"LTP within {d_up:.1f}% of upper circuit; long signals suppressed (illiquid lock risk)."})
    if direction == "SHORT" and d_dn < CIRCUIT_WARN_PCT:
        blockers.append({"code": "LOWER_CIRCUIT", "message": f"LTP within {d_dn:.1f}% of lower circuit; short signals suppressed."})
    # structural validity
    if direction != "NO_TRADE" and setup in ("MOMENTUM", "BREAKAWAY"):
        if direction == "LONG" and not (ltp > vw and ltp > poc and ltp >= vah * 0.999 and vol_exp):
            blockers.append({"code": "STRUCTURE", "message": "Long needs price > VWAP, > POC and breaking Value Area High on expanding volume."})
        if direction == "SHORT" and not (ltp < vw and ltp < poc and ltp <= val_ * 1.001 and vol_exp):
            blockers.append({"code": "STRUCTURE", "message": "Short needs price < VWAP, < POC and breaking Value Area Low on expanding volume."})
    if direction != "NO_TRADE" and setup == "MEAN_REVERSION" and not forced:
        if direction == "LONG" and not ltp <= val_ * 1.002:
            blockers.append({"code": "STRUCTURE", "message": "Range-bound long only valid at/below Value Area Low."})
        if direction == "SHORT" and not ltp >= vah * 0.998:
            blockers.append({"code": "STRUCTURE", "message": "Range-bound short only valid at/above Value Area High."})
    if direction != "NO_TRADE" and setup in ("MOMENTUM", "BREAKAWAY") and not (hk["surge_buy"] if direction == "LONG" else hk["surge_sell"]) \
            and (hk["buy"] if direction == "LONG" else hk["sell"]) < 0.15:
        blockers.append({"code": "HAWKES", "message": "Breakout lacks self-exciting order flow (Hawkes branching ratio low)."})

    if toxic:
        warnings.append({"level": "high", "code": "TOXIC_FLOW", "message": f"VPIN {vpin_v:.2f} indicates toxic flow; size halved."})
    if min(d_up, d_dn) < CIRCUIT_WARN_PCT:
        warnings.append({"level": "high", "code": "CIRCUIT_PROXIMITY", "message": f"Within {min(d_up, d_dn):.1f}% of exchange circuit limit."})
    if news["divergence"] > 0.4:
        warnings.append({"level": "medium", "code": "MEDIA_DIVERGENCE", "message": "Cross-media sentiment divergence: spreads/slippage risk; stops widened, size cut."})
    if vix > 20:
        warnings.append({"level": "medium", "code": "HIGH_VIX", "message": f"India VIX {vix:.1f}: stops widened, size reduced."})
    gf = c.get("index_gex") and c["index_gex"].get("gamma_flip")
    if gf and ((c["nifty"] > gf) != (c["nifty_open"] > gf)):
        warnings.append({"level": "medium", "code": "GAMMA_FLIP", "message": f"Nifty crossed the gamma flip ({gf:.0f}): destabilising regime, widen risk."})
        stop_dist *= 1.2

    if forced and direction != "NO_TRADE":  # overrides do not get to skip the conviction floor
        pre_conv = 100 * clip(abs(composite) / 0.6 + (0.1 if (s_macro > 0.15) == (direction == "LONG") and abs(s_macro) > 0.15 else 0), 0, 1)
        if pre_conv < OVERRIDE_MIN_CONVICTION:
            blockers.append({"code": "LOW_CONVICTION", "message": f"{contradiction[0].replace('_', ' ').title()} override has conviction {pre_conv:.0f} < {OVERRIDE_MIN_CONVICTION}; shown as context only."})

    if blockers or direction == "NO_TRADE":
        final = "NO_TRADE"
    else:
        final = direction
    sign = 1 if direction == "LONG" else -1
    entry = ltp
    stop = entry - sign * stop_dist
    t1 = entry + sign * rr * stop_dist
    capital, risk_pct = c["capital"], c["risk_pct"]
    risk_amt = capital * risk_pct / 100
    qty = int(risk_amt * size_mult / stop_dist) if stop_dist > 0 else 0
    if qty * ltp > capital * LEVERAGE:
        qty = int(capital * LEVERAGE / ltp)
        warnings.append({"level": "high", "code": "LEVERAGE_CAP", "message": f"Size capped at {LEVERAGE:.0f}x capital notional; actual risk is below the risk setting."})

    # ---- LPP on the proposed ATM option (modelled) ----
    lpp = {"applicable": False}
    opt_idea = None
    if final != "NO_TRADE":
        sess_day = today.index[-1].date()
        T = max((options_idea.monthly_expiry(sess_day) - sess_day).days, 1) / 365.0
        sigma = (opt or {}).get("atm_iv")
        sigma = sigma / 100 if sigma else float(daily_ret.tail(20).std() * math.sqrt(252))
        kind = "Call" if direction == "LONG" else "Put"
        rows = [o for o in (opt or {}).get("_rows", []) if o["type"] == kind and o["ltp"] > 0]
        premium = min(rows, key=lambda o: abs(o["k"] - ltp))["ltp"] if rows else 0.4 * ltp * sigma * math.sqrt(T)
        ref = premium - 0.5 * sign * (ltp - float(today["close"].iloc[-2] if len(today) > 1 else ltp)) * 30 / 300
        limit = lppmod.clamp(premium * 1.005, ref, premium)
        hr = lppmod.headroom_pct(limit, ref, premium)
        rng = lppmod.lpp_range(ref, premium)
        lpp = {"applicable": rng is not None, "option": "ATM " + ("CE" if direction == "LONG" else "PE"),
               "premium": round(premium, 2), "reference": round(ref, 2),
               "lower": round(rng[0], 2) if rng else None, "upper": round(rng[1], 2) if rng else None,
               "entry_limit": round(limit, 2), "headroom_pct": hr, "modelled": True,
               "valid": lppmod.validate_order(limit, ref, premium)}
        opt_idea = options_idea.idea(direction, ltp, sess_day, premium, stop_dist, rr)
        if hr is not None and hr < 15:
            warnings.append({"level": "high", "code": "LPP_BOUNDARY", "message": f"Entry limit within {hr:.0f}% of the 30-second LPP boundary (NSE error 17070 risk)."})

    inval = ("Thesis invalidated if price closes back below VWAP (%.2f), OFI turns positive, or price falls back inside the Value Area (below VAH %.2f)." % (vw, vah)
             if direction == "SHORT" else
             "Thesis invalidated if price closes back below VWAP (%.2f), OFI turns negative, or price falls back inside the Value Area (below VAH %.2f)." % (vw, vah))
    if setup in ("MEAN_REVERSION", "TRAP_FADE") and direction == "SHORT":
        inval = "Thesis invalidated if price reclaims VWAP (%.2f) with OFI turning positive, or trades above Value Area High (%.2f) on expanding volume." % (vw, vah)
    conv = clip(abs(composite) / 0.6, 0, 1)
    aligned = (s_macro > 0.15 and direction == "LONG") or (s_macro < -0.15 and direction == "SHORT")
    conv = clip(conv + (0.1 if aligned else 0), 0, 1)
    if final == "NO_TRADE":
        conv *= 0.6

    thesis = (f"{regime.replace('_', ' ').title()} regime (VIX {vix:.1f}, ADX {adx_v:.0f}). Composite {composite:+.2f} "
              f"[F {pillars['fundamental']:+.2f} | P {s_price:+.2f} | I {s_inst:+.2f} | M {s_macro:+.2f}]. "
              f"Flow: OFI {ofi_v:+.2f}, VPIN {vpin_v:.2f}, Hawkes buy/sell {hk['buy']:.2f}/{hk['sell']:.2f}; F&O: {quad.replace('_', ' ').lower()}. "
              + (contradiction[1] if contradiction else "") + (" Macro aligned." if aligned else ""))
    return {
        "symbol": sym, "name": c["name"], "sector": c["sector"], "direction": final, "bias": direction, "setup": setup,
        "conviction": round(conv * 100), "composite": round(composite, 3), "regime": regime, "weights": w,
        "price": {"ltp": round(ltp, 2), "prev_close": round(prev_close, 2), "open": round(opn, 2), "chg_pct": round(chg, 2),
                  "gap_pct": round(gap, 2), "high": round(float(today["high"].max()), 2), "low": round(float(today["low"].min()), 2),
                  "vwap": round(vw, 2), "atr_daily": round(atr_d, 2), "atr_pct": round(100 * atr_d / ltp, 2), "adx": round(adx_v, 1),
                  "turnover_cr": round(turnover_cr, 0)},
        "pillars": {k2: {"score": round(v, 3), "weight": w[k2]} for k2, v in pillars.items()},
        "fundamental": {"engine": news["engine"], "event": news["event"], "sue_proxy": news["sue_proxy"],
                        "divergence": news["divergence"], "headlines": news["items"]},
        "microstructure": {"ofi": round(ofi_v, 3), "vpin": round(vpin_v, 3), "toxic": toxic,
                           "hawkes_buy": round(hk["buy"], 2), "hawkes_sell": round(hk["sell"], 2),
                           "hawkes_surge": hk["surge_buy"] if direction == "LONG" else hk["surge_sell"],
                           "poc": round(poc, 2), "vah": round(vah, 2), "val": round(val_, 2), "volume_expanding": vol_exp,
                           "oi_quadrant": quad, "fut_oi_chg_pct": fut_oi,
                           "iv_skew": None if skew is None else round(skew, 2),
                           "call_writing_otm": bool(opt and opt["call_writing_otm"])},
        "execution": {"option_idea": opt_idea, "entry_trigger": round(entry, 2), "stop": round(stop, 2), "stop_distance": round(stop_dist, 2),
                      "stop_distance_pct": round(100 * stop_dist / ltp, 2), "trail_distance": round(stop_dist, 2),
                      "target": round(t1, 2), "rr": rr, "risk_reward": f"1:{rr:.0f}", "qty": qty, "risk_amount": round(qty * stop_dist, 0),
                      "invalidation_vwap": round(vw, 2), "invalidation_text": inval,
                      "gap_close_target": round(prev_close, 2) if setup == "TRAP_FADE" else None},
        "risk": {"vix": round(vix, 2), "expected_daily_move_pct": round(sig_d, 2), "size_multiplier": size_mult,
                 "circuit": {"upper": round(upper, 2), "lower": round(lower, 2), "dist_upper_pct": round(d_up, 2),
                             "dist_lower_pct": round(d_dn, 2), "band_pct": band},
                 "lpp": lpp, "warnings": warnings, "blockers": blockers},
        "contradiction": {"type": contradiction[0], "note": contradiction[1]} if contradiction else None,
        "thesis": thesis,
    }
