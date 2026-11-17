"""Order server for the app's stock signals, real money (16 Nov 2026, asked by the owner: "automatic", "trade the
stocks", capital Rs20,000, risk 5% a trade, most to lose in a day Rs5,000). It runs on the owner's Mumbai VPS beside
the commodities-trading order server, because Zerodha accepts API orders only from a whitelisted static IP, and uses
the same Kite API key and the same morning login.

What it does. The engine runs on this box after each 15-minute candle (server/signal_run.sh) and writes today's
journal (today_log.json). For each NEW journal entry made in the last few minutes this server:
  1. sizes it like the paper record: quantity = risk rupees x the signal's size multiplier / the stop distance,
     cut down to the margin left of the capital (skipped if less than half fits);
  2. enters with a LIMIT order just through the best price (re-priced up to 3 times, then cancelled: never a MARKET
     order), BUY for a LONG signal, SELL for a SHORT one, intraday (MIS);
  3. as soon as it fills, places the stop-loss order at the exchange (SL: the signal's stop distance from the real
     fill), so the stop holds even if this server goes down;
  4. watches the price every 2 seconds: at the target (rr x the stop distance) it closes; once the price has moved
     the breakeven distance in favour it moves the stop order to the entry price; at EXIT_T it closes everything.
     Stocks stop trading normally at 15:15 (the closing auction, since 3 Aug 2026), so EXIT_T is 15:05, not the
     paper record's 15:15.
Each journal entry is tried once, also when it is refused. Hard limits live on this server only
(/etc/intraday-orders.env): the capital, the risk a trade and INTRADAY_MAX_LOSS_DAY: when today's realised plus open
loss after charges reaches it, everything is closed and nothing new is taken until tomorrow. "Stop everything" on the
phone does the same at once. INTRADAY_MODE=practice does everything except send orders (fills at Kite's live bid and
ask), =off takes nothing.

Honest note: the signals have not passed their backtests (about -0.04R to -0.07R a trade after costs) and the live
record is too short to tell. This trades them anyway, at the owner's request.

Privacy: trades, orders and P&L stay on this server's disk and the owner's phone; nothing goes to the repository and
the log prints no prices or quantities. The phone talks over HTTPS (Caddy) with a long passcode made at setup;
10 wrong passcodes in an hour lock the API for an hour.

Usage: python3 server/orders_server.py     (systemd runs it; settings from the environment, see setup.sh)
"""
import hmac
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from engine import charges as zcharges, shared_session  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
API = "https://api.kite.trade"
TAG = "intraday"
OPEN_T, LAST_ENTRY_T, EXIT_T = "09:15", "14:45", "15:05"
ENTRY_TRIES, ENTRY_WAIT, EXIT_WAIT, SL_WAIT = 3, 10, 5, 10       # re-prices; seconds between them
THROUGH = 0.001                 # a "now" limit order is priced this far through the best price (0.1%)
SL_GAP = 0.003                  # the stop order's limit is this far beyond its trigger
BAR_MIN, SIGNAL_MAX_AGE = 5, 300    # a signal is acted on up to this many seconds after its 5-minute bar closed
MAX_DRIFT = 0.5                 # ... and only while the price is within this many stop distances of the signal's
LEVERAGE = 5.0                  # as engine/config.py: notional capped at 5x capital, and the margin guess
MIN_FIT = 0.5                   # a signal that fits less than this share of its size in the margin left is skipped
SESSION_URL = "https://raw.githubusercontent.com/ramanauday1561/commodities-trading/kite-session/session.json"
LIVE = ("entering", "open", "exiting")


def tick_round(x, tick, mode=0):
    """x on the tick grid: mode -1 down, +1 up, 0 nearest."""
    n = round(x / tick, 6)
    n = n // 1 if mode < 0 else -(-n // 1) if mode > 0 else round(n)
    return max(tick, round(n * tick, 2))


def hm(now):
    return now.strftime("%H:%M")


class KiteError(RuntimeError):
    pass


