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
    assert session_phase(mk(9, 45)) == "MORATORIUM"   # e.g. the 3 Nov RELIANCE 09:35 signal: outside the tested window
    assert session_phase(mk(10, 4)) == "MORATORIUM"   # 10:00 bar not yet complete
    assert session_phase(mk(10, 5)) == "ACTIVE"
    assert session_phase(mk(14, 39)) == "ACTIVE"      # latest completed bar 14:30 = last backtest checkpoint
    assert session_phase(mk(14, 40)) == "LATE"
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


def test_priced_in_override_needs_confirmation():
    from engine.scoring import classify_contradiction as cc
    base = dict(pos_news=True, gap=0.2, last_bar_bear=False, prior_rally=6, extension=2.0, opt=None, surge_buy=False)
    rising = cc(ofi_v=0.05, ltp=1321, vw=1312, **base)          # DRREDDY 27 Oct: above VWAP, flow not negative
    assert rising[0] == "PRICED_IN_WATCH" and rising[2] is None
    confirmed = cc(ofi_v=-0.2, ltp=1305, vw=1312, **base)
    assert confirmed[0] == "PRICED_IN_EXHAUSTION" and confirmed[2] == "SHORT"
    assert cc(ofi_v=0.0, ltp=1, vw=1, pos_news=False, gap=0, last_bar_bear=False, prior_rally=0, extension=0, opt=None, surge_buy=False) is None


def test_exit_policy_simulation():
    from engine.backtest import simulate_policy
    bars = _bars([(100, 101.5, 99.8, 101, 1), (101, 101.2, 99.5, 99.6, 1), (99.6, 99.7, 99.6, 99.6, 1)])
    # +1R reached in bar 1 (sd=1); breakeven moves stop to 100 -> stopped at entry in bar 2
    assert simulate_policy(True, bars, 1.0, None, True)[0] == 0.0
    # without breakeven the trade survives (low 99.5 > stop 99) and is marked at the last close
    assert round(simulate_policy(True, bars, 1.0, None, False)[0], 2) == -0.4
    # target 1.5R (101.5) hit in bar 1
    assert simulate_policy(True, bars, 1.0, 1.5, False)[0] == 1.5
    # short mirror: stop at 101 hit in bar 1 (high 101.5)
    assert simulate_policy(False, bars, 1.0, 2.0, False)[0] == -1.0


def test_exit_walk_forward_picks_on_train_only():
    from datetime import date, timedelta
    from engine import evaluate as ev
    days = [date(2025, 1, 6) + timedelta(days=i) for i in range(40)]
    trades = []
    for i, d in enumerate(days):
        for k in range(3):
            ex = {pid: (0.5 if (pid == "stop2.0x_tgtnone_be0" and i < 24) else -0.1) for pid, *_ in ev.EXIT_POLICIES}
            trades.append({"date": d.isoformat(), "exits": ex})
    r = ev.walk_forward_exits(trades, days)
    assert r["selected"]["policy"] == "stop2.0x_tgtnone_be0" and r["policies_tried"] == 18
    assert r["selected"]["test"]["avg_net_r"] == -0.1  # good on train only -> test exposes it


def _day_bars(closes, start="2026-10-28 10:00"):
    idx = pd.date_range(start, periods=len(closes), freq="5min", tz=IST)
    rows = [(c, c + 0.5, c - 0.5, c, 1000) for c in closes]
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def test_journal_status_and_carry_over():
    from engine import journal
    bars = _day_bars([100, 100, 101, 102, 103, 104])
    entry = {"symbol": "X", "direction": "LONG", "signal_bar": "2026-10-28T10:00:00+05:30", "stop_distance": 1.0, "rr": 2.0}
    st = journal.status(entry, bars)
    assert st["fill"] == 100 and st["state"] == "TARGET" and st["r"] == 2.0 and st["at"] == "10:15"
    short = dict(entry, direction="SHORT")
    assert journal.status(short, bars)["state"] == "STOPPED"
    assert journal.status(dict(entry, signal_bar="2026-10-28T10:25:00+05:30"), bars)["state"] == "PENDING"

    def payload(phase, sigs, asof="2026-10-28T10:00:00+05:30"):
        mk = lambda sym, d: {"symbol": sym, "name": sym, "direction": d, "setup": "MOMENTUM", "conviction": 50, "price": {"ltp": 100},
                             "execution": {"stop_distance": 1.0, "rr": 2.0, "option_idea": None}, "contradiction": None}
        return {"meta": {"data_as_of": asof, "mode": "live", "session_phase": phase, "generated_at": asof},
                "signals": [mk(*x) for x in sigs]}
    first = journal.update(None, payload("ACTIVE", [("X", "LONG")]), {"X": bars})
    # 10 minutes later the signal is gone from the main list, but the journal keeps it and adds the new one
    later = journal.update(first, payload("ACTIVE", [("Y", "LONG"), ("X", "NO_TRADE")], "2026-10-28T10:10:00+05:30"), {"X": bars})
    assert [e["symbol"] for e in later["entries"]] == ["X", "Y"] and later["entries"][1]["status"]["state"] == "NO_DATA"
    # a new day starts a fresh journal; a closed session adds nothing
    assert journal.update(later, payload("CLOSED", [("Z", "LONG")], "2026-10-29T15:25:00+05:30"), {})["entries"] == []


