#!/bin/bash
# Long backtest on the VPS: download years of Kite 5-minute history, then run both pre-registered experiments.
# Run as root after the morning Kite login, after 15:30:   bash /opt/intraday-orders/server/long_backtest.sh
# Runs in the background (survives the console closing). Progress: tail -f /root/backtest.log
# Results:  grep -E "EXPERIMENT|TRAIN|TEST|Error|No Kite" /root/backtest.log
set -u
APP=/opt/intraday-orders
LOG=/root/backtest.log
if [ "${1:-}" != "--run" ]; then
  nohup bash "$0" --run > "$LOG" 2>&1 &
  echo "Started in the background (pid $!). Progress: tail -f $LOG"
  exit 0
fi
set -a
[ -f /etc/nifty-orders.env ] && . /etc/nifty-orders.env
. /etc/intraday-orders.env
set +a
# Same Kite login as the order server (see setup.sh): the shared morning session.
export KITE_SHARED_SESSION=/var/lib/nifty-orders/kite/session.json:/var/lib/intraday-orders/kite/session.json
export ARCHIVE_DIR=/var/lib/intraday-history XDG_CACHE_HOME=/var/lib/intraday-history/cache
mkdir -p "$ARCHIVE_DIR"
cd "$APP"
PY="nice -n 19 $APP/venv/bin/python"
echo "== $(date) download"
$PY -m engine.download_kite --days 1500 || exit 1
echo "== $(date) experiment: target"
$PY -m engine.experiment --param EXIT_TARGET_MODE --values 2R none --offline --out "$ARCHIVE_DIR/exp_target.json"
echo "== $(date) experiment: range gate"
$PY -m engine.experiment --param ATR_RANGE_MODE --values current rr2 off --offline --out "$ARCHIVE_DIR/exp_atr.json"
echo "== $(date) finished"