class Kite:
    """The few Kite Connect calls the server needs (NSE stocks, MIS)."""

    def __init__(self, key, token):
        self.key, self.token = key, token

    def _call(self, method, path, data=None, raw=False, as_json=False):
        h = {"X-Kite-Version": "3", "User-Agent": "intraday-orders", "Authorization": f"token {self.key}:{self.token}"}
        if as_json:
            body, h["Content-Type"] = json.dumps(data).encode(), "application/json"
        else:
            body = urllib.parse.urlencode(data).encode() if data is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(API + path, data=body, headers=h, method=method), timeout=15) as f:
                return f.read().decode() if raw else json.load(f)["data"]
        except urllib.error.HTTPError as e:
            try:
                j = json.load(e)
            except Exception:
                j = {}
            raise KiteError(f"{j.get('error_type', 'HTTP ' + str(e.code))}: {j.get('message', '')}".strip()) from None
        except OSError as e:                                           # timeouts, DNS: the caller treats it as "Kite said no"
            raise KiteError(f"network: {type(e).__name__}") from None

    def place(self, symbol, side, qty, order_type, price, trigger=None):
        d = {"exchange": "NSE", "tradingsymbol": symbol, "transaction_type": side, "quantity": qty, "product": "MIS",
             "order_type": order_type, "price": f"{price:.2f}", "validity": "DAY", "tag": TAG}
        if trigger is not None:
            d["trigger_price"] = f"{trigger:.2f}"
        return self._call("POST", "/orders/regular", d)["order_id"]

    def modify(self, order_id, price, order_type="LIMIT", trigger=None):
        d = {"order_type": order_type, "price": f"{price:.2f}"}
        if trigger is not None:
            d["trigger_price"] = f"{trigger:.2f}"
        self._call("PUT", f"/orders/regular/{order_id}", d)

    def cancel(self, order_id):
        self._call("DELETE", f"/orders/regular/{order_id}")

    def orders(self):
        return {o["order_id"]: o for o in self._call("GET", "/orders")}

    def quote(self, symbols):
        q = "&".join("i=" + urllib.parse.quote(f"NSE:{s}") for s in symbols)
        return {k[4:]: v for k, v in self._call("GET", f"/quote?{q}").items()}

    def _legs(self, legs):
        """Rows for Kite's margin and charges calls from (symbol, side, qty, price[, exchange, product])."""
        return [{"exchange": (x[4:5] or ["NSE"])[0], "tradingsymbol": x[0], "transaction_type": x[1], "variety": "regular",
                 "product": (x[5:6] or ["MIS"])[0], "order_type": "LIMIT", "quantity": int(x[2]), "price": float(x[3])} for x in map(list, legs)]

    def charges(self, legs):
        """Kite's own charges in rupees for these legs."""
        rows = [{**r, "order_id": str(i), "average_price": r["price"]} for i, r in enumerate(self._legs(legs), 1)]
        return round(sum(float((r.get("charges") or {}).get("total") or 0) for r in self._call("POST", "/charges/orders", rows, as_json=True)), 2)

    def margin(self, symbol, side, qty, price):
        """The margin Kite blocks for this order, in rupees."""
        r = self._call("POST", "/margins/orders", self._legs([(symbol, side, qty, price)]), as_json=True)
        return float(r[0]["total"])

    def funds(self):
        return self._call("GET", "/user/margins/equity")

    def positions(self):
        return self._call("GET", "/portfolio/positions").get("net", [])

    def instruments(self):
        import csv
        import io
        return list(csv.DictReader(io.StringIO(self._call("GET", "/instruments/NSE", raw=True))))


def best(q):
    """(ltp, bid, ask) from a full quote."""
    d = q.get("depth") or {}
    bid = next((x["price"] for x in d.get("buy", []) if x.get("price")), None)
    ask = next((x["price"] for x in d.get("sell", []) if x.get("price")), None)
    return q.get("last_price"), bid, ask


