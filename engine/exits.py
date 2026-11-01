"""One exit walker shared by the live journal, the backtest and the exit-policy study, so the rules
the app shows are exactly the rules that were tested."""

EXIT_HHMM = (15, 15)


def walk(long_, bars, sd, rr=None, breakeven_r=None, exit_hhmm=EXIT_HHMM):
    """Fill at the first bar's open; stop `sd` away; optional target at rr*sd; optional move of the stop
    to entry once price has moved breakeven_r*sd in favour (takes effect from the next bar). The stop
    wins if stop and target fall in the same bar. Flat at the first bar at/after `exit_hhmm`.
    Returns (state, fill, exit_price, exit_ts, stop_at_exit) with state OPEN/STOPPED/BREAKEVEN/TARGET/CLOSED_1515."""
    fill = float(bars["open"].iloc[0])
    sgn = 1 if long_ else -1
    stop = fill - sgn * sd
    target = None if rr is None else fill + sgn * rr * sd
    moved = False
    for ts, b in bars.iterrows():
        if (ts.hour, ts.minute) >= exit_hhmm:
            return "CLOSED_1515", fill, float(b["open"]), ts, stop
        if (b["low"] <= stop) if long_ else (b["high"] >= stop):
            return ("BREAKEVEN" if moved else "STOPPED"), fill, stop, ts, stop
        if target is not None and ((b["high"] >= target) if long_ else (b["low"] <= target)):
            return "TARGET", fill, target, ts, stop
        if breakeven_r is not None and not moved and sgn * ((b["high"] if long_ else b["low"]) - fill) >= breakeven_r * sd:
            stop, moved = fill, True
    return "OPEN", fill, float(bars["close"].iloc[-1]), bars.index[-1], stop
