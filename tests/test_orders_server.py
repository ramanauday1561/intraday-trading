"""The order server against a fake Kite: sizing, the stop at the exchange, target, breakeven, exit time, limits."""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import orders_server as S  # noqa: E402

DAY = datetime(2026, 11, 17, 10, 5, 30, tzinfo=S.IST)


class FakeKite:
    token = "t"

    def __init__(self):
        self.o, self.n, self.px, self.fail, self.margin_x = {}, 0, {"INFY": (1000.0, 999.9, 1000.1)}, set(), 5.0

    def instruments(self):
        return [{"tradingsymbol": "INFY", "instrument_type": "EQ", "segment": "NSE", "tick_size": "0.1"},
                {"tradingsymbol": "SBIN", "instrument_type": "EQ", "segment": "NSE", "tick_size": "0.05"}]

    def quote(self, syms):
        return {s: {"last_price": self.px[s][0], "depth": {"buy": [{"price": self.px[s][1]}], "sell": [{"price": self.px[s][2]}]}}
                for s in syms if s in self.px}

    def place(self, symbol, side, qty, order_type, price, trigger=None):
        if order_type in self.fail:
            raise S.KiteError("refused")
        self.n += 1
        oid = str(self.n)
        self.o[oid] = {"order_id": oid, "tradingsymbol": symbol, "transaction_type": side, "quantity": qty, "order_type": order_type,
                       "price": price, "trigger_price": trigger, "status": "TRIGGER PENDING" if order_type == "SL" else "OPEN",
                       "filled_quantity": 0, "average_price": 0, "tag": S.TAG, "exchange": "NSE", "product": "MIS"}
        return oid

    def modify(self, oid, price, order_type="LIMIT", trigger=None):
        self.o[oid].update(price=price, **({"trigger_price": trigger} if trigger is not None else {}))

    def cancel(self, oid):
        if self.o[oid]["status"] in ("COMPLETE", "CANCELLED"):
            raise S.KiteError("cannot cancel")
        self.o[oid]["status"] = "CANCELLED"

    def orders(self):
        return self.o

    def fill(self, oid, price, qty=None):
        o = self.o[oid]
        o.update(status="COMPLETE", filled_quantity=qty or o["quantity"], average_price=price)

    def charges(self, legs):
        return 10.0

    def margin(self, symbol, side, qty, price):
        return qty * price / self.margin_x

    def funds(self):
        return {"net": 39000.0, "available": {"opening_balance": 40000.0}}

    def positions(self):
        return [{"pnl": -100.0}]


SIG = {"symbol": "INFY", "direction": "LONG", "ref_price": 1000.0, "stop_distance": 20.0, "rr": 2.0, "breakeven_after_r": 1.0,
       "size_mult": 1.0, "signal_time": "10:00", "signal_bar": "2026-11-17T10:00:00+05:30"}


@pytest.fixture
def eng(tmp_path):
    clock = {"now": DAY, "t": 1000.0}
    e = S.Engine(str(tmp_path / "state.json"), {"capital": 20000.0, "risk_pct": 5.0, "max_loss_day": 5000.0, "max_open": 0},
                 now=lambda: clock["now"], clock=lambda: clock["t"], history_path=str(tmp_path / "money.json"))
    e.kite, e.c = FakeKite(), clock
    return e


def trade(e, tid):
    return next(t for t in e.state["trades"] if t["id"] == tid)


def enter(e, sig=SIG, price=1000.1):
    tid = e.add(sig)
    t = trade(e, tid)
    e.kite.fill(t["entry_id"], price)
    e.tick()
    return t


def test_tick_round():
    assert S.tick_round(100.03, 0.05, 1) == 100.05 and S.tick_round(100.03, 0.05, -1) == 100.0 and S.tick_round(100.03, 0.05) == 100.05