def test_track_record_accumulates_and_replaces_days():
    from engine import track
    day = lambda d, rs: {"period": {"from": d}, "trades": [{"date": d, "symbol": f"S{i}", "direction": "LONG", "net_r": r,
                                                            "stop_distance": 2.5, "size_mult": 1.0} for i, r in enumerate(rs)]}
    rec = track.add_day({}, day("2026-10-28", [0.5, -1.0]), "abc1234")
    rec = track.add_day(rec, day("2026-10-29", [1.0]))
    rec = track.add_day(rec, day("2026-10-28", [0.5]))  # re-running a day replaces it
    assert [d["date"] for d in rec["days"]] == ["2026-10-28", "2026-10-29"]
    assert rec["summary"]["trades"] == 2 and rec["summary"]["total_net_r"] == 1.5 and rec["days"][0]["engine"] is None
    assert rec["days"][1]["trades"][0]["stop_distance"] == 2.5  # kept so the app can size paper trades
    assert track.add_day(rec, {"period": None}) is rec


def test_daily_from_intraday_and_fetch_retry(monkeypatch):
    from engine import market
    d = market.daily_from_intraday(pd.concat([_day_bars([1, 2, 3]), _day_bars([5, 4], "2026-10-29 09:15")]))
    assert list(d["open"]) == [1, 5] and list(d["close"]) == [3, 4] and list(d["volume"]) == [3000, 2000]
    calls = {}
    def flaky(sym, rng, itv):
        calls[sym] = calls.get(sym, 0) + 1
        return None if (sym == "B" and calls[sym] < 2) else d
    monkeypatch.setattr(market, "yahoo", flaky)
    monkeypatch.setattr(market.time, "sleep", lambda s: None)
    out = market.fetch_many(["A", "B"], "5d", "5m")
    assert out["A"] is not None and out["B"] is not None and calls["B"] == 2


def test_adopted_exit_policy_in_engine_and_simulation():
    from datetime import datetime as dt
    from engine.backtest import simulate
    from engine.config import EXIT_STOP_MULT, EXIT_TARGET_R
    payload = build(now=dt(2025, 1, 6, 11, 30, tzinfo=IST), force_demo=True)
    live = [x for x in payload["signals"] if x["direction"] != "NO_TRADE"]
    assert live
    for x in live:
        e = x["execution"]
        assert abs(e["stop_distance"] - EXIT_STOP_MULT * e["stop_distance_base"]) < 0.02 and e["rr"] == EXIT_TARGET_R
        assert e["breakeven_after_r"] == 1.0
    sig = {"symbol": "X", "direction": "LONG", "setup": "MOMENTUM", "conviction": 50, "regime": "TRENDING",
           "risk": {"size_multiplier": 1.0}, "execution": {"stop_distance": 2.0, "rr": 2.0, "qty": 10, "breakeven_after_r": 1.0}}
    # +1R (102) reached in bar 1, then price falls back through entry: exits at breakeven, not at the -1R stop
    be = simulate(sig, _bars([(100, 102.5, 99.5, 102, 1), (102, 102, 97, 97.5, 1), (97.5, 98, 97, 97, 1)]))
    assert be["reason"] == "BREAKEVEN" and be["r_multiple"] == 0.0


