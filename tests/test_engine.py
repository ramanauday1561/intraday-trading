import json
import math
from datetime import datetime

import pandas as pd
import pytest

from engine import indicators as ind
from engine import lpp, news
from engine.config import IST, REGIME_WEIGHTS
from engine.market import oi_quadrant
from engine.pipeline import build, to_json
from engine.scoring import daily_sigma_pct, detect_regime, session_phase


def test_weights_sum_to_one():
    for w in REGIME_WEIGHTS.values():
        assert math.isclose(sum(w.values()), 1.0)


def test_vix_daily_sigma():
    assert math.isclose(daily_sigma_pct(16), 16 / math.sqrt(252))


def test_regimes():
    assert detect_regime(25, 30, None, 0.9, False) == "MACRO_SHOCK"
    assert detect_regime(16, 30, {"gex_norm": -0.3}, 0.5, False) == "TRENDING"
    assert detect_regime(11, 15, {"gex_norm": 0.6}, 0.5, False) == "RANGE_BOUND"
    assert detect_regime(16, 30, {"gex_norm": 0.6}, 0.5, False) == "TRANSITIONAL"
    assert detect_regime(11, 15, None, 0.5, True) == "EVENT_DRIVEN"


def test_session_phase():
    mk = lambda h, m: datetime(2025, 1, 6, h, m, tzinfo=IST)  # Monday
    assert session_phase(mk(9, 20)) == "MORATORIUM"
    assert session_phase(mk(10, 0)) == "ACTIVE"
    assert session_phase(mk(15, 10)) == "LATE"
    assert session_phase(mk(16, 0)) == "CLOSED"
    assert session_phase(datetime(2025, 1, 4, 11, 0, tzinfo=IST)) == "CLOSED"


def test_oi_quadrants():
    assert oi_quadrant(1, 1) == "LONG_BUILDUP"
    assert oi_quadrant(-1, 1) == "SHORT_BUILDUP"
    assert oi_quadrant(1, -1) == "SHORT_COVERING"
    assert oi_quadrant(-1, -1) == "LONG_UNWINDING"
    assert oi_quadrant(1, None) == "UNAVAILABLE"


def test_lpp():
    ref = lpp.reference_price([(0, 100), (10, 110), (25, 120), (50, 500)], 55)
    assert ref == 310  # trades at t=25 and t=50 fall inside the trailing 30s
    assert lpp.lpp_range(100, 40) is None  # premium <= 50 not modelled
    assert lpp.lpp_range(100, 80) == (60, 140)
    assert lpp.validate_order(139, 100, 80) and not lpp.validate_order(141, 100, 80)
    assert lpp.validate_order(lpp.clamp(500, 100, 80), 100, 80)
    assert lpp.headroom_pct(100, 100, 80) == 100.0


def test_news_taxonomy_and_materiality():
    assert news.classify("USFDA issues warning letter to plant")[:2] == (1, "USFDA")
    assert news.classify("Company signs MoU with state govt")[1] == "MOU"
    assert news.order_value_cr("Firm bags order worth Rs 1,200 crore") == 1200
    from datetime import timezone
    ts = datetime.now(timezone.utc)
    small = news.analyse_symbol("X", "X Ltd", 100000, [{"title": "X bags order worth Rs 100 crore", "source": "a", "url": "", "ts": ts}])
    big = news.analyse_symbol("X", "X Ltd", 100000, [{"title": "X bags order worth Rs 9,000 crore", "source": "a", "url": "", "ts": ts}])
    assert small["score"] == 0.0 and big["score"] > 0


def test_volume_profile_and_vwap():
    idx = pd.date_range("2025-01-06 09:15", periods=10, freq="5min", tz=IST)
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": [100, 100, 100, 100, 100, 100, 100, 100, 105, 110],
                       "volume": [100, 100, 100, 100, 100, 100, 100, 100, 5, 5]}, index=idx)
    df["high"] = df[["close", "high"]].max(axis=1); df["low"] = df[["close", "low"]].min(axis=1)
    poc, val, vah = ind.volume_profile(df)
    assert val <= poc <= vah and abs(poc - 100) < 2
    assert 99 < ind.vwap(df) < 102


def test_demo_payload_schema_and_json_safe():
    payload = build(now=datetime(2025, 1, 6, 11, 30, tzinfo=IST), force_demo=True)
    js = json.loads(to_json(payload))  # allow_nan=False -> raises on NaN
    assert js["meta"]["mode"] == "demo" and js["signals"]
    s = js["signals"][0]
    for k in ("symbol", "direction", "conviction", "pillars", "microstructure", "execution", "risk", "thesis"):
        assert k in s
    assert any(x["contradiction"] and x["contradiction"]["type"] == "DISTRIBUTION_TRAP" for x in js["signals"])
    for x in js["signals"]:
        if x["direction"] != "NO_TRADE":
            e = x["execution"]
            assert math.isclose(abs(e["target"] - e["entry_trigger"]) / e["stop_distance"], e["rr"], rel_tol=0.02)
            assert e["qty"] >= 0


def _bars(rows):
    idx = pd.date_range("2025-01-06 10:35", periods=len(rows), freq="5min", tz=IST)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def test_backtest_simulation_rules():
    from engine.backtest import simulate
    sig = {"symbol": "X", "direction": "LONG", "setup": "MOMENTUM", "conviction": 50, "regime": "TRENDING",
           "risk": {"size_multiplier": 1.0}, "execution": {"stop_distance": 2.0, "rr": 2.0, "qty": 10}}
    tgt = simulate(sig, _bars([(100, 101, 99.5, 100.5, 1), (100.5, 105, 100, 104, 1), (104, 104, 103, 103, 1)]))
    assert tgt["reason"] == "TARGET" and tgt["r_multiple"] == 2.0
    both = simulate(sig, _bars([(100, 106, 97, 100, 1), (100, 100, 100, 100, 1)]))
    assert both["reason"] == "STOP" and both["r_multiple"] == -1.0  # stop wins ties
    short = dict(sig, direction="SHORT")
    assert simulate(short, _bars([(100, 103, 99, 101, 1), (101, 101, 101, 101, 1)]))["reason"] == "STOP"


def test_leverage_cap_limits_qty():
    payload = build(now=datetime(2025, 1, 6, 11, 30, tzinfo=IST), force_demo=True, capital=100000, risk_pct=50)
    for x in payload["signals"]:
        assert x["execution"]["qty"] * x["price"]["ltp"] <= 100000 * 5 + x["price"]["ltp"]


def test_news_is_point_in_time():
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    future = {"title": "X bags order worth Rs 9,000 crore", "source": "a", "url": "", "ts": now + timedelta(hours=1)}
    past = dict(future, title="X wins USFDA approval, strong growth", ts=now - timedelta(hours=1))
    only_future = news.analyse_symbol("X", "X Ltd", 100000, [future], now=now)
    assert only_future["items"] == [] and only_future["score"] == 0.0
    assert news.analyse_symbol("X", "X Ltd", 100000, [future, past], now=now)["items"][0]["title"].startswith("X wins USFDA")
