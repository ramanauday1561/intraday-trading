#!/bin/bash
# Where does the strategy lose? Breaks the 4-year Kite backtest down by setup, time, stock, cost and more.
# Run as root on the VPS (after long_backtest.sh has downloaded the history):  bash /opt/intraday-orders/server/analyze.sh
# Runs in the background; waits for any running experiment to finish first. The first run takes a few hours (it runs
# the backtest once and caches the trades); later runs take seconds.
# Result: /root/analysis.txt, also published at <server address>/data/analysis.txt
set -u
APP=/opt/intraday-orders
OUT=/root/analysis.txt
if [ "${1:-}" != "--run" ]; then
  nohup bash "$0" --run > /root/analysis.log 2>&1 &
  echo "Started in the background (pid $!). Done when /root/analysis.txt exists: cat /root/analysis.txt"
  exit 0
fi
set -a
[ -f /etc/nifty-orders.env ] && . /etc/nifty-orders.env
. /etc/intraday-orders.env
set +a
export ARCHIVE_DIR=/var/lib/intraday-history XDG_CACHE_HOME=/var/lib/intraday-history/cache
while pgrep -f "engine.experiment" > /dev/null; do sleep 60; done   # don't compete with a running experiment
cd "$APP"
rm -f "$OUT"
nice -n 19 "$APP/venv/bin/python" -W ignore -m engine.analyze --offline --cache "$ARCHIVE_DIR/trades_5m.json" > "$OUT.tmp" \
  && mv "$OUT.tmp" "$OUT" && install -m 644 -o orders -g orders "$OUT" /var/lib/intraday-public/analysis.txt
echo "== $(date) finished"
