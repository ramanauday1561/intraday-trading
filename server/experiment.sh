#!/bin/bash
# Run one pre-registered experiment on the 4-year Kite history (download it first with long_backtest.sh).
#   bash /opt/intraday-orders/server/experiment.sh ENTRY_FILTERS none late,chase,cost
# Runs in the background and waits for any running experiment/analysis first.
# Result:  grep -E "EXPERIMENT|TRAIN|TEST|Error" /root/experiment_<PARAM>.log
set -u
APP=/opt/intraday-orders
if [ "${1:-}" != "--run" ]; then
  [ $# -ge 3 ] || { echo "usage: $0 PARAM VALUE1 VALUE2 [...]"; exit 1; }
  nohup bash "$0" --run "$@" > "/root/experiment_$1.log" 2>&1 &
  echo "Started in the background (pid $!). Result: grep -E \"EXPERIMENT|TRAIN|TEST|Error\" /root/experiment_$1.log"
  exit 0
fi
shift; PARAM=$1; shift
set -a
[ -f /etc/nifty-orders.env ] && . /etc/nifty-orders.env
. /etc/intraday-orders.env
set +a
export ARCHIVE_DIR=/var/lib/intraday-history XDG_CACHE_HOME=/var/lib/intraday-history/cache
while pgrep -f "engine.experiment|engine.analyze" > /dev/null; do sleep 60; done
cd "$APP"
echo "== $(date) experiment $PARAM: $*"
nice -n 19 "$APP/venv/bin/python" -m engine.experiment --param "$PARAM" --values "$@" --offline --out "$ARCHIVE_DIR/exp_$PARAM.json"
echo "== $(date) finished"
