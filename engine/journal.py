"""Today's signal journal: every signal that became actionable today stays listed with its live outcome.

Signals are snapshots, so one can appear at 10:10 and be gone by 10:20. The journal keeps the first
appearance of each (symbol, direction) and tracks it with the backtest's own rules: entry at the next
bar's open, stop/target from the signal, stop wins ties, flat at 15:15. State is carried between
10-minute runs by reading the previously deployed copy from GitHub Pages."""
import time

import requests

from . import dayrules, exits

PAGES_LOG = "https://ramanauday1561.github.io/intraday-trading/data/today_log.json"


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
    long_, sd = entry["direction"] == "LONG", entry["stop_distance"]
    state, fill, px, at, stop_now = exits.walk(long_, after, sd, entry["rr"], entry.get("breakeven_after_r"))
    sgn = 1 if long_ else -1
    return {"state": state, "fill": round(fill, 2), "stop": round(fill - sgn * sd, 2), "stop_now": round(stop_now, 2),
            "target": round(fill + sgn * entry["rr"] * sd, 2), "price": round(px, 2), "at": at.strftime("%H:%M"),
            "r": round(sgn * (px - fill) / sd, 2)}


def checkpoint_record(signals, top=3):
    """What one checkpoint saw: how many signals passed, and the closest candidates with what blocked them.
    Lets the app (and us) tell 'the live app ran and nothing qualified' from 'the live app did not run'."""
    leaning = sorted((s for s in signals if s.get("bias") not in (None, "NO_TRADE")), key=lambda s: -abs(s.get("composite", 0)))
    return {"signals": sum(s["direction"] != "NO_TRADE" for s in signals),
            "closest": [{"symbol": s["symbol"], "bias": s["bias"], "composite": round(s.get("composite", 0), 3),
                         "blockers": [b["code"] for b in (s.get("risk") or {}).get("blockers", [])]} for s in leaning[:top]]}


def update(prev, payload, bars):
    meta = payload["meta"]
    day, mode = meta["data_as_of"][:10], meta["mode"]
    same_day = bool(prev and prev.get("date") == day and prev.get("mode") == mode)
    entries = list(prev["entries"]) if same_day else []
    checks = dict(prev.get("checks") or {}) if same_day else {}
    for e in entries:  # outcomes so far, needed by the day rules (loss limit)
        e["status"] = status(e, bars.get(e["symbol"]))
    known = {(e["symbol"], e["direction"]) for e in entries}
    if meta["session_phase"] == "ACTIVE" or mode == "demo":
        # Day rules block signals in payload["signals"] too, so the Actionable list and the journal agree.
        dayrules.apply_live(payload["signals"], entries, meta["data_as_of"][11:16])
        for s in payload["signals"]:
            if s["direction"] == "NO_TRADE" or (s["symbol"], s["direction"]) in known:
                continue
            e = s["execution"]
            entries.append({"symbol": s["symbol"], "name": s["name"], "direction": s["direction"], "setup": s["setup"],
                            "conviction": s["conviction"], "signal_bar": meta["data_as_of"],
                            "signal_time": meta["data_as_of"][11:16], "ref_price": s["price"]["ltp"],
                            "stop_distance": e["stop_distance"], "rr": e["rr"], "option_idea": e.get("option_idea"),
                            "breakeven_after_r": e.get("breakeven_after_r"), "size_mult": (s.get("risk") or {}).get("size_multiplier", 1.0),
                            "contradiction": (s.get("contradiction") or {}).get("type")})
            known.add((s["symbol"], s["direction"]))
        if mode == "live":
            checks[meta["data_as_of"][11:16]] = checkpoint_record(payload["signals"])
    for e in entries:
        e["status"] = status(e, bars.get(e["symbol"]))
    done = [e["status"]["r"] for e in entries if "r" in e["status"]]
    return {"date": day, "mode": mode, "updated": meta["generated_at"], "entries": entries, "checks": checks,
            "summary": {"signals": len(entries), "net_r_gross": round(sum(done), 2) if done else 0.0,
                        "open": sum(e["status"]["state"] == "OPEN" for e in entries)}}