class Engine:
    """Today's trades and the orders for them. One lock; the HTTP threads and the 2-second loop both use it."""

    def __init__(self, state_path, limits, now=None, clock=time.time, history_path=None):
        self.path, self.limits, self.history_path = state_path, limits, history_path
        self.now = now or (lambda: datetime.now(IST))
        self.clock = clock
        self.lock = threading.RLock()
        self.kite, self.ticks, self.ticks_day = None, {}, None
        self.mode = "real"                        # "real", "practice" (no orders) or "off" (INTRADAY_MODE)
        self.state = {"day": None, "locked": None, "trades": [], "done": []}
        try:
            self.state.update(json.load(open(state_path)))
        except (OSError, ValueError):
            pass
        self._new_day()

    # ------------------------------------------------------------------ state
    def _new_day(self):
        today = self.now().date().isoformat()
        if self.state.get("day") != today:
            for t in self.state["trades"]:                    # MIS: Zerodha squared off anything left yesterday
                if t["status"] in LIVE:
                    print(f"trade {t['id']} was still {t['status']} at the end of {self.state.get('day')}: check Kite", flush=True)
            self.state = {"day": today, "locked": None, "trades": [], "done": []}
            self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.path)

    def log(self, t, msg):
        t.setdefault("log", []).append(f"{hm(self.now())} {msg}")

    def tick_of(self, symbol):
        """The stock's price step, or None when it is not an NSE stock trading today."""
        today = self.now().date().isoformat()
        if self.kite and self.ticks_day != today:
            self.ticks = {r["tradingsymbol"]: float(r.get("tick_size") or 0.05) for r in self.kite.instruments()
                          if r.get("instrument_type") == "EQ" and r.get("segment") == "NSE"}
            self.ticks_day = today
        return self.ticks.get(symbol)

    def _charges(self, legs):
        """(rupees, "kite" or "estimate"): Kite's charges for these legs, or Zerodha's published schedule when Kite cannot be reached."""
        try:
            return self.kite.charges(legs), "kite"
        except Exception as e:                                          # never block an exit on a charges lookup
            print(f"charges: {type(e).__name__}", flush=True)
            (_, side, qty, p_in), (_, _, _, p_out) = legs[0][:4], legs[1][:4]           # Zerodha's schedule (engine/charges.py)
            return zcharges.intraday(p_in, p_out, qty, side == "BUY", slippage=False), "estimate"

    def _margin(self, sym, side, qty, price):
        try:
            return self.kite.margin(sym, side, qty, price)
        except Exception as e:
            print(f"margin: {type(e).__name__}", flush=True)
            return qty * price / LEVERAGE

    def used(self):
        return sum(t.get("margin", 0) for t in self.state["trades"] if t["status"] in LIVE + ("practice",))

    # ------------------------------------------------------------------ a new trade from a signal
    def add(self, sig):
        """sig: one journal entry {symbol, direction, ref_price, stop_distance, rr, breakeven_after_r, size_mult,
        signal_time}. Returns the trade id; ValueError says why it was not taken."""
        with self.lock:
            self._new_day()
            now, L = self.now(), self.limits
            sym, long_ = str(sig.get("symbol", "")).strip().upper(), sig.get("direction") == "LONG"
            if sig.get("direction") not in ("LONG", "SHORT"):
                raise ValueError("not a LONG or SHORT signal")
            try:
                sd, rr, ref0 = float(sig["stop_distance"]), float(sig["rr"]), float(sig["ref_price"])
                be = float(sig["breakeven_after_r"]) if sig.get("breakeven_after_r") is not None else None
                mult = float(sig.get("size_mult") or 1.0)
            except (KeyError, TypeError, ValueError):
                raise ValueError("the signal has no stop distance, target or price")
            if self.mode == "off":
                raise ValueError("orders are switched off (INTRADAY_MODE=off)")
            if not self.kite:
                raise ValueError("no Kite login today")
            if self.state["locked"]:
                raise ValueError(f"locked for today: {self.state['locked']}")
            if not (OPEN_T <= hm(now) < LAST_ENTRY_T) or now.weekday() >= 5:
                raise ValueError(f"trades are taken from {OPEN_T} to {LAST_ENTRY_T} on market days")
            tick = self.tick_of(sym)
            if not tick:
                raise ValueError(f"{sym or 'that'} is not an NSE stock trading today")
            if any(t["symbol"] == sym and t["status"] in LIVE + ("practice",) for t in self.state["trades"]):
                raise ValueError(f"{sym} already has an open trade")
            live = [t for t in self.state["trades"] if t["status"] in LIVE + ("practice",)]
            if L["max_open"] and len(live) >= L["max_open"]:
                raise ValueError(f"already {len(live)} open trades (INTRADAY_MAX_OPEN {L['max_open']})")
            ltp, bid, ask = best(self.kite.quote([sym]).get(sym, {}))
            ref = (ask if long_ else bid) or ltp
            if not ref or sd <= 0 or rr <= 0:
                raise ValueError("no price for that stock right now")
            if abs(ref - ref0) > MAX_DRIFT * sd:
                raise ValueError(f"the price has moved more than {MAX_DRIFT} stop distances since the signal")
            side = "BUY" if long_ else "SELL"
            qty = min(int(L["capital"] * L["risk_pct"] / 100 * mult / sd), int(L["capital"] * LEVERAGE / ref))
            if qty < 1:
                raise ValueError("the stop is too wide for even one share at this risk")
            margin, left = self._margin(sym, side, qty, ref), L["capital"] - self.used()
            if margin > left:
                fit = int(qty * left / margin) if margin > 0 else 0
                if fit < 1 or fit < MIN_FIT * qty:
                    raise ValueError("not enough of the capital left for at least half the size")
                margin, qty = margin * fit / qty, fit
            est, _ = self._charges([(sym, side, qty, ref), (sym, "SELL" if long_ else "BUY", qty, ref)])
            risk = sd * qty + est
            if not self.practice and risk > L["max_loss_day"] + self._pnl_today():
                raise ValueError("this trade's risk is more than what is left of today's loss limit")
            t = {"id": uuid.uuid4().hex[:8], "symbol": sym, "side": "LONG" if long_ else "SHORT", "qty": qty, "tick": tick,
                 "sd": sd, "rr": rr, "be": be, "ref": ref0, "signal": sig.get("signal_time"), "margin": round(margin, 2),
                 "made": now.isoformat(timespec="seconds"), "status": "entering", "tries": 0, "since": self.clock(),
                 "filled": 0, "avg_in": None, "avg_out": None, "stop": None, "target": None, "moved": False,
                 "ltp": ltp, "why": None, "charges_est": est}
            if self.practice:
                t.update(status="practice", practice=True, filled=qty, avg_in=ref)
                self._levels(t)
                self.log(t, "practice: no order sent")
            else:
                t["entry_price"] = tick_round(ref * (1 + THROUGH if long_ else 1 - THROUGH), tick, 1 if long_ else -1)
                try:
                    t["entry_id"] = self.kite.place(sym, side, qty, "LIMIT", t["entry_price"])
                except KiteError as e:
                    raise ValueError(f"Kite refused the order: {e}")
                self.log(t, f"{side.lower()} {qty} limit {t['entry_price']}")
            self.state["trades"].append(t)
            self.save()
            return t["id"]

    @property
    def practice(self):
        return self.mode == "practice"

    def _levels(self, t):
        s = 1 if t["side"] == "LONG" else -1
        t["stop"] = tick_round(t["avg_in"] - s * t["sd"], t["tick"])
        t["target"] = tick_round(t["avg_in"] + s * t["rr"] * t["sd"], t["tick"])
        t["be_at"] = None if t["be"] is None else t["avg_in"] + s * t["be"] * t["sd"]

    # ------------------------------------------------------------------ the phone's requests
    def view(self):
        with self.lock:
            hide = ("entry_id", "sl_id", "exit_id")
            return {"day": self.state["day"], "now": self.now().isoformat(timespec="seconds"), "kite": bool(self.kite),
                    "mode": self.mode, "locked": self.state["locked"], "limits": self.limits, "used": round(self.used(), 2),
                    "day_pnl": round(self._pnl_today(), 2),
                    "practice_pnl": round(sum(self._pnl(t) for t in self.state["trades"] if t.get("practice")), 2),
                    "trades": [{k: v for k, v in t.items() if k not in hide} for t in self.state["trades"]]}

    def exit(self, tid, why="closed from the phone"):
        with self.lock:
            t = next((x for x in self.state["trades"] if x["id"] == tid), None)
            if not t:
                raise ValueError("no such trade")
            self._exit(t, why)
            self.save()

    def stop_all(self, why="stop everything from the phone"):
        with self.lock:
            self.state["locked"] = why
            self._exit_all(why)
            self.save()

    def _exit_all(self, why):
        for t in self.state["trades"]:
            if t["status"] in ("entering", "open", "practice"):
                try:
                    self._exit(t, why)
                except KiteError as e:                                 # the loop tries again every 2 seconds
                    self.log(t, f"Kite: {e}")

    def money(self):
        """This app's own money only (17 Nov 2026, asked by the owner: "both the repos money should be different"):
        its trades' real fills after Kite's charges, on top of the capital set aside for it. The Kite account is one
        pool shared with the NIFTY app, so its balance is shown only as "free in Kite". One row of totals a day is
        kept on this server."""
        with self.lock:
            self._new_day()
            mine = [t for t in self.state["trades"] if t.get("avg_in") and not t.get("practice")]
            net = round(sum(self._pnl(t) for t in mine), 2)
            ch = round(sum(t.get("charges", t.get("charges_est", 0)) for t in mine), 2)
            day = {"v": 2, "pnl": round(net + ch, 2), "charges": ch, "net": net, "trades": len(mine)}
            hist = {}
            if self.history_path:
                try:
                    hist = {d: v for d, v in json.load(open(self.history_path)).items() if v.get("v") == 2}   # older rows were the whole account
                except (OSError, ValueError, AttributeError):
                    pass
                hist[self.state["day"]] = day
                with open(self.history_path + ".tmp", "w") as fh:
                    json.dump(hist, fh)
                os.replace(self.history_path + ".tmp", self.history_path)
            cap, used = self.limits["capital"], round(self.used(), 2)
            start = round(cap + sum(v["net"] for d, v in hist.items() if d != self.state["day"]), 2)
            out = {"day": self.state["day"], "now": self.now().isoformat(timespec="seconds"), "capital": cap, "used": used,
                   "today": {**day, "start": start, "now": round(start + net, 2)}, "free": round(max(0.0, cap - used), 2),
                   "kite_free": None, "history": [{"d": d, **v} for d, v in sorted(hist.items())[-90:]]}
            k = self.kite
        if k:                                                           # outside the lock: a slow Kite must not hold the loop
            try:
                out["kite_free"] = round(float(k.funds().get("net") or 0), 2)
            except Exception as e:
                print(f"funds: {type(e).__name__}", flush=True)
        return out

    # ------------------------------------------------------------------ the 2-second loop
    def tick(self):
        with self.lock:
            self._new_day()
            live = [t for t in self.state["trades"] if t["status"] in LIVE + ("practice",)]
            if not live or not self.kite:
                return
            now = self.now()
            orders = self.kite.orders() if any(t["status"] in LIVE for t in live) else {}
            quotes = self.kite.quote(sorted({t["symbol"] for t in live}))
            for t in live:
                ltp, bid, ask = best(quotes.get(t["symbol"], {}))
                t["ltp"], t["bid"], t["ask"] = ltp, bid, ask
                try:
                    getattr(self, "_" + t["status"])(t, orders, ltp, bid, ask)
                except KiteError as e:
                    self.log(t, f"Kite: {e}")
            if self.state["locked"] is None and self._pnl_today() <= -self.limits["max_loss_day"]:
                self.state["locked"] = f"today's loss reached Rs{self.limits['max_loss_day']:,} (INTRADAY_MAX_LOSS_DAY)"
                self._exit_all(self.state["locked"])
            if hm(now) >= EXIT_T:
                self._exit_all("exit time")
            self.save()

    def _entering(self, t, orders, ltp, bid, ask):
        o = orders.get(t["entry_id"], {})
        st, long_ = o.get("status"), t["side"] == "LONG"
        if st == "COMPLETE" or (st in ("REJECTED", "CANCELLED") and int(o.get("filled_quantity") or 0)):
            t["filled"], t["avg_in"] = int(o["filled_quantity"]), float(o["average_price"])   # a part fill: keep what we have
            t["margin"] = round(t["margin"] * t["filled"] / t["qty"], 2)
            self.log(t, f"filled {t['filled']} at {t['avg_in']}")
            return self._protect(t)
        if st in ("REJECTED", "CANCELLED"):
            t["status"], t["why"] = "not entered", t.get("why") or o.get("status_message") or st.lower()
            return self.log(t, f"entry {st.lower()}: {t['why']}")
        if t.get("cancelling"):                                       # waiting for Kite to confirm the cancel
            return
        ref = (ask if long_ else bid) or ltp
        if self.clock() - t["since"] >= ENTRY_WAIT:
            if t["tries"] >= ENTRY_TRIES or not ref or abs(ref - t["ref"]) > MAX_DRIFT * t["sd"]:
                return self._cancel_entry(t, "not filled after re-pricing" if t["tries"] >= ENTRY_TRIES else "the price moved away")
            t["tries"] += 1
            t["entry_price"], t["since"] = tick_round(ref * (1 + THROUGH if long_ else 1 - THROUGH), t["tick"], 1 if long_ else -1), self.clock()
            self.kite.modify(t["entry_id"], t["entry_price"])
            self.log(t, f"re-priced the entry to {t['entry_price']}")

    def _cancel_entry(self, t, why):
        t["cancelling"], t["why"] = True, why
        try:
            self.kite.cancel(t["entry_id"])
        except KiteError:                                              # it may have just filled: the next tick sees it
            pass

    def _sl_prices(self, t, trig):
        """(trigger, limit) of the stop order: the limit a little beyond the trigger, on the losing side."""
        s = 1 if t["side"] == "LONG" else -1
        gap = max(t["tick"], trig * SL_GAP)
        return trig, tick_round(trig - s * gap, t["tick"], -s)

    def _protect(self, t):
        self._levels(t)
        trig, price = self._sl_prices(t, t["stop"])
        t["status"], t["since"] = "open", self.clock()
        try:
            t["sl_id"] = self.kite.place(t["symbol"], "SELL" if t["side"] == "LONG" else "BUY", t["filled"], "SL", price, trigger=trig)
        except KiteError as e:                                         # no stop at the exchange: do not hold the trade
            t["sl_id"] = None
            self.log(t, f"the stop order was refused: {e}")
            return self._exit(t, "no stop order could be placed; closed")
        self.log(t, f"stop-loss at the exchange: trigger {trig}, limit {price}")

    def _open(self, t, orders, ltp, bid, ask):
        s, long_ = (1 if t["side"] == "LONG" else -1), t["side"] == "LONG"
        if not t.get("sl_id"):
            return self._exit(t, t.get("why") or "no stop order; closed")
        o = orders.get(t["sl_id"], {})
        st = o.get("status")
        if st == "COMPLETE":
            return self._closed(t, float(o["average_price"]), "breakeven exit" if t["moved"] else "stopped out")
        if st in ("REJECTED", "CANCELLED"):                          # no stop at the exchange any more: get out
            self.log(t, f"the stop order was {st.lower()}: {o.get('status_message') or ''}")
            t["sl_id"] = None
            return self._exit(t, "stop order rejected; closed")
        if st == "OPEN":                                             # triggered, limit not reached
            px = bid if long_ else ask
            if self.clock() - t.setdefault("sl_seen", self.clock()) >= SL_WAIT and px:
                p = tick_round(px * (1 - s * THROUGH), t["tick"], -s)
                self.kite.modify(t["sl_id"], p)
                t["sl_seen"] = self.clock()
                self.log(t, f"the stop triggered; re-priced to {p}")
            return
        if ltp is None:
            return
        if s * (ltp - t["target"]) >= 0:
            return self._exit(t, "target reached")
        if t["be_at"] is not None and not t["moved"] and s * (ltp - t["be_at"]) >= 0:
            trig, price = self._sl_prices(t, tick_round(t["avg_in"], t["tick"]))
            self.kite.modify(t["sl_id"], price, order_type="SL", trigger=trig)
            t["moved"], t["stop"] = True, trig
            self.log(t, f"stop moved to the entry price {trig}")

    def _exiting(self, t, orders, ltp, bid, ask):
        o = orders.get(t.get("exit_id"), {})
        st, long_ = o.get("status"), t["side"] == "LONG"
        if st == "COMPLETE":
            return self._closed(t, float(o["average_price"]), t["why"])
        px = (bid if long_ else ask) or ltp
        if st in ("REJECTED", "CANCELLED") or not t.get("exit_id"):
            self.log(t, f"the closing order was {(st or 'not placed').lower()}: {o.get('status_message') or ''}; trying again")
            return self._send_exit(t, px)
        if self.clock() - t["since"] >= EXIT_WAIT and px:
            p = self._exit_price(t, px)
            self.kite.modify(t["exit_id"], p)
            t["since"] = self.clock()
            self.log(t, f"re-priced the closing order to {p}")

    def _exit(self, t, why):
        if t["status"] == "practice":
            ltp, bid, ask = t.get("ltp"), t.get("bid"), t.get("ask")
            return self._closed(t, (bid if t["side"] == "LONG" else ask) or ltp or t["avg_in"], why + " (practice)")
        if t["status"] == "entering":
            if not t.get("cancelling"):
                self._cancel_entry(t, why)
                self.log(t, f"cancelling the entry: {why}")
            return
        if t["status"] != "open":
            return
        t["why"] = why
        if t.get("sl_id"):
            try:
                self.kite.cancel(t["sl_id"])
            except KiteError:                                          # it may have just filled
                o = self.kite.orders().get(t["sl_id"], {})
                if o.get("status") == "COMPLETE":
                    return self._closed(t, float(o["average_price"]), "stopped out")
                raise
            t["sl_id"] = None
        t["status"], t["since"], t["exit_id"] = "exiting", self.clock(), None
        self.log(t, f"closing: {why}")
        ltp, bid, ask = best(self.kite.quote([t["symbol"]]).get(t["symbol"], {}))
        self._send_exit(t, (bid if t["side"] == "LONG" else ask) or ltp)

    def _exit_price(self, t, px):
        s = 1 if t["side"] == "LONG" else -1
        return tick_round(px * (1 - s * 2 * THROUGH), t["tick"], -s)

    def _send_exit(self, t, px):
        px = px or t.get("ltp") or t["avg_in"]
        t["exit_id"] = self.kite.place(t["symbol"], "SELL" if t["side"] == "LONG" else "BUY", t["filled"], "LIMIT", self._exit_price(t, px))
        t["since"] = self.clock()

    def _practice(self, t, orders, ltp, bid, ask):
        s = 1 if t["side"] == "LONG" else -1
        if ltp is None:
            return
        if s * (ltp - t["stop"]) <= 0:
            return self._closed(t, t["stop"], ("breakeven exit" if t["moved"] else "stopped out") + " (practice)")
        if s * (ltp - t["target"]) >= 0:
            return self._closed(t, t["target"], "target reached (practice)")
        if t["be_at"] is not None and not t["moved"] and s * (ltp - t["be_at"]) >= 0:
            t["moved"], t["stop"] = True, tick_round(t["avg_in"], t["tick"])
            self.log(t, "stop moved to the entry price")

    def _closed(self, t, avg_out, why):
        s = 1 if t["side"] == "LONG" else -1
        a, b = ("BUY", "SELL") if s > 0 else ("SELL", "BUY")
        t["status"], t["avg_out"], t["why"] = "closed", avg_out, why
        t["pnl"] = round(s * (avg_out - t["avg_in"]) * t["filled"], 2)
        c, _ = self._charges([(t["symbol"], a, t["filled"], t["avg_in"]), (t["symbol"], b, t["filled"], avg_out)])
        t["charges"], t["net"] = c, round(t["pnl"] - c, 2)
        self.log(t, f"closed {t['filled']} at {avg_out}: {why}; charges Rs{c:.2f}")
        if self.history_path:                                           # real fills beside the signal's price: the true cost
            try:
                with open(os.path.join(os.path.dirname(self.history_path), "trades.jsonl"), "a") as f:
                    f.write(json.dumps({k: t.get(k) for k in ("made", "symbol", "side", "signal", "ref", "sd", "rr", "filled", "avg_in",
                                                              "avg_out", "why", "pnl", "charges", "net", "practice")}) + "\n")
            except OSError:
                pass

    def _pnl(self, t):
        """Rupees after charges: closed at the real prices less Kite's charges, open at the last price less the estimate."""
        if t.get("pnl") is not None:
            return t["pnl"] - t.get("charges", t.get("charges_est", 0))
        if t["status"] in ("open", "exiting", "practice") and t.get("avg_in") and t.get("ltp"):
            return (1 if t["side"] == "LONG" else -1) * (t["ltp"] - t["avg_in"]) * t["filled"] - t.get("charges_est", 0)
        return 0

    def _pnl_today(self):
        return sum(self._pnl(t) for t in self.state["trades"] if not t.get("practice"))


