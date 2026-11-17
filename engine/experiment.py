"""Compare variants of one engine parameter on the same data, judged by a pre-registered rule.

    python -m engine.experiment --param ATR_RANGE_MODE --values current rr2 off [--hourly] [--offline]

Each variant runs in its own process (the parameter is read from the environment at import), so variants
cannot leak into each other. Selection = the live rules (first signal per stock per day + day rules).
Sessions are split 60/40 chronologically; mean net R per trade after costs is reported for TRAIN and TEST.
--offline uses only the repo's 5-minute archive (no Yahoo, no news): for quick local checks.
"""
import argparse
import json
import os
import subprocess
import sys

from . import evaluate


def one(hourly, offline):
    from . import backtest, market
    if offline:
        market.fetch_many = lambda *a, **k: {}
    res = backtest.run(hourly=hourly, fetch_news=not offline, keep_candidates=True)
    days = res["_days"]
    sel = evaluate.apply_filters(res["_cands"], day_rules=True)
    cut = int(len(days) * evaluate.TRAIN_FRACTION)
    train = {d.isoformat() for d in days[:cut]}
    out = {}
    for name, part in (("train", [t for t in sel if t["date"] in train]), ("test", [t for t in sel if t["date"] not in train])):
        m = evaluate.metrics(part)
        out[name] = {"trades": m.get("trades", 0), "avg_net_r": m.get("avg_net_r"), "ci95": evaluate.bootstrap_ci(part)}
    out["sessions"] = [days[0].isoformat(), days[-1].isoformat(), len(days), cut]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--param", required=True)
    ap.add_argument("--values", nargs="+", required=True)
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.child:
        print("RESULT " + json.dumps(one(a.hourly, a.offline)))
        return
    results = {}
    for v in a.values:
        env = dict(os.environ, **{a.param: v})
        cmd = [sys.executable, "-W", "ignore", "-m", "engine.experiment", "--param", a.param, "--values", v, "--child"]
        cmd += ["--hourly"] * a.hourly + ["--offline"] * a.offline
        p = subprocess.run(cmd, env=env, capture_output=True, text=True)
        line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
        if not line:
            print(p.stdout[-2000:], p.stderr[-2000:]); raise SystemExit(f"variant {v} failed")
        results[v] = json.loads(line[7:])
    res = "1h (2 years)" if a.hourly else "5m"
    print(f"EXPERIMENT {a.param} · {res} · sessions {next(iter(results.values()))['sessions']}")
    for v, r in results.items():
        f = lambda p: f"n={r[p]['trades']:4d} avg={r[p]['avg_net_r'] if r[p]['avg_net_r'] is not None else float('nan'):+.3f}R ci={r[p]['ci95']}"
        print(f"  {v:10s} TRAIN {f('train')} | TEST {f('test')}")
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({"param": a.param, "resolution": res, "results": results}, fh, indent=1)


if __name__ == "__main__":
    main()