def test_live_uses_completed_bars_only():
    from datetime import datetime as dt
    from engine.market import drop_incomplete
    bars = _day_bars([100, 101, 102], "2026-11-02 10:40")          # bars start 10:40, 10:45, 10:50
    kept = drop_incomplete(bars, dt(2026, 11, 2, 10, 50, 20, tzinfo=IST))  # refresh 20 s into the 10:50 bar
    assert [t.strftime("%H:%M") for t in kept.index] == ["10:40", "10:45"]
    assert len(drop_incomplete(bars, dt(2026, 11, 2, 10, 55, tzinfo=IST))) == 3   # 10:50 bar now complete
    assert drop_incomplete(bars, dt(2026, 11, 2, 10, 41, tzinfo=IST)) is None


def test_last_full_session_ignores_partial_days(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path))
    from engine import archive
    importlib.reload(archive)
    full = _day_bars([100.0] * 75, "2026-11-02 09:15")
    partial = _day_bars([100.0] * 10, "2026-11-03 09:15")
    archive.save_bars("^NSEI", pd.concat([full, partial]))
    assert archive.last_full_session() == "2026-11-02"


def test_save_journal_keeps_live_days_and_index(tmp_path):
    from engine import archive
    log = lambda d, n, mode="live": {"date": d, "mode": mode, "entries": [{}] * n, "summary": {"net_r_gross": 0.5 * n}}
    assert archive.save_journal(log("2026-11-04", 4), str(tmp_path)) == "2026-11-04"
    assert archive.save_journal(log("2026-11-05", 1), str(tmp_path)) == "2026-11-05"
    assert archive.save_journal(log("2026-11-05", 2), str(tmp_path)) == "2026-11-05"   # later save replaces
    assert archive.save_journal(log("2026-11-06", 3, "demo"), str(tmp_path)) is None     # demo never saved
    assert archive.save_journal(None, str(tmp_path)) is None
    idx = json.load(open(tmp_path / "index.json"))["days"]
    assert [(d["date"], d["signals"]) for d in idx] == [("2026-11-04", 4), ("2026-11-05", 2)]


# ---------------- Zerodha Kite (fake client: no network) ----------------
def test_kite_session_checksum_encryption_and_expiry(tmp_path):
    import hashlib
    from datetime import datetime as dt
    from engine import kite
    assert kite.checksum("key", "req", "sec") == hashlib.sha256(b"keyreqsec").hexdigest()
    path = str(tmp_path / "s.enc")
    kite.save_session({"access_token": "AT", "created": "2026-11-05T09:01:00+05:30"}, "secret1", path)
    assert kite.read_session("secret1", path)["access_token"] == "AT"
    assert kite.read_session("other-secret", path) is None          # cannot be read without the repo secret
    assert b"AT" not in open(path, "rb").read()                       # stored encrypted
    s = lambda created: {"created": created}
    assert kite.session_fresh(s("2026-11-05T07:00:00+05:30"), dt(2026, 11, 5, 10, 0, tzinfo=IST))
    assert not kite.session_fresh(s("2026-11-04T20:00:00+05:30"), dt(2026, 11, 5, 10, 0, tzinfo=IST))  # expired at 06:00
    assert kite.session_fresh(s("2026-11-04T20:00:00+05:30"), dt(2026, 11, 5, 5, 0, tzinfo=IST))       # still valid before 06:00
    assert not kite.session_fresh(None)


def test_kite_parsers_and_implied_vol():
    from engine import kite
    csv_text = ("instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
                "408065,1594,INFY,\"INFOSYS\",0,,0,0.05,1,EQ,NSE,NSE\n"
                "12345,48,INFY26NOV1500CE,\"INFY\",0,2026-11-24,1500,0.05,400,CE,NFO-OPT,NFO\n")
    rows = kite.parse_instruments(csv_text)
    assert rows[0]["token"] == 408065 and rows[0]["expiry"] is None and rows[1]["lot"] == 400 and rows[1]["strike"] == 1500.0
    df = kite.parse_candles([["2026-11-04T09:15:00+0530", 100, 101, 99, 100.5, 1000], ["2026-11-04T09:20:00+0530", 100.5, 102, 100, 101, 1500]])
    assert str(df.index[0]) == "2026-11-04 09:15:00+05:30" and df["volume"].iloc[1] == 1500
    p = kite.bs_price(1500, 1520, 20 / 365, 0.25, True)
    assert abs(kite.implied_vol(p, 1500, 1520, 20 / 365, True) - 0.25) < 1e-4
    assert kite.implied_vol(0.01, 1500, 1000, 20 / 365, True) is None   # below intrinsic value: no IV