def fresh_signals(doc, now, done):
    """Today's journal entries to act on now: made on a bar that closed at most SIGNAL_MAX_AGE seconds ago, live data
    only, and not tried before (done: the ids already tried today)."""
    today = now.date().isoformat()
    if not doc or doc.get("date") != today or doc.get("mode") != "live":
        return []
    out = []
    for e in doc.get("entries") or []:
        sid = f"{e.get('symbol')} {e.get('direction')}"
        if sid in done or e.get("direction") not in ("LONG", "SHORT"):
            continue
        try:
            closed = datetime.fromisoformat(e["signal_bar"]).astimezone(now.tzinfo) + timedelta(minutes=BAR_MIN)
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= (now - closed).total_seconds() <= SIGNAL_MAX_AGE:
            out.append({**e, "id": sid})
    return out


class Auto:
    """Reads the journal the engine writes on this box and turns each new entry into a trade, once."""

    def __init__(self, engine, journal_path):
        self.engine, self.path, self.mtime = engine, journal_path, None

    def tick(self):
        try:
            m = os.stat(self.path).st_mtime
            if m == self.mtime:
                return
            with open(self.path) as f:
                doc = json.load(f)
            self.mtime = m
        except (OSError, ValueError):
            return
        e = self.engine
        with e.lock:
            e._new_day()
            done = e.state.setdefault("done", [])
            todo = fresh_signals(doc, e.now(), set(done))
            for s in todo:
                done.append(s["id"])                         # before trying: never twice, even after a refusal
            e.save()
        for s in todo:
            try:
                tid = e.add(s)
                print(f"signal {s['signal_time']} {s['symbol']} {s['direction']}: {'practice ' if e.practice else ''}trade {tid}", flush=True)
            except (ValueError, KeyError, KiteError) as err:
                print(f"signal {s.get('signal_time')} {s.get('symbol')} {s.get('direction')} not taken: {err}", flush=True)


