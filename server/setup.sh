#!/usr/bin/env bash
# Sets up the intraday order server and the signal runs on the VPS that already runs the commodities-trading order
# server (Hostinger, Mumbai; its IP is the one whitelisted in the Kite developer console). Run as root in hPanel's
# Web console:
#   curl -fsSL https://raw.githubusercontent.com/ramanauday1561/intraday-trading/main/server/setup.sh -o intraday-setup.sh
#   bash intraday-setup.sh
# It uses the Kite key and secret already on this box (/etc/nifty-orders.env); only if they are not there does it ask
# for them (typed, not shown). It makes a passcode for the app and prints it once. Run it again to update the code
# (settings are kept).
set -euo pipefail

REPO=https://github.com/ramanauday1561/intraday-trading.git
BRANCH=${BRANCH:-main}
APP=/opt/intraday-orders
ENV=/etc/intraday-orders.env
NENV=/etc/nifty-orders.env
DATA=/var/lib/intraday-orders
PUB=/var/lib/intraday-public

apt-get update -qq
apt-get install -y -qq python3 python3-cryptography python3-venv git caddy >/dev/null

if [ -d "$APP/.git" ]; then git -C "$APP" fetch -q --depth 1 origin "$BRANCH" && git -C "$APP" reset -q --hard FETCH_HEAD
else git clone -q --depth 1 -b "$BRANCH" "$REPO" "$APP"; fi
id orders >/dev/null 2>&1 || useradd --system --home /var/lib/nifty-orders --create-home --shell /usr/sbin/nologin orders
install -d -m 700 -o orders -g orders "$DATA"
install -d -m 755 -o orders -g orders "$PUB"

IP=$(curl -4 -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
HOST="intraday.${IP//./-}.sslip.io"

if ! test -f "$ENV"; then
  install -m 600 -o root -g root /dev/null "$ENV"
  if ! grep -q '^KITE_API_KEY=.\+' "$NENV" 2>/dev/null; then
    read -rsp "Kite API key: " KEY; echo
    read -rsp "Kite API secret: " SECRET; echo
    printf 'KITE_API_KEY=%s\nKITE_API_SECRET=%s\n' "$KEY" "$SECRET" >>"$ENV"
    unset KEY SECRET
  fi
  read -rp "Capital for the stock trades, rupees [20000]: " CAP; CAP=${CAP:-20000}
  read -rp "Risk a trade, % of that capital [5]: " RISK; RISK=${RISK:-5}
  read -rp "Most rupees to lose in a day before everything is closed [5000]: " LOSS; LOSS=${LOSS:-5000}
  read -rp "Real orders or practice (no orders)? [real]: " MODE; MODE=${MODE:-real}
  CODE=$(python3 -c 'import secrets; print(secrets.token_urlsafe(30))')
  printf 'INTRADAY_PASSCODE=%s\nINTRADAY_CAPITAL=%s\nINTRADAY_RISK_PCT=%s\nINTRADAY_MAX_LOSS_DAY=%s\nINTRADAY_MAX_OPEN=0\nINTRADAY_MODE=%s\nALLOW_ORIGIN=https://ramanauday1561.github.io\n' \
    "$CODE" "$CAP" "$RISK" "$LOSS" "$MODE" >>"$ENV"
  NEW=1
fi

python3 -m venv --system-site-packages "$APP/venv" >/dev/null
"$APP/venv/bin/pip" install -q --upgrade -r "$APP/requirements.txt" >/dev/null

COMMON="User=orders
EnvironmentFile=-$NENV
EnvironmentFile=$ENV
Environment=INTRADAY_DATA=$DATA INTRADAY_PUBLIC=$PUB APP=$APP
Environment=KITE_SHARED_SESSION=/var/lib/nifty-orders/kite/session.json:$DATA/kite/session.json
Environment=XDG_CACHE_HOME=$DATA/cache
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA $PUB
ProtectHome=true
PrivateTmp=true"

cat >/etc/systemd/system/intraday-orders.service <<EOF
[Unit]
Description=Intraday order server (stock signals, real money)
After=network-online.target
Wants=network-online.target

[Service]
$COMMON
ExecStart=$APP/venv/bin/python $APP/server/orders_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/intraday-signals.service <<EOF
[Unit]
Description=Intraday signals: one engine run (server/signal_run.sh)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
$COMMON
ExecStart=/bin/bash $APP/server/signal_run.sh
TimeoutStartSec=280
EOF

cat >/etc/systemd/system/intraday-signals.timer <<EOF
[Unit]
Description=Intraday signals: 20 s after each 15-minute checkpoint bar closes, and once in between (weekdays)

[Timer]
OnCalendar=Mon..Fri 09..15:05,20,35,50:20
OnCalendar=Mon..Fri 09..15:12,27,42,57:20
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

# The web address: its own name on the same IP, as a block in Caddy's config. The commodities setup rewrites that
# file (17 Nov 2026: it did at 07:54 and the app could not reach this server until this script ran again), so the
# block is kept in its own file and a watcher puts it back, and reloads Caddy, whenever the config changes.
cat >/etc/caddy/intraday.block <<EOF

$HOST {
  handle_path /data/* {
    root * $PUB
    header Access-Control-Allow-Origin "*"
    header Cache-Control "no-store"
    file_server
  }
  handle {
    reverse_proxy 127.0.0.1:8788
  }
}
EOF
cat >/usr/local/sbin/intraday-caddy <<EOF
#!/bin/sh
# Adds the intraday block to Caddy's config when it is missing (does nothing, and reloads nothing, when it is there).
grep -q "^$HOST {" /etc/caddy/Caddyfile 2>/dev/null && exit 0
cat /etc/caddy/intraday.block >>/etc/caddy/Caddyfile
sleep 3
systemctl reload caddy || systemctl restart caddy
EOF
chmod 755 /usr/local/sbin/intraday-caddy
cat >/etc/systemd/system/intraday-caddy.service <<EOF
[Unit]
Description=Keep the intraday web address in Caddy's config

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/intraday-caddy
EOF
cat >/etc/systemd/system/intraday-caddy.path <<EOF
[Unit]
Description=Watch Caddy's config for the intraday web address

[Path]
PathChanged=/etc/caddy/Caddyfile

[Install]
WantedBy=multi-user.target
EOF
/usr/local/sbin/intraday-caddy

grep -q '^precedence ::ffff:0:0/96' /etc/gai.conf 2>/dev/null || echo 'precedence ::ffff:0:0/96  100' >>/etc/gai.conf   # orders must leave from the whitelisted IPv4
timedatectl set-timezone Asia/Kolkata
systemctl daemon-reload
systemctl enable -q --now intraday-orders intraday-signals.timer intraday-caddy.path
systemctl restart intraday-orders

echo
echo "Done. Server address for the app (Settings -> Order server):  https://$HOST"
if [ "${NEW:-}" = 1 ]; then
  echo "Passcode for the app (shown once; it is also in $ENV, readable only by root):"
  echo "  $(grep ^INTRADAY_PASSCODE= "$ENV" | cut -d= -f2-)"
fi
echo "Mode: $(grep ^INTRADAY_MODE= "$ENV" | cut -d= -f2-)   (change: nano $ENV, then systemctl restart intraday-orders)"
echo "Logs: journalctl -u intraday-orders -f     signal runs: journalctl -u intraday-signals -n 30"
