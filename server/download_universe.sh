#!/bin/bash
# Download 4 years of Kite 5-minute bars for a stock list into the long-backtest history.
#   bash /opt/intraday-orders/server/download_universe.sh nifty50
# Needs today's Kite login. Existing bars are kept; only missing ones are added. Takes about 5-10 minutes.
set -u
SET=${1:-nifty50}
APP=/opt/intraday-orders
set -a
[ -f /etc/nifty-orders.env ] && . /etc/nifty-orders.env
. /etc/intraday-orders.env
set +a
export KITE_SHARED_SESSION=/var/lib/nifty-orders/kite/session.json:/var/lib/intraday-orders/kite/session.json
export ARCHIVE_DIR=/var/lib/intraday-history XDG_CACHE_HOME=/var/lib/intraday-history/cache UNIVERSE_SET=$SET
cd "$APP"
nice -n 10 "$APP/venv/bin/python" -m engine.download_kite --days 1500 | tail -30