class Session:
    """The day's access token from the commodities-trading morning login: its file on this box when readable, else
    its copy on GitHub (the kite-session branch), checked every minute until found."""

    def __init__(self, engine, key, secret, paths, store, fetch=None):
        self.engine, self.key, self.secret, self.store = engine, key, secret, store
        self.paths = list(paths) + [os.path.join(store, "session.json")]
        self.fetch = fetch or (lambda: urllib.request.urlopen(SESSION_URL, timeout=15).read())
        self.checked = 0

    def refresh(self):
        tok = shared_session.load(self.paths, self.secret, self.engine.now())
        if not tok and time.time() - self.checked >= 60:
            self.checked = time.time()
            try:
                os.makedirs(self.store, exist_ok=True)
                with open(os.path.join(self.store, "session.json"), "wb") as f:
                    f.write(self.fetch())
                tok = shared_session.load(self.paths, self.secret, self.engine.now())
            except Exception as e:
                print(f"kite session: {type(e).__name__}", flush=True)
        with self.engine.lock:
            if tok and (not self.engine.kite or self.engine.kite.token != tok):
                self.engine.kite = Kite(self.key, tok)
                print("kite session: today's login found", flush=True)
            elif not tok:
                self.engine.kite = None


class Guard:
    """The passcode check: constant-time compare; 10 failures in an hour lock the API for an hour."""

    def __init__(self, passcode, clock=time.time):
        self.passcode, self.clock, self.fails = passcode.encode(), clock, []

    def ok(self, given):
        now = self.clock()
        self.fails = [t for t in self.fails if now - t < 3600]
        if len(self.fails) >= 10:
            return False
        if given and hmac.compare_digest(given.encode(), self.passcode):
            return True
        self.fails.append(now)
        return False


