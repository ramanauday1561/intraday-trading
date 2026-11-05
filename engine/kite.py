"""Zerodha Kite Connect: read-only market data (candles, quotes, option chains, lot sizes).

Safety: this module only calls GET data endpoints. It never places, modifies or cancels orders.

Session: Kite access tokens expire every morning (~06:00 IST) and need an interactive login. The user logs in
from the app (⚙ → Connect Kite); Kite redirects to kite.html with a single-use request_token, which the
kite-session workflow exchanges for an access token using the KITE_API_SECRET repository secret. The token is
encrypted with a key derived from that secret and kept in the Actions cache, so workflows without the secret
(e.g. pull requests from forks) cannot read it.

python -m engine.kite --exchange <request_token>   (used by .github/workflows/kite-session.yml)
python -m engine.kite --check                      (prints whether today's session works)"""
import base64
import csv
import hashlib
import io
import json
import math
import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests
from cryptography.fernet import Fernet, InvalidToken

from .config import IST, RISK_FREE

API = "https://api.kite.trade"
SESSION_FILE = os.path.join(os.environ.get("KITE_SESSION_DIR", ".kite"), "session.enc")
# Yahoo symbols used across the engine -> Kite "EXCHANGE:TRADINGSYMBOL"
INDEX_MAP = {"^NSEI": "NSE:NIFTY 50", "^NSEBANK": "NSE:NIFTY BANK", "^INDIAVIX": "NSE:INDIA VIX", "^CNXIT": "NSE:NIFTY IT",
             "^CNXPHARMA": "NSE:NIFTY PHARMA", "^CNXAUTO": "NSE:NIFTY AUTO", "^CNXFMCG": "NSE:NIFTY FMCG",
             "^CNXMETAL": "NSE:NIFTY METAL", "^CNXENERGY": "NSE:NIFTY ENERGY"}


class KiteError(Exception):
    pass


def kite_symbol(yahoo_symbol):
    if yahoo_symbol in INDEX_MAP:
        return INDEX_MAP[yahoo_symbol]
    return "NSE:" + yahoo_symbol.removesuffix(".NS")


# ---------------- session ----------------
def checksum(api_key, request_token, api_secret):
    return hashlib.sha256((api_key + request_token + api_secret).encode()).hexdigest()


def _fernet(api_secret):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("kite-session:" + api_secret).encode()).digest()))


