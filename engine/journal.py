"""Today's signal journal: every signal that became actionable today stays listed with its live outcome.

Signals are snapshots, so one can appear at 10:10 and be gone by 10:20. The journal keeps the first
appearance of each (symbol, direction) and tracks it with the backtest's own rules: entry at the next
bar's open, stop/target from the signal, stop wins ties, flat at 15:15. State is carried between
10-minute runs by reading the previously deployed copy from GitHub Pages."""
import time

import requests

PAGES_LOG = "https://ramanauday1561.github.io/intraday-trading/data/today_log.json"
EXIT_HHMM = (15, 15)


def fetch_previous(url=PAGES_LOG, tries=3):
    for i in range(tries):
        try:
            r = requests.get(f"{url}?t={int(time.time())}", timeout=15)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(2 * (i + 1))
    return None


def status(entry, bars):
    """Outcome so far for one journal entry, given that symbol's bars for the day."""
    if bars is None or not len(bars):
        return {"state": "NO_DATA"}
    after = bars[bars.index > entry["signal_bar"]]
    if not len(after):
        return {"state": "PENDING"}
    long_ = entry["direction"] == "LONG"
    sgn, sd = (1 if long_ else -1), entry["stop_distance"]
    fill = float(after["open"].iloc[0])
    stop, target = fill - sgn * sd, fill + sgn * entry["rr"] * sd
    state, px, at = "OPEN", float(after["close"].iloc[-1]), after.index[-1]
    for ts, b in after.iterrows():
        if (ts.hour, ts.minute) >= EXIT_HHMM:
            state, px, at = "CLOSED_1515", float(b["open"]), ts
            break
        if (b["low"] <= stop) if long_ else (b["high"] >= stop):
            state, px, at = "STOPPED", stop, ts
            break
        if (b["high"] >= target) if long_ else (b["low"] <= target):
            state, px, at = "TARGET", target, ts
            break
    return {"state": state, "fill": round(fill, 2), "stop": round(stop, 2), "target": round(target, 2),
            "price": round(px, 2), "at": at.strftime("%H:%M"), "r": round(sgn * (px - fill) / sd, 2)}


def update(prev, payload, bars):
    meta = payload["meta"]
    day, mode = meta["data_as_of"][:10], meta["mode"]
    entries = list(prev["entries"]) if prev and prev.get("date") == day and prev.get("mode") == mode else []
    known = {(e["symbol"], e["direction"]) for e in entries}
    if meta["session_phase"] == "ACTIVE" or mode == "demo":
        for s in payload["signals"]:
            if s["direction"] == "NO_TRADE" or (s["symbol"], s["direction"]) in known:
                continue
            e = s["execution"]
            entries.append({"symbol": s["symbol"], "name": s["name"], "direction": s["direction"], "setup": s["setup"],
                            "conviction": s["conviction"], "signal_bar": meta["data_as_of"],
                            "signal_time": meta["data_as_of"][11:16], "ref_price": s["price"]["ltp"],
                            "stop_distance": e["stop_distance"], "rr": e["rr"], "option_idea": e.get("option_idea"),
                            "contradiction": (s.get("contradiction") or {}).get("type")})
            known.add((s["symbol"], s["direction"]))
    for e in entries:
        e["status"] = status(e, bars.get(e["symbol"]))
    done = [e["status"]["r"] for e in entries if "r" in e["status"]]
    return {"date": day, "mode": mode, "updated": meta["generated_at"], "entries": entries,
            "summary": {"signals": len(entries), "net_r_gross": round(sum(done), 2) if done else 0.0,
                        "open": sum(e["status"]["state"] == "OPEN" for e in entries)}}
