#!/usr/bin/env bash
# One engine run on the VPS (16 Nov 2026): the same "python -m engine.run" GitHub's job does, started by the
# intraday-signals timer 20 seconds after each 15-minute checkpoint bar closes (10:05:20 ... 14:35:20) and once in
# between to refresh the outcomes. Prices come from Kite with the commodities-trading morning login. It writes
# signals.json and today_log.json to the public folder: the order server reads the journal from there, and Caddy
# serves both to the app at /data/.
set -euo pipefail
APP=${APP:-/opt/intraday-orders}
OUT=${INTRADAY_PUBLIC:-/var/lib/intraday-public}
PY=$APP/venv/bin/python; [ -x "$PY" ] || PY=python3
now=$(date +%H%M); dow=$(date +%u)
if [ "$dow" -gt 5 ] || [ "$now" -lt 0900 ] || [ "$now" -gt 1535 ]; then exit 0; fi
cd "$APP"
"$PY" -W ignore -m engine.run --out "$OUT/signals.json" --local-journal \
  --capital "${INTRADAY_CAPITAL:-20000}" --risk-pct "${INTRADAY_RISK_PCT:-5}" | grep -v "^data fetch errors" || true
echo "signal run done $(date +%H:%M:%S)"
