"""Day-level trading rules, pre-registered on 2026-11-09 (docs/IMPROVEMENT_LOG.md) and judged only on later sessions.

1. Direction lock: once the day's first trade is taken, no trade in the opposite direction that day.
   (Both backtests: later trades against the first signal averaged -0.13R.)
2. Daily loss limit: no new trades once the trades already closed today total DAY_LOSS_LIMIT_R or worse.

There is deliberately no cap on the number of trades: every signal that passes all filters is taken.
The same function is used by the live app (run.py) and by the replays/backtests (evaluate.apply_filters),
so the live list and the track record follow identical rules.
"""
from .config import DAY_LOSS_LIMIT_R

CLOSED = ("STOPPED", "BREAKEVEN", "TARGET", "CLOSED_1515")


def gate(direction, signal_time, taken, limit=DAY_LOSS_LIMIT_R):
    """taken: today's accepted trades in the order they were taken, each {direction, exit_time ('HH:MM' or None), r}.
    signal_time 'HH:MM' is the checkpoint bar. A trade counts as closed if it exited at or before that bar.
    Returns a blocker dict, or None if the trade is allowed."""
    if not taken:
        return None
    booked = sum(t["r"] for t in taken if t.get("exit_time") and t["exit_time"] <= signal_time and t.get("r") is not None)
    if booked <= limit:
        return {"code": "DAY_LOSS_LIMIT",
                "message": f"Today's closed trades total {booked:+.2f}R, at or below the {limit:.0f}R daily loss limit: no new trades today."}
    first = taken[0]["direction"]
    if direction != first:
        return {"code": "DAY_DIRECTION",
                "message": f"Today's first trade was {first}; trades against it are skipped for the rest of the day."}
    return None


def taken_from_journal(entries):
    """Live journal entries -> the `taken` list gate() expects."""
    out = []
    for e in entries:
        st = e.get("status") or {}
        closed = st.get("state") in CLOSED
        out.append({"direction": e["direction"], "exit_time": st.get("at") if closed else None, "r": st.get("r")})
    return out


def apply_live(signals, entries, signal_time):
    """Block today's new signals that break the day rules. `signals` must already be in priority order
    (strongest |composite| first); signals already in the journal are left alone. Mutates and returns signals."""
    taken = taken_from_journal(entries)
    known = {(e["symbol"], e["direction"]) for e in entries}
    for s in signals:
        if s["direction"] == "NO_TRADE" or (s["symbol"], s["direction"]) in known:
            continue
        b = gate(s["direction"], signal_time, taken)
        if b:
            s.setdefault("risk", {}).setdefault("blockers", []).insert(0, b)  # first, so the card tags show it
            s["direction"] = "NO_TRADE"
        else:
            taken.append({"direction": s["direction"], "exit_time": None, "r": None})
            known.add((s["symbol"], s["direction"]))
    return signals
