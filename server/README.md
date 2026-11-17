# Order server (the app's stock signals, real money)

Added 16 Nov 2026 at the owner's request. It runs on the same Mumbai VPS as the commodities-trading order server
(Zerodha takes API orders only from a whitelisted static IP), with the same Kite API key and the same morning login.

## What runs on the VPS

- **Signals** (`signal_run.sh`, the `intraday-signals` timer): the engine runs 20 seconds after each 15-minute
  checkpoint bar closes (10:05:20 ... 14:35:20) with Kite prices, and once in between to refresh outcomes. It writes
  `signals.json` and `today_log.json`, served to the app at `https://<server>/data/`.
- **Orders** (`orders_server.py`, the `intraday-orders` service): each new journal entry becomes a trade, once:
  1. quantity = capital x risk % x the signal's size multiplier / the stop distance, cut to the margin left of the
     capital (skipped if less than half fits);
  2. a LIMIT order just through the best price (BUY for LONG, SELL for SHORT, MIS), re-priced 3 times, then cancelled;
  3. on the fill, a stop-loss order at the exchange;
  4. closed at the 2R target, the stop moved to the entry price after +1R, everything closed at **15:05**
     (stocks stop trading normally at 15:15 because of the closing auction).
- A signal is taken only within 5 minutes of its bar closing and while the price is within half a stop distance of
  the signal's price.

## Limits (in `/etc/intraday-orders.env`, not on the phone)

`INTRADAY_CAPITAL` (20000), `INTRADAY_RISK_PCT` (5), `INTRADAY_MAX_LOSS_DAY` (5000: everything is closed and nothing
new is taken that day), `INTRADAY_MAX_OPEN` (0 = as many as the capital fits), `INTRADAY_MODE` (`real`, `practice` =
no orders, fills at Kite's live prices, or `off`). After a change: `systemctl restart intraday-orders`.

## Setup and updates (VPS Web console, as root)

```
curl -fsSL https://raw.githubusercontent.com/ramanauday1561/intraday-trading/main/server/setup.sh -o intraday-setup.sh
bash intraday-setup.sh
```

It prints the server address and, the first time, the passcode: enter both in the app under Settings → Order server.
Run it again to update. The commodities setup rewrites the web config; a watcher (`intraday-caddy.path`) puts this
server's address back within seconds.

## Every day

Log in to Kite once before 10:00 (the NIFTY app's Kite page, or Settings → Log in to Kite here: it is the same login).
The Money tab shows this app's own money only (its trades' real fills after Kite's charges, on top of its capital; the
Kite balance, shared with the NIFTY app, is one small line), with **Close now** and **Stop everything**.

## Good to know

- Kite has one pool of money. This server counts only its own trades against its capital and its loss limit.
- The stop is a stop-limit order: in a fast move the loss can be more than planned. If it triggers and does not fill,
  the server re-prices it after 10 seconds.
- If the server goes down the stop is still at the exchange; the target and the 15:05 exit are not. Check Kite.
- Trades, fills and P&L stay on the server's disk (`/var/lib/intraday-orders`) and the phone; nothing goes to the repo.
- The signals have not passed their backtests. This trades them anyway, at the owner's request.
- Logs: `journalctl -u intraday-orders -f` and `journalctl -u intraday-signals -n 30`.

## Long backtests with Kite history (run on the VPS, after market hours)

Yahoo gives only 60 days of 5-minute data. Kite gives years. After the morning Kite login, once:

```
cd /opt/intraday-orders && set -a && . /etc/intraday-orders.env && set +a
ARCHIVE_DIR=/var/lib/intraday-history nice -n 19 venv/bin/python -m engine.download_kite --days 1500
```

Then compare variants on that history (offline: no Yahoo, no news; takes hours, so `nice` keeps the order server
responsive; run it after 15:30):

```
ARCHIVE_DIR=/var/lib/intraday-history nice -n 19 venv/bin/python -m engine.experiment \
  --param EXIT_TARGET_MODE --values 2R none --offline --out /var/lib/intraday-history/exp_target.json
ARCHIVE_DIR=/var/lib/intraday-history nice -n 19 venv/bin/python -m engine.experiment \
  --param ATR_RANGE_MODE --values current rr2 off --offline --out /var/lib/intraday-history/exp_atr.json
```