def handler(engine, guard, origin):
    class H(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):                          # no request lines
            pass

        def _send(self, code, body):
            b = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_OPTIONS(self):
            self._send(204, {})

        def _auth(self):
            h = self.headers.get("Authorization", "")
            if guard.ok(h[7:] if h.startswith("Bearer ") else ""):
                return True
            self._send(401, {"error": "wrong passcode (10 wrong in an hour lock the server for an hour)"})
            return False

        def do_GET(self):
            if not self._auth():
                return
            try:
                p = urllib.parse.urlparse(self.path).path
                if p == "/api/state":
                    return self._send(200, engine.view())
                if p == "/api/money":
                    return self._send(200, engine.money())
            except (ValueError, KiteError) as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                return
            try:
                p = self.path.rstrip("/")
                if p.startswith("/api/trades/") and p.endswith("/exit"):
                    engine.exit(p.split("/")[3])
                    return self._send(200, {"ok": True})
                if p == "/api/stop":
                    engine.stop_all()
                    return self._send(200, {"ok": True})
            except (ValueError, KiteError) as e:
                return self._send(400, {"error": str(e)})
            self._send(404, {"error": "not found"})
    return H


def limits_from_env():
    e = os.environ.get
    return {"capital": float(e("INTRADAY_CAPITAL", "20000")), "risk_pct": float(e("INTRADAY_RISK_PCT", "5")),
            "max_loss_day": float(e("INTRADAY_MAX_LOSS_DAY", "5000")), "max_open": int(e("INTRADAY_MAX_OPEN", "0"))}


