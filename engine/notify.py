"""Push a notification for each NEW actionable signal (not actionable in the previously deployed run).

Secrets (GitHub → Settings → Secrets and variables → Actions):
  VAPID_PRIVATE_KEY   private half of the key pair whose public half is in assets/app.js
  PUSH_SUBSCRIPTIONS  JSON copied from the app's ⚙ → Notifications (one object, or a list of them)
Usage: python -m engine.notify [--test]"""
import argparse
import json
import os
import time

import requests

from . import webpush

PAGES_URL = "https://ramanauday1561.github.io/intraday-trading/data/signals.json"
SUBJECT = "https://github.com/ramanauday1561/intraday-trading"


def _subscriptions():
    raw = os.environ.get("PUSH_SUBSCRIPTIONS", "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    return data if isinstance(data, list) else [data]


def _actionable(payload):
    return {(s["symbol"], s["direction"]): s for s in payload.get("signals", []) if s["direction"] != "NO_TRADE"}


def new_signals(current, previous):
    """Signals actionable now that were not actionable (same symbol + direction) in the previous run."""
    if current["meta"].get("mode") != "live" or current["meta"].get("session_phase") != "ACTIVE":
        return []
    before = _actionable(previous or {})
    return [s for k, s in _actionable(current).items() if k not in before]


def message_for(s):
    e, oi = s["execution"], s["execution"].get("option_idea")
    title = f"{s['direction']} {s['symbol']}" + (f" → {oi['action']} {oi['strike']}" if oi else "")
    body = f"Entry ₹{e['entry_trigger']} · SL ₹{e['stop']} · Target ₹{e['target']} · conviction {s['conviction']}"
    if oi:
        body += f" · exp {oi['expiry']}"
    return {"title": title, "body": body + ". Paper-trade idea, not advice.",
            "tag": f"{s['symbol']}-{s['direction']}", "url": f"./?sym={s['symbol']}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="send one test notification")
    ap.add_argument("--current", default="data/signals.json")
    a = ap.parse_args()
    key_b64, subs = os.environ.get("VAPID_PRIVATE_KEY", "").strip(), _subscriptions()
    if not key_b64 or not subs:
        print("notify: VAPID_PRIVATE_KEY or PUSH_SUBSCRIPTIONS not set; skipping")
        return
    key = webpush.load_vapid_private(key_b64)
    if a.test:
        msgs = [{"title": "Intraday Quant Desk", "body": "Test notification: push is working.", "tag": "test", "url": "./"}]
    else:
        current = json.load(open(a.current))
        try:  # the copy users currently see; cache-buster avoids a stale CDN copy
            previous = requests.get(f"{PAGES_URL}?t={int(time.time())}", timeout=15).json()
        except (requests.RequestException, ValueError):
            previous = None
        msgs = [message_for(s) for s in new_signals(current, previous)][:5]
    for m in msgs:
        for sub in subs:
            status = webpush.send(sub, m, key, SUBJECT)
            print(f"notify: '{m['title']}' -> {status}")
    if not msgs:
        print("notify: no new actionable signals")


if __name__ == "__main__":
    main()