class FakeKite:
    """Mimics engine.kite.Kite: NFO instruments for INFY and quotes priced at 25% volatility."""
    def __init__(self, spot=1500.0):
        from datetime import date
        from engine import kite
        self.spot, self.exp = spot, date(2026, 11, 24)
        self.rows = [{"token": 1, "symbol": "INFY26NOVFUT", "name": "INFY", "expiry": self.exp, "strike": 0.0, "lot": 400, "type": "FUT", "segment": "NFO-FUT", "exchange": "NFO"}]
        for k in range(1380, 1640, 20):
            for t in ("CE", "PE"):
                self.rows.append({"token": k, "symbol": f"INFY26NOV{k}{t}", "name": "INFY", "expiry": self.exp, "strike": float(k), "lot": 400, "type": t, "segment": "NFO-OPT", "exchange": "NFO"})
        self.kite = kite

    def instruments(self, exchange):
        return self.rows

    def quote(self, keys):
        from datetime import date
        out, t = {}, (self.exp - date(2026, 11, 5)).days / 365
        for key in keys:
            r = next(x for x in self.rows if "NFO:" + x["symbol"] == key)
            if r["type"] == "FUT":
                out[key] = {"last_price": self.spot, "oi": 1_100_000}
            else:
                out[key] = {"last_price": round(self.kite.bs_price(self.spot, r["strike"], t, 0.25, r["type"] == "CE"), 2), "oi": 5000 + r["strike"]}
        return out


def test_kite_option_chain_rows_iv_and_futures_oi():
    from datetime import date
    from engine import kite
    from engine.market import options_structure
    ch = kite.option_chains(FakeKite(), {"INFY": 1500.0}, prev_oi={"INFY": 1_000_000}, today=date(2026, 11, 5), min_days=3)["INFY"]
    assert ch["expiry"] == date(2026, 11, 24) and ch["lot"] == 400
    assert abs(ch["fut_oi_chg_pct"] - 10.0) < 1e-9                       # 1.1M vs 1.0M at yesterday's close
    assert all(abs(k / 1500 - 1) <= 0.08 for k in {o["k"] for o in ch["options"]})
    atm = [o for o in ch["options"] if o["k"] == 1500.0 and o["type"] == "Call"][0]
    assert abs(atm["iv"] - 25.0) < 0.5                                     # IV recovered from the premium
    st = options_structure(ch["options"], 1500.0, ch["expiry"])
    assert st and st["atm_iv"] and st["call_wall"] and st["put_wall"]      # GEX / walls now computable
    assert kite.option_chains(FakeKite(), {"INFY": 1500.0}, today=date(2026, 11, 22), min_days=3) == {}  # too close to expiry


def test_option_idea_uses_the_real_contract():
    from datetime import date
    from engine import options_idea as oi
    c = {"exp": date(2026, 11, 24), "k": 1500.0, "ltp": 31.5, "lot": 400, "symbol": "INFY26NOV1500CE"}
    i = oi.idea("LONG", 1498.0, date(2026, 11, 5), 0, 10.0, 2.0, contract=c)
    assert i["strike"] == 1500 and i["expiry"] == "2026-11-24" and i["premium_est"] == 31.5
    assert i["live"] and i["lot_size"] == 400 and i["lot_cost"] == 12600 and i["tradingsymbol"] == "INFY26NOV1500CE"
    assert oi.idea("LONG", 1498.0, date(2026, 11, 5), 30, 10.0, 2.0)["live"] is False


def test_live_data_prefers_kite_and_falls_back_to_yahoo(monkeypatch):
    from datetime import datetime as dt
    from engine import pipeline
    from engine.config import INDEX_SYMBOLS, UNIVERSE
    bars = _day_bars([100.0] * 20, "2026-11-05 09:15")
    daily = pipeline.daily_from_intraday(bars)
    every = [u[0] + ".NS" for u in UNIVERSE] + list(INDEX_SYMBOLS.values()) + sorted({u[3] for u in UNIVERSE} - set(INDEX_SYMBOLS.values()))
    from_kite = [s for s in every if s != "INFY.NS"]
    monkeypatch.setattr(pipeline.kitemod, "fetch_bars", lambda kc, syms, now: ({s: bars for s in from_kite}, {s: daily for s in from_kite}))
    asked = {}
    def yahoo(syms, rng, itv):
        asked.setdefault(itv, []).extend(syms)
        return {s: bars if itv == "5m" else daily for s in syms}
    monkeypatch.setattr(pipeline, "fetch_many", yahoo)
    src = {}
    i5, d1 = pipeline._load_live(dt(2026, 11, 5, 10, 50, 20, tzinfo=IST), kc=object(), source=src)
    assert asked == {"5m": ["INFY.NS"], "1d": ["INFY.NS"]}                # Yahoo only for what Kite lacked
    assert src == {"kite": len(from_kite), "yahoo": 1}
    assert i5["INFY.NS"].index[-1].strftime("%H:%M") == "10:45"          # forming 10:50 bar dropped for Kite and Yahoo alike


