"""CLI: python -m engine.run [--demo] [--out data/signals.json]"""
import argparse
import os

from . import market
from .pipeline import build, to_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="force synthetic data")
    ap.add_argument("--out", default="data/signals.json")
    ap.add_argument("--capital", type=float, default=100000)
    ap.add_argument("--risk-pct", type=float, default=50.0)
    a = ap.parse_args()
    payload = build(force_demo=a.demo, capital=a.capital, risk_pct=a.risk_pct)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        f.write(to_json(payload))
    m = payload["meta"]
    print(f"mode={m['mode']} phase={m['session_phase']} analysed={m['analysed']} "
          f"actionable={sum(s['direction'] != 'NO_TRADE' for s in payload['signals'])} -> {a.out}")
    print("data health:", m["health"])
    if market.ERRORS:
        print("data fetch errors:", {k: f"{len(v)} symbols, e.g. {v[:3]}" for k, v in market.ERRORS.items()})


if __name__ == "__main__":
    main()