def save_session(data, api_secret, path=SESSION_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(_fernet(api_secret).encrypt(json.dumps(data).encode()))


def read_session(api_secret, path=SESSION_FILE):
    try:
        with open(path, "rb") as f:
            return json.loads(_fernet(api_secret).decrypt(f.read()))
    except (OSError, InvalidToken, ValueError):
        return None


def session_fresh(sess, now=None):
    """Kite tokens die at ~06:00 IST the next morning; treat a token as fresh only after today's 06:00."""
    if not sess or not sess.get("created"):
        return False
    now = now or datetime.now(IST)
    cutoff = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now < cutoff:
        cutoff -= timedelta(days=1)
    return datetime.fromisoformat(sess["created"]) >= cutoff


def exchange(request_token, api_key, api_secret):
    r = requests.post(f"{API}/session/token", headers={"X-Kite-Version": "3"}, timeout=20,
                      data={"api_key": api_key, "request_token": request_token, "checksum": checksum(api_key, request_token, api_secret)})
    js = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or js.get("status") != "success":
        raise KiteError(f"session exchange failed ({r.status_code}): {js.get('message') or r.text[:120]}")
    d = js["data"]
    return {"access_token": d["access_token"], "user_id": d.get("user_id"), "created": datetime.now(IST).isoformat(timespec="seconds")}


# ---------------- client ----------------
class Kite:
    def __init__(self, api_key, access_token, min_interval=0.36):
        self.s = requests.Session()
        self.s.headers.update({"X-Kite-Version": "3", "Authorization": f"token {api_key}:{access_token}"})
        self.min_interval, self._last = min_interval, 0.0  # historical API allows ~3 requests/second
        self._inst = {}

    def _get(self, path, params=None, raw=False):
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        r = self.s.get(f"{API}{path}", params=params, timeout=30)
        if r.status_code == 403:
            raise KiteError("session expired or not permitted (403)")
        if r.status_code != 200:
            raise KiteError(f"{path}: HTTP {r.status_code} {r.text[:120]}")
        if raw:
            return r.text
        js = r.json()
        if js.get("status") != "success":
            raise KiteError(f"{path}: {js.get('message')}")
        return js["data"]

    def instruments(self, exchange):
        if exchange not in self._inst:
            self._inst[exchange] = parse_instruments(self._get(f"/instruments/{exchange}", raw=True))
        return self._inst[exchange]

    def quote(self, keys):
        out = {}
        for i in range(0, len(keys), 500):  # 500 instruments per call
            out.update(self._get("/quote", params=[("i", k) for k in keys[i:i + 500]]))
        return out

    def candles(self, token, interval, start, end):
        fmt = "%Y-%m-%d %H:%M:%S"
        data = self._get(f"/instruments/historical/{token}/{interval}", params={"from": start.strftime(fmt), "to": end.strftime(fmt)})
        return parse_candles(data.get("candles", []))


def load_client():
    """Kite client for today's session, or None (no secrets, no session, or session from before 06:00)."""
    key, secret = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_API_SECRET")
    if not key or not secret:
        return None
    sess = read_session(secret)
    if not session_fresh(sess):
        return None
    return Kite(key, sess["access_token"])


# ---------------- parsing ----------------
def parse_instruments(text):
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        rows.append({"token": int(r["instrument_token"]), "symbol": r["tradingsymbol"], "name": r["name"].strip('"'),
                     "expiry": date.fromisoformat(r["expiry"]) if r.get("expiry") else None,
                     "strike": float(r["strike"] or 0), "lot": int(float(r["lot_size"] or 0)),
                     "type": r["instrument_type"], "segment": r["segment"], "exchange": r["exchange"]})
    return rows


def parse_candles(candles):
    if not candles:
        return None
    df = pd.DataFrame([c[:6] for c in candles], columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("ts")).dt.tz_convert(IST)
    return df.astype(float)


# ---------------- market data for the engine ----------------
def fetch_bars(kc, yahoo_symbols, now, intraday_days=7, daily_days=380):
    """{yahoo_symbol: 5-minute frame}, {yahoo_symbol: daily frame} from Kite; symbols that fail are left out
    (the caller falls back to Yahoo for them)."""
    nse = {f"NSE:{r['symbol']}": r["token"] for r in kc.instruments("NSE")}
    i5, d1 = {}, {}
    end = now.replace(tzinfo=None)
    for ys in yahoo_symbols:
        tok = nse.get(kite_symbol(ys))
        if tok is None:
            continue
        try:
            i5[ys] = kc.candles(tok, "5minute", end - timedelta(days=intraday_days), end)
            d1[ys] = kc.candles(tok, "day", end - timedelta(days=daily_days), end)
        except KiteError:
            i5.pop(ys, None)
    return i5, d1


def bs_price(s, k, t, vol, call):
    if vol <= 0 or t <= 0:
        return max(0.0, (s - k) if call else (k - s))
    d1 = (math.log(s / k) + (RISK_FREE + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if call:
        return s * n(d1) - k * math.exp(-RISK_FREE * t) * n(d2)
    return k * math.exp(-RISK_FREE * t) * n(-d2) - s * n(-d1)


def implied_vol(price, s, k, t, call):
    """Bisection on Black-Scholes; None when the price is outside no-arbitrage bounds."""
    if price <= 0 or t <= 0:
        return None
    lo, hi = 0.01, 3.0
    if not bs_price(s, k, t, lo, call) <= price <= bs_price(s, k, t, hi, call):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs_price(s, k, t, mid, call) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def option_chains(kc, spots, prev_oi=None, today=None, width=0.08, min_days=0):
    """Per underlying (Kite name, e.g. 'INFY', 'NIFTY'): nearest expiry with at least `min_days` left, strikes within
    +/-width of spot, as rows the engine's options_structure() understands (IV in %, OI, LTP), plus futures OI,
    its change vs the previous close snapshot (prev_oi) and the lot size."""
    today = today or datetime.now(IST).date()
    nfo = kc.instruments("NFO")
    by_name = {}
    for r in nfo:
        if r["name"] in spots and r["expiry"] and r["expiry"] >= today:
            by_name.setdefault(r["name"], []).append(r)
    wanted, plan = [], {}
    for name, rows in by_name.items():
        s = spots[name]
        opt_exp = sorted({r["expiry"] for r in rows if r["type"] in ("CE", "PE") and (r["expiry"] - today).days >= min_days})
        fut = sorted((r for r in rows if r["type"] == "FUT"), key=lambda r: r["expiry"])
        if not opt_exp:
            continue
        exp = opt_exp[0]
        legs = [r for r in rows if r["expiry"] == exp and r["type"] in ("CE", "PE") and abs(r["strike"] / s - 1) <= width]
        plan[name] = {"expiry": exp, "legs": legs, "fut": fut[0] if fut else None, "all_expiries": opt_exp}
        wanted += [f"NFO:{r['symbol']}" for r in legs] + ([f"NFO:{fut[0]['symbol']}"] if fut else [])
    q = kc.quote(wanted) if wanted else {}
    out = {}
    for name, p in plan.items():
        s, t = spots[name], max((p["expiry"] - today).days, 0.5) / 365.0
        rows = []
        for r in p["legs"]:
            d = q.get(f"NFO:{r['symbol']}") or {}
            ltp, oi = float(d.get("last_price") or 0), float(d.get("oi") or 0)
            iv = implied_vol(ltp, s, r["strike"], t, r["type"] == "CE") if ltp > 0 else None
            rows.append({"exp": p["expiry"], "k": r["strike"], "type": "Call" if r["type"] == "CE" else "Put", "oi": oi, "doi": 0.0,
                         "iv": 100 * iv if iv else 0.0, "ltp": ltp, "lot": r["lot"], "symbol": r["symbol"]})
        fut_oi = float(((q.get(f"NFO:{p['fut']['symbol']}") or {}).get("oi")) or 0) if p["fut"] else None
        prev = (prev_oi or {}).get(name)
        out[name] = {"options": rows, "expiry": p["expiry"], "lot": p["legs"][0]["lot"] if p["legs"] else None,
                     "fut_oi": fut_oi, "fut_oi_chg_pct": (100 * (fut_oi / prev - 1)) if fut_oi and prev else None}
    return out


def futures_oi(kc, names, today=None):
    """Near-month futures open interest per underlying (for the end-of-day snapshot used as 'previous OI')."""
    today = today or datetime.now(IST).date()
    near = {}
    for r in kc.instruments("NFO"):
        if r["type"] == "FUT" and r["name"] in names and r["expiry"] and r["expiry"] >= today:
            if r["name"] not in near or r["expiry"] < near[r["name"]]["expiry"]:
                near[r["name"]] = r
    q = kc.quote([f"NFO:{r['symbol']}" for r in near.values()]) if near else {}
    return {n: float((q.get(f"NFO:{r['symbol']}") or {}).get("oi") or 0) for n, r in near.items()}


def kite_name(sym):
    return {"^NSEI": "NIFTY", "^NSEBANK": "BANKNIFTY"}.get(sym, sym)


if __name__ == "__main__":
    import sys
    key, secret = os.environ.get("KITE_API_KEY"), os.environ.get("KITE_API_SECRET")
    if not key or not secret:
        sys.exit("KITE_API_KEY / KITE_API_SECRET repository secrets are not set")
    if "--exchange" in sys.argv:
        sess = exchange(sys.argv[sys.argv.index("--exchange") + 1].strip(), key, secret)
        print(f"::add-mask::{sess['access_token']}")
        save_session(sess, secret)
        print("Kite session saved for user", (sess.get("user_id") or "?")[:2] + "****")
    elif "--check" in sys.argv:
        kc = load_client()
        if not kc:
            print("no fresh Kite session today (log in from the app: ⚙ → Connect Kite)")
        else:
            q = kc.quote(["NSE:NIFTY 50"])
            print("Kite OK: NIFTY", q.get("NSE:NIFTY 50", {}).get("last_price"))
