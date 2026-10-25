"""Open market data: Yahoo Finance chart API (prices) and NSE public endpoints (derivatives)."""
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

from .config import IST, RISK_FREE

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


ERRORS = {}


def _note(sym, msg):
    ERRORS.setdefault(msg, []).append(sym)


def _yahoo_http(symbol, rng, interval):
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={rng}&interval={interval}"
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code != 200:
                _note(symbol, f"http_{r.status_code}")
                continue
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame(q, index=pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(IST))
            return df[["open", "high", "low", "close", "volume"]].dropna()
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as e:
            _note(symbol, type(e).__name__)
    return None


def _yahoo_yfinance(symbol, rng, interval):
    try:
        import yfinance as yf  # open-source client; manages Yahoo cookies/crumb
        df = yf.Ticker(symbol).history(period=rng, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            _note(symbol, "yfinance_empty")
            return None
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
        df.index = df.index.tz_convert(IST) if df.index.tz is not None else df.index.tz_localize("UTC").tz_convert(IST)
        return df
    except Exception as e:  # noqa: BLE001
        _note(symbol, "yfinance_" + type(e).__name__)
        return None


def yahoo(symbol, rng, interval):
    df = _yahoo_http(symbol, rng, interval)
    if df is None or not len(df):
        df = _yahoo_yfinance(symbol, rng, interval)
    return df if df is not None and len(df) else None


def fetch_many(symbols, rng, interval, workers=8):
    with ThreadPoolExecutor(workers) as ex:
        return dict(zip(symbols, ex.map(lambda s: yahoo(s, rng, interval), symbols)))


# ---------- NSE derivatives (best effort; NSE often blocks cloud IPs) ----------
class NSE:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({**UA, "Accept": "application/json", "Referer": "https://www.nseindia.com/option-chain"})
        self.ok = False
        try:
            self.s.get("https://www.nseindia.com/option-chain", timeout=15)
            self.ok = True
        except requests.RequestException:
            pass

    def derivatives(self, symbol):
        if not self.ok:
            return None
        try:
            time.sleep(0.35)
            r = self.s.get(f"https://www.nseindia.com/api/quote-derivative?symbol={quote(symbol)}", timeout=15)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None


def _g(d, *path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def parse_derivatives(js, spot):
    """Turn NSE quote-derivative JSON into futures OI change and an options-structure summary."""
    out = {"fut_oi_chg_pct": None, "options": []}
    if not js:
        return out
    now = datetime.now(IST).date()
    best_fut = None
    for row in js.get("stocks", []):
        md = row.get("metadata", {})
        info = _g(row, "marketDeptOrderBook", "tradeInfo") or {}
        itype = (md.get("instrumentType") or "").lower()
        try:
            exp = datetime.strptime(md.get("expiryDate", ""), "%d-%b-%Y").date()
        except ValueError:
            continue
        if "futures" in itype:
            if best_fut is None or exp < best_fut[0]:
                best_fut = (exp, info.get("pchangeinOpenInterest"))
        elif md.get("optionType") in ("Call", "Put"):
            out["options"].append({"exp": exp, "k": float(md.get("strikePrice") or 0), "type": md["optionType"],
                                   "oi": float(info.get("openInterest") or 0), "doi": float(info.get("changeinOpenInterest") or 0),
                                   "iv": float(_g(row, "marketDeptOrderBook", "otherInfo", "impliedVolatility") or md.get("impliedVolatility") or 0),
                                   "ltp": float(md.get("lastPrice") or 0)})
    if best_fut and best_fut[1] is not None:
        out["fut_oi_chg_pct"] = float(best_fut[1])
    opts = [o for o in out["options"] if o["exp"] >= now]
    if opts:
        near = min(o["exp"] for o in opts)
        out["options"] = [o for o in opts if o["exp"] == near]
        out["expiry"] = near
    else:
        out["options"] = []
    return out


def oi_quadrant(price_chg_pct, oi_chg_pct):
    if oi_chg_pct is None:
        return "UNAVAILABLE"
    if price_chg_pct >= 0:
        return "LONG_BUILDUP" if oi_chg_pct >= 0 else "SHORT_COVERING"
    return "SHORT_BUILDUP" if oi_chg_pct >= 0 else "LONG_UNWINDING"


def _bs_gamma(s, k, t, iv):
    if iv <= 0 or t <= 0:
        return 0.0
    d1 = (math.log(s / k) + (RISK_FREE + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (s * iv * math.sqrt(t))


def options_structure(opts, spot, expiry):
    """GEX (gamma x OI x S^2, calls +, puts -), walls, gamma flip, IV skew, OTM call writing."""
    if not opts or not expiry:
        return None
    days = max((expiry - datetime.now(IST).date()).days, 0.5)
    t = days / 365.0
    strikes = sorted({o["k"] for o in opts})
    def net_gex(s):
        tot = 0.0
        per = {}
        for o in opts:
            iv = (o["iv"] or 0) / 100.0
            g = _bs_gamma(s, o["k"], t, iv) * o["oi"] * s * s * 0.01
            g = g if o["type"] == "Call" else -g
            tot += g; per[o["k"]] = per.get(o["k"], 0.0) + g
        return tot, per
    net, per = net_gex(spot)
    gross = sum(abs(v) for v in per.values()) or 1.0
    call_wall = max((o for o in opts if o["type"] == "Call"), key=lambda o: o["oi"], default=None)
    put_wall = max((o for o in opts if o["type"] == "Put"), key=lambda o: o["oi"], default=None)
    flip, grid = None, np.linspace(spot * 0.95, spot * 1.05, 41)
    vals = [net_gex(s)[0] for s in grid]
    for i in range(1, len(grid)):
        if vals[i - 1] * vals[i] < 0:
            flip = float(grid[i - 1] + (grid[i] - grid[i - 1]) * abs(vals[i - 1]) / (abs(vals[i - 1]) + abs(vals[i])))
            break
    def iv_at(kind, target):
        c = [o for o in opts if o["type"] == kind and o["iv"] > 0]
        return min(c, key=lambda o: abs(o["k"] - target))["iv"] if c else None
    ivp, ivc = iv_at("Put", spot * 0.95), iv_at("Call", spot * 1.05)
    otm_calls = sorted((o for o in opts if o["type"] == "Call" and o["k"] > spot), key=lambda o: o["k"])[:2]
    call_writing = bool(otm_calls) and sum(o["doi"] for o in otm_calls) > 0 and \
        sum(o["doi"] for o in otm_calls) > 0.15 * sum(o["oi"] for o in otm_calls)
    return {"net_gex": net, "gex_norm": net / gross, "call_wall": call_wall["k"] if call_wall else None,
            "put_wall": put_wall["k"] if put_wall else None, "gamma_flip": flip,
            "iv_skew": (ivp - ivc) if ivp is not None and ivc is not None else None,
            "call_writing_otm": call_writing, "strikes": len(strikes), "expiry": expiry.isoformat(),
            "atm_iv": iv_at("Call", spot)}