def test_long_sized_by_risk_and_stop_goes_to_the_exchange(eng):
    t = enter(eng)
    assert t["qty"] == 50                                   # Rs1,000 risk / Rs20 stop
    assert eng.kite.o[t["entry_id"]]["transaction_type"] == "BUY" and eng.kite.o[t["entry_id"]]["order_type"] == "LIMIT"
    assert eng.kite.o[t["entry_id"]]["price"] == 1001.2     # 0.1% through the ask, up to the 0.1 tick
    sl = eng.kite.o[t["sl_id"]]
    assert t["status"] == "open" and sl["order_type"] == "SL" and sl["transaction_type"] == "SELL" and sl["quantity"] == 50
    assert sl["trigger_price"] == 980.1 and sl["price"] < 980.1 and t["target"] == 1040.1


def test_short_mirrors_the_long(eng):
    t = enter(eng, {**SIG, "direction": "SHORT"}, 999.9)
    sl = eng.kite.o[t["sl_id"]]
    assert eng.kite.o[t["entry_id"]]["transaction_type"] == "SELL" and sl["transaction_type"] == "BUY"
    assert sl["trigger_price"] == 1019.9 and sl["price"] > 1019.9 and t["target"] == 959.9
    eng.kite.px["INFY"] = (959.0, 958.9, 959.1)
    eng.tick()
    assert t["status"] == "exiting" and eng.kite.o[t["exit_id"]]["transaction_type"] == "BUY" and eng.kite.o["2"]["status"] == "CANCELLED"
    eng.kite.fill(t["exit_id"], 959.1)
    eng.tick()
    assert t["status"] == "closed" and t["pnl"] == round((999.9 - 959.1) * 50, 2) and t["net"] == t["pnl"] - 10


def test_target_then_closed_with_charges(eng):
    t = enter(eng)
    eng.kite.px["INFY"] = (1041.0, 1040.9, 1041.1)
    eng.tick()
    assert t["status"] == "exiting" and t["why"] == "target reached" and eng.kite.o["2"]["status"] == "CANCELLED"
    assert eng.kite.o[t["exit_id"]]["price"] < 1040.9
    eng.kite.fill(t["exit_id"], 1040.9)
    eng.tick()
    assert t["status"] == "closed" and t["net"] == round((1040.9 - 1000.1) * 50 - 10, 2)
    assert json.loads(open(os.path.join(os.path.dirname(eng.path), "trades.jsonl")).read())["avg_out"] == 1040.9


def test_breakeven_moves_the_stop_order(eng):
    t = enter(eng)
    eng.kite.px["INFY"] = (1020.5, 1020.4, 1020.6)
    eng.tick()
    assert t["moved"] and eng.kite.o[t["sl_id"]]["trigger_price"] == 1000.1 and t["status"] == "open"
    eng.kite.fill(t["sl_id"], 1000.0)
    eng.tick()
    assert t["status"] == "closed" and t["why"] == "breakeven exit"


def test_stopped_out_and_day_loss_limit_locks(eng):
    eng.limits["max_loss_day"] = 900.0
    with pytest.raises(ValueError, match="loss limit"):
        eng.add(SIG)
    eng.limits["max_loss_day"] = 1100.0
    t = enter(eng)
    eng.kite.px["INFY"] = (975.0, 974.9, 975.1)             # open loss 50 x 25.1 + charges > 1100
    eng.tick()
    assert eng.state["locked"] and t["status"] == "exiting"
    with pytest.raises(ValueError, match="locked"):
        eng.add({**SIG, "symbol": "SBIN"})


def test_stop_triggered_but_not_filled_is_repriced(eng):
    t = enter(eng)
    eng.kite.o[t["sl_id"]]["status"] = "OPEN"
    eng.kite.px["INFY"] = (970.0, 969.9, 970.1)
    eng.tick()
    eng.c["t"] += 11
    eng.tick()
    assert eng.kite.o[t["sl_id"]]["price"] <= 969.0
    eng.kite.fill(t["sl_id"], 969.0)
    eng.tick()
    assert t["status"] == "closed" and t["why"] == "stopped out"


def test_refused_stop_order_closes_the_trade(eng):
    eng.kite.fail.add("SL")
    t = enter(eng)
    assert t["status"] == "exiting" and eng.kite.o[t["exit_id"]]["transaction_type"] == "SELL"


