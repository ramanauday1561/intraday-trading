#!/bin/bash
# New signal ideas on the 4-year Kite history (engine/signals_lab.py). Takes a few minutes; run as root:
#   bash /opt/intraday-orders/server/signals_lab.sh
# Prints the result and publishes it at <server address>/data/signals_lab.txt
set -u
APP=/opt/intraday-orders
H=/var/lib/intraday-history
cd "$APP"
ARCHIVE_DIR=$H XDG_CACHE_HOME=$H/cache nice -n 10 "$APP/venv/bin/python" -W ignore -m engine.signals_lab \
  --cache "$H/trades_5m.json" | tee /root/signals_lab.txt
install -m 644 -o orders -g orders /root/signals_lab.txt /var/lib/intraday-public/signals_lab.txt
