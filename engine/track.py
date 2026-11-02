"""Append one session's replay (python -m engine.backtest --date D) to the running track record.

Usage: python -m engine.track --day /tmp/day.json --out data/track_record.json
The replay applies the engine rules as committed (GITHUB_SHA is recorded), using only bars and
headlines published before each 15-minute checkpoint. It is a reconstruction, not a broker
statement: live runs can differ slightly through data delays and revised bars."""
import argparse
import json
import os

from . import evaluate

KEEP = ("symbol", "direction", "setup", "signal_time", "entry", "exit", "reason", "r_multiple", "net_r", "conviction",
        "news_bucket", "stop_distance", "size_mult")  # stop/size let the app re-size trades for the user's paper account


def add_day(record, day_result, engine_version=None):
    date = day_result["period"]["from"] if day_result.get("period") else None
    if not date:
        return record
    trades = [{k: t.get(k) for k in KEEP} for t in day_result.get("trades", []) if t.get("date", date) == date]
    days = [d for d in record.get("days", []) if d["date"] != date]
    days.append({"date": date, "engine": engine_version, "trades": trades,
                 "net_r": round(sum(t["net_r"] for t in trades), 3)})
    days.sort(key=lambda d: d["date"])
    flat = [dict(t, date=d["date"]) for d in days for t in d["trades"]]
    summary = evaluate.metrics(flat)
    summary.update({"sessions": len(days), "sessions_with_trades": sum(bool(d["trades"]) for d in days),
                    "net_r_ci95": evaluate.bootstrap_ci(flat), "from": days[0]["date"], "to": days[-1]["date"]})
    return {"days": days, "summary": summary,
            "note": "Replayed after each close with the rules in force that day (see 'engine'); "
                    "net R includes 0.06% round-trip costs. Not a broker statement."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--out", default="data/track_record.json")
    a = ap.parse_args()
    record = json.load(open(a.out)) if os.path.exists(a.out) else {}
    record = add_day(record, json.load(open(a.day)), (os.environ.get("GITHUB_SHA") or "")[:7] or None)
    with open(a.out, "w") as f:
        json.dump(record, f, indent=1)
    s = record.get("summary", {})
    print("track record:", {k: s.get(k) for k in ("sessions", "trades", "win_rate_pct", "avg_net_r", "total_net_r", "net_r_ci95")})


if __name__ == "__main__":
    main()
