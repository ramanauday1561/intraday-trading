"""CLI: python -m engine.run [--demo] [--out data/signals.json]"""
import argparse
import json
import os

from . import journal, market
from .config import DEFAULT_RISK_PCT
from .pipeline import build, to_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="force synthetic data")
    ap.add_argument("--out", default="data/signals.json")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--risk-pct", type=float, default=DEFAULT_RISK_PCT)
    ap.add_argument("--local-journal", action="store_true", help="carry today's journal in the output folder (VPS)")
    a = ap.parse_args()
    payload, bars = build(force_demo=a.demo, capital=a.capital, risk_pct=a.risk_pct, return_bars=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    log_path = os.path.join(os.path.dirname(a.out) or ".", "today_log.json")
    if payload["meta"]["mode"] == "demo":
        prev = None
    elif a.local_journal:  # on the VPS the journal is carried in the output folder, not read back from GitHub Pages
        try:
            with open(log_path) as f:
                prev = json.load(f)
        except (OSError, ValueError):
            prev = None
    else:
        prev = journal.fetch_previous()
    log = journal.update(prev, payload, bars)  # also applies the day rules to payload["signals"]
    for path, doc in ((log_path, log), (a.out, payload)):  # whole files only: the order server reads them
        with open(path + ".tmp", "w") as f:
            f.write(to_json(doc))
        os.replace(path + ".tmp", path)
    print("journal:", log["summary"], "carried over" if prev and prev.get("date") == log["date"] else "new day/started fresh")
    m = payload["meta"]
    print(f"mode={m['mode']} phase={m['session_phase']} analysed={m['analysed']} "
          f"actionable={sum(s['direction'] != 'NO_TRADE' for s in payload['signals'])} -> {a.out}")
    print("data health:", m["health"])
    if market.ERRORS:
        print("data fetch errors:", {k: f"{len(v)} symbols, e.g. {v[:3]}" for k, v in market.ERRORS.items()})


if __name__ == "__main__":
    main()