def test_futures_oi_snapshots(tmp_path):
    from engine import archive
    p = str(tmp_path / "oi.json")
    assert archive.load_oi("2026-11-05", p) == {}
    archive.save_oi("2026-11-03", {"INFY": 1.0}, p)
    archive.save_oi("2026-11-04", {"INFY": 2.0}, p)
    archive.save_oi("2026-11-05", {"INFY": 3.0}, p)
    assert archive.load_oi("2026-11-05", p) == {"INFY": 2.0}   # strictly before today: yesterday's close
    archive.save_oi("2026-11-06", {"INFY": 4.0}, p, keep=2)
    assert sorted(json.load(open(p))) == ["2026-11-05", "2026-11-06"]


def test_day_rules_direction_lock_and_loss_limit():
    from engine import dayrules
    assert dayrules.gate("SHORT", "10:00", []) is None                                   # first trade: anything goes
    taken = [{"direction": "LONG", "exit_time": None, "r": None}]
    assert dayrules.gate("LONG", "10:15", taken) is None                                 # no cap on the number of trades
    assert dayrules.gate("SHORT", "10:15", taken)["code"] == "DAY_DIRECTION"
    taken = [{"direction": "LONG", "exit_time": "10:20", "r": -1.0}, {"direction": "LONG", "exit_time": "10:40", "r": -1.0}]
    assert dayrules.gate("LONG", "10:30", taken) is None                                 # second loss not closed yet at 10:30
    assert dayrules.gate("LONG", "10:45", taken)["code"] == "DAY_LOSS_LIMIT"             # -2R booked by 10:45
    # live: strongest signal first sets the lock; later opposite one is blocked, same-direction ones pass
    mk = lambda sym, d: {"symbol": sym, "direction": d, "risk": {"blockers": []}}
    sigs = [mk("A", "SHORT"), mk("B", "LONG"), mk("C", "SHORT"), mk("D", "NO_TRADE")]
    dayrules.apply_live(sigs, [], "10:00")
    assert [s["direction"] for s in sigs] == ["SHORT", "NO_TRADE", "SHORT", "NO_TRADE"]
    assert sigs[1]["risk"]["blockers"][0]["code"] == "DAY_DIRECTION"


def test_backtest_selection_applies_day_rules_without_a_cap():
    from engine import evaluate as ev
    t = lambda sym, d, tm, ex, r, comp=0.5: {"date": "2026-11-09", "symbol": sym, "direction": d, "signal_time": tm, "exit_time": ex,
                                             "r_multiple": r, "net_r": r, "conviction": 50, "cost_r": 0.05, "composite": comp}
    c = [t("A", "SHORT", "10:00", "15:15", 0.2), t("B", "LONG", "10:00", "15:15", 0.1, comp=0.3), t("C", "SHORT", "10:15", "10:30", -1.0),
         t("D", "SHORT", "10:30", "10:45", -1.0), t("E", "SHORT", "11:00", "15:15", 0.5), t("F", "SHORT", "11:15", "15:15", 0.5)]
    assert [x["symbol"] for x in ev.apply_filters(c)] == ["A", "B", "C", "D", "E", "F"]     # rules off: unchanged behaviour
    # B blocked (against A), C and D taken, then -2R booked by 10:45 -> E and F blocked
    assert [x["symbol"] for x in ev.apply_filters(c, day_rules=True)] == ["A", "C", "D"]


def test_live_signals_use_the_15_minute_checkpoint():
    import pandas as pd
    from engine.pipeline import to_checkpoint
    idx = pd.date_range("2026-11-09 09:15", "2026-11-09 10:25", freq="5min", tz="Asia/Kolkata")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)
    cut, ts = to_checkpoint({"^NSEI": df, "X.NS": df, "Y.NS": None})
    assert ts.strftime("%H:%M") == "10:15" and cut["X.NS"].index[-1] == ts and cut["Y.NS"] is None