def test_exit_time_closes_everything_and_no_late_entries(eng):
    t = enter(eng)
    eng.c["now"] = DAY.replace(hour=15, minute=5)
    eng.tick()
    assert t["status"] == "exiting" and t["why"] == "exit time"
    with pytest.raises(ValueError, match="14:45"):
        eng.add({**SIG, "symbol": "SBIN"})


def test_entry_repriced_then_cancelled_and_part_fill_kept(eng):
    tid = eng.add(SIG)
    t = trade(eng, tid)
    for _ in range(3):
        eng.c["t"] += 11
        eng.tick()
    assert t["tries"] == 3 and t["status"] == "entering"
    eng.c["t"] += 11
    eng.tick()
    assert eng.kite.o[t["entry_id"]]["status"] == "CANCELLED"
    eng.kite.o[t["entry_id"]].update(filled_quantity=20, average_price=1000.2)
    eng.tick()
    assert t["status"] == "open" and t["filled"] == 20 and eng.kite.o[t["sl_id"]]["quantity"] == 20
    eng.kite.px["SBIN"] = (500.0, 499.95, 500.05)
    t2 = trade(eng, eng.add({**SIG, "symbol": "SBIN", "ref_price": 500.0, "stop_distance": 10.0}))
    for _ in range(4):
        eng.c["t"] += 11
        eng.tick()
    eng.tick()
    assert t2["status"] == "not entered"


def test_price_moved_away_and_one_trade_a_stock(eng):
    with pytest.raises(ValueError, match="moved"):
        eng.add({**SIG, "ref_price": 985.0})
    enter(eng)
    with pytest.raises(ValueError, match="already"):
        eng.add(SIG)


def test_margin_cuts_the_size_or_skips(eng):
    eng.kite.margin_x = 2.0                                  # 50 x 1000 / 2 = 25,000 needed, 20,000 there
    t = trade(eng, eng.add(SIG))
    assert t["qty"] == 39 and t["margin"] <= 20000.0          # the ask is 1000.1
    eng.kite.px["SBIN"] = (500.0, 499.95, 500.05)
    with pytest.raises(ValueError, match="capital left"):
        eng.add({**SIG, "symbol": "SBIN", "ref_price": 500.0})


def test_practice_sends_no_orders(eng):
    eng.mode = "practice"
    t = trade(eng, eng.add(SIG))
    assert t["status"] == "practice" and not eng.kite.o and t["avg_in"] == 1000.1
    eng.kite.px["INFY"] = (1041.0, 1040.9, 1041.1)
    eng.tick()
    assert t["status"] == "closed" and "practice" in t["why"] and eng._pnl_today() == 0 and eng.view()["practice_pnl"] > 0
    eng.mode = "off"
    with pytest.raises(ValueError, match="off"):
        eng.add({**SIG, "symbol": "SBIN"})


def test_stop_everything(eng):
    t = enter(eng)
    eng.stop_all()
    assert t["status"] == "exiting" and eng.state["locked"]


def test_fresh_signals_once_and_only_recent(eng, tmp_path):
    doc = {"date": "2026-11-17", "mode": "live", "entries": [SIG, {**SIG, "symbol": "SBIN", "signal_bar": "2026-11-17T09:45:00+05:30"}]}
    assert [s["symbol"] for s in S.fresh_signals(doc, DAY, set())] == ["INFY"]
    assert S.fresh_signals(doc, DAY + timedelta(minutes=6), set()) == []
    assert S.fresh_signals({**doc, "mode": "demo"}, DAY, set()) == [] and S.fresh_signals({**doc, "date": "2026-11-16"}, DAY, set()) == []
    p = tmp_path / "today_log.json"
    p.write_text(json.dumps(doc))
    a = S.Auto(eng, str(p))
    a.tick()
    assert len(eng.state["trades"]) == 1 and eng.state["done"] == ["INFY LONG"]
    a.mtime = None
    a.tick()
    assert len(eng.state["trades"]) == 1


