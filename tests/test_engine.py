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


def test_archive_roundtrip_and_merge(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path))
    from engine import archive
    importlib.reload(archive)
    idx = pd.date_range("2025-01-06 09:15", periods=4, freq="5min", tz=IST)
    a = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": [1, 1.1, 1.2, 1.3], "volume": 10}, index=idx)
    archive.save_bars("X.NS", a.iloc[:3])
    merged = archive.merge_bars(archive.load_bars("X.NS"), a.iloc[2:])
    assert len(merged) == 4 and merged.index.is_monotonic_increasing
    archive.save_bars("X.NS", merged)
    assert archive.load_bars("X.NS")["close"].iloc[-1] == 1.3
    from datetime import timezone
    ts = datetime(2025, 1, 6, tzinfo=timezone.utc)
    assert archive.save_news("X", [{"title": "a", "source": "s", "url": "", "ts": ts}]) == 1
    assert archive.save_news("X", [{"title": "a", "source": "s", "url": "", "ts": ts}, {"title": "b", "source": "s", "url": "", "ts": ts}]) == 2
    assert archive.load_news("X")[0]["ts"] == ts


def _cand(date, time, sym, conv, net_r, cost_r=0.1):
    return {"date": date, "signal_time": time, "symbol": sym, "conviction": conv, "net_r": net_r, "cost_r": cost_r}


def test_filters_select_first_signal_per_stock_and_cap_per_day():
    from engine import evaluate as ev
    c = [_cand("2025-01-06", "10:00", "A", 40, -1), _cand("2025-01-06", "10:15", "A", 90, 2),
         _cand("2025-01-06", "10:15", "B", 70, 1, cost_r=0.5), _cand("2025-01-06", "10:30", "C", 70, 1)]
    assert [t["symbol"] for t in ev.apply_filters(c)] == ["A", "B", "C"]            # baseline: first per stock
    assert [t["symbol"] for t in ev.apply_filters(c, conv_min=65)] == ["A", "B", "C"]  # A re-qualifies at 10:15
    assert [t["symbol"] for t in ev.apply_filters(c, max_cost_r=0.25)] == ["A", "C"]
    assert len(ev.apply_filters(c, max_per_day=2)) == 2


def test_walk_forward_is_chronological_and_flags_no_edge():
    import numpy as np
    from datetime import date, timedelta
    from engine import evaluate as ev
    rng = np.random.default_rng(1)
    days = [date(2025, 1, 6) + timedelta(days=i) for i in range(40)]
    cands = [_cand(d.isoformat(), "10:00", f"S{k}", 50 + 10 * (k % 4), float(rng.normal(-0.05, 1.0))) for d in days for k in range(5)]
    wf = ev.walk_forward(cands, days)
    assert wf["train_sessions"][1] < wf["test_sessions"][0] and wf["configs_tried"] == 24
    assert wf["selected"]["verdict"] in ("NO_EDGE_DEMONSTRATED", "POSITIVE_BUT_NOT_SIGNIFICANT")  # pure noise must not look like an edge
    lo, hi = wf["selected"]["test_ci95"]
    assert lo < hi


def test_hypothesis_verdict_rules():
    from engine import evaluate as ev
    mk = lambda n, r: [{"date": f"2026-11-{1 + i % 28:02d}", "net_r": r, "news_bucket": "news_opposed"} for i in range(n)]
    assert ev.hypothesis_report(mk(50, 1.0), False)["verdict"] == "INSUFFICIENT_DATA"
    assert ev.hypothesis_report(mk(120, 0.0), False)["verdict"] == "NOT_SUPPORTED"
    good = [dict(t, net_r=0.5 + (i % 3) * 0.1) for i, t in enumerate(mk(120, 0))]
    assert ev.hypothesis_report(good, False)["verdict"] == "SUPPORTED"
    assert ev.hypothesis_report(good, True)["verdict"].startswith("IN_SAMPLE")


def test_option_idea_strike_and_expiry():
    from datetime import date
    from engine import options_idea as oi
    assert oi.atm_strike(1238.0) == 1240 and oi.atm_strike(188.6) == 187.5 and oi.atm_strike(11169) == 11200
    assert oi.last_tuesday(2026, 10) == date(2026, 10, 27)
    assert oi.monthly_expiry(date(2026, 10, 12)) == date(2026, 10, 27)
    assert oi.monthly_expiry(date(2026, 10, 26)) == date(2026, 11, 24)  # 1 day left -> roll to next month
    i = oi.idea("SHORT", 1748.1, date(2026, 10, 12), 40.0, 10.0, 2.0)
    assert i["action"] == "BUY PE" and i["strike"] == 1750 and i["premium_stop_est"] == 35.0 and i["premium_target_est"] == 50.0
    assert oi.idea("NO_TRADE", 100, date(2026, 10, 12), 1, 1, 2) is None


def test_webpush_rfc8291_vector_and_roundtrip():
    from cryptography.hazmat.primitives.asymmetric import ec
    from engine import webpush as wp
    # RFC 8291 Appendix A test vector
    as_key = ec.derive_private_key(int.from_bytes(wp.unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    body = wp.encrypt(b"When I grow up, I want to be a watermelon",
                      "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                      "BTBZMqHH6r4Tts7J_aSIgg", salt=wp.unb64u("DGv6ra1nlYgDCS1FRnbzlw"), as_key=as_key)
    assert wp.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                             "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_vapid_jwt_verifies():
    import json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from engine import webpush as wp
    priv, pub = wp.generate_vapid_keys()
    hdr = wp.vapid_header("https://web.push.apple.com/QGuQ", wp.load_vapid_private(priv), "https://example.com")
    t, k = hdr[len("vapid t="):].split(", k=")
    assert k == pub
    h, c, s = t.split(".")
    assert json.loads(wp.unb64u(c))["aud"] == "https://web.push.apple.com"
    raw = wp.unb64u(s)
    sig = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), wp.unb64u(pub)).verify(sig, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))


def test_notify_only_new_live_active_signals():
    from engine import notify
    mk = lambda sym, d: {"symbol": sym, "direction": d, "conviction": 60,
                         "execution": {"entry_trigger": 1, "stop": 0.9, "target": 1.2, "option_idea": None}}
    cur = {"meta": {"mode": "live", "session_phase": "ACTIVE"}, "signals": [mk("A", "LONG"), mk("B", "SHORT"), mk("C", "NO_TRADE")]}
    prev = {"signals": [mk("A", "LONG")]}
    assert [s["symbol"] for s in notify.new_signals(cur, prev)] == ["B"]
    assert [s["symbol"] for s in notify.new_signals(cur, None)] == ["A", "B"]
    assert notify.new_signals(dict(cur, meta={"mode": "live", "session_phase": "CLOSED"}), prev) == []
    assert notify.message_for(mk("B", "SHORT"))["tag"] == "B-SHORT"
