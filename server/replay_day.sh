#!/bin/bash
# Replay one session with Kite's own 5-minute bars (the live engine's data), to compare with what live did.
#   bash /opt/intraday-orders/server/replay_day.sh 2026-11-19
# Needs today's Kite login. Adds the last 5 days of Kite bars to the 4-year history, then replays the date offline.
set -u
D=${1:?usage: $0 YYYY-MM-DD}
APP=/opt/intraday-orders
set -a
[ -f /etc/nifty-orders.env ] && . /etc/nifty-orders.env
. /etc/intraday-orders.env
set +a
export KITE_SHARED_SESSION=/var/lib/nifty-orders/kite/session.json:/var/lib/intraday-orders/kite/session.json
export ARCHIVE_DIR=/var/lib/intraday-history XDG_CACHE_HOME=/var/lib/intraday-history/cache
cd "$APP"
"$APP/venv/bin/python" -m engine.download_kite --days 5 > /dev/null || exit 1
"$APP/venv/bin/python" -W ignore -m engine.backtest --date "$D" --capital 20000 --offline --out /tmp/replay_$D.json \
  | grep -E "REPLAY (TRADE|checks)|^with_news"