def write_status(path, engine):
    """A small public file for the app (no passcode): is the server up, is today's Kite login in, which mode."""
    try:
        with open(path + ".tmp", "w") as f:
            json.dump({"at": engine.now().isoformat(timespec="seconds"), "kite": bool(engine.kite), "mode": engine.mode,
                       "locked": bool(engine.state["locked"])}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def main():
    key, secret, code = (os.environ.get(k, "").strip() for k in ("KITE_API_KEY", "KITE_API_SECRET", "INTRADAY_PASSCODE"))
    if not (key and secret and len(code) >= 24):
        sys.exit("KITE_API_KEY, KITE_API_SECRET and INTRADAY_PASSCODE (24+ characters) must be set (see server/setup.sh)")
    data = os.environ.get("INTRADAY_DATA", "/var/lib/intraday-orders")
    public = os.environ.get("INTRADAY_PUBLIC", "/var/lib/intraday-public")
    os.makedirs(data, exist_ok=True)
    engine = Engine(os.path.join(data, "state.json"), limits_from_env(), history_path=os.path.join(data, "money.json"))
    mode = os.environ.get("INTRADAY_MODE", "real").strip().lower()
    engine.mode = mode if mode in ("real", "practice", "off") else "off"
    sess = Session(engine, key, secret, [p for p in os.environ.get("KITE_SHARED_SESSION", "").split(":") if p], os.path.join(data, "kite"))
    auto = Auto(engine, os.path.join(public, "today_log.json"))
    srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("INTRADAY_PORT", "8788"))),
                              handler(engine, Guard(code), os.environ.get("ALLOW_ORIGIN", "https://ramanauday1561.github.io")))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"intraday order server up; mode {engine.mode.upper()}; limits {engine.limits}", flush=True)
    n = 0
    while True:
        try:
            sess.refresh()
            engine.tick()
            auto.tick()
            if n % 15 == 0:
                write_status(os.path.join(public, "server.json"), engine)
        except Exception as e:                                       # keep going; the exchange holds each stop
            print(f"loop: {type(e).__name__}: {e}", flush=True)
        n += 1
        time.sleep(2)


if __name__ == "__main__":
    main()