def test_money_is_this_apps_own_and_guard(eng):
    json.dump({"2026-11-16": {"start": 18668.1, "net": -1604.18}, "2026-11-13": {"v": 2, "net": 300.0, "pnl": 320.0, "charges": 20.0, "trades": 1}},
              open(eng.history_path, "w"))                  # a whole-account row from the first version is ignored
    m = eng.money()
    assert m["today"] == {"v": 2, "pnl": 0.0, "charges": 0.0, "net": 0.0, "trades": 0, "start": 20300.0, "now": 20300.0}
    assert m["capital"] == 20300.0 and m["free"] == 20300.0 and m["kite_free"] == 39000.0 and [d["d"] for d in m["history"]] == ["2026-11-13", "2026-11-17"]
    t = enter(eng)
    eng.kite.px["INFY"] = (1041.0, 1040.9, 1041.1)
    eng.tick()
    eng.kite.fill(t["exit_id"], 1040.9)
    eng.tick()
    m = eng.money()
    assert m["today"]["net"] == t["net"] and m["today"]["charges"] == 10.0 and m["today"]["now"] == round(20300 + t["net"], 2)
    eng.kite = None
    assert eng.money()["kite_free"] is None                 # no login: the app's own numbers still show
    g = S.Guard("x" * 24)
    assert g.ok("x" * 24) and not g.ok("no")


def test_shared_session(tmp_path):
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    from engine import shared_session
    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"kite-session:sec").digest()))
    p = tmp_path / "session.json"
    p.write_text(json.dumps({"at": "2026-11-17T08:30", "until": "2026-11-18T06:00+05:30", "token": f.encrypt(b"tok").decode()}))
    assert shared_session.load([str(tmp_path / "none"), str(p)], "sec", DAY) == "tok"
    assert shared_session.load([str(p)], "sec", DAY + timedelta(days=1)) is None and shared_session.load([str(p)], "other", DAY) is None


def test_capital_carries_each_days_result_forward(eng):
    """Day 1 starts at INTRADAY_CAPITAL; each next day starts at the previous start +/- that day's net (after charges)."""
    assert eng.capital() == 20000.0
    t = enter(eng)                                          # 50 shares: Rs1,000 risk on Rs20,000
    assert t["qty"] == 50
    eng.kite.px["INFY"] = (1041.0, 1040.9, 1041.1)
    eng.tick()
    eng.kite.fill(t["exit_id"], 1040.9)
    eng.tick()
    assert eng.capital() == 20000.0                         # today's result counts from tomorrow
    eng.c["now"] = DAY + timedelta(days=1)                  # next morning, Money tab never opened: still recorded
    eng.kite = FakeKite()
    eng._new_day()
    gain = t["net"]
    assert gain > 0 and eng.capital() == round(20000 + gain, 2)
    t2 = enter(eng, {**SIG, "stop_distance": 10.0})         # 5% of the new capital / Rs10 stop
    assert t2["qty"] == int((20000 + gain) * 0.05 / 10.0)
    assert eng.money()["today"]["start"] == eng.capital()


def test_capital_from_ignores_earlier_days_and_stops_at_zero(eng):
    json.dump({"2026-11-13": {"v": 2, "net": 500.0, "pnl": 520.0, "charges": 20.0, "trades": 1},
               "2026-11-16": {"v": 2, "net": -700.0, "pnl": -680.0, "charges": 20.0, "trades": 1}}, open(eng.history_path, "w"))
    assert eng.capital() == 19800.0
    eng.limits["capital_from"] = "2026-11-16"                # "back to 20k" from the 16th: only later days count
    assert eng.capital() == 19300.0
    eng.limits["capital_from"] = "2026-11-17"
    assert eng.capital() == 20000.0
    json.dump({"2026-11-16": {"v": 2, "net": -25000.0, "pnl": -25000.0, "charges": 0.0, "trades": 1}}, open(eng.history_path, "w"))
    eng.limits["capital_from"] = ""
    with pytest.raises(ValueError, match="capital is used up"):
        eng.add(SIG)
