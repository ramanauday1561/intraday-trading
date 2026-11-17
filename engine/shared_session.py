"""Today's Kite access token from the commodities-trading login (16 Nov 2026, asked by the owner: one login a day
for both apps, same API key). That login saves {"at", "until", "token"}: the token encrypted with a key made from
the API secret. Standard library plus cryptography only, so the order server can use it without pandas."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def load(paths, secret, now=None):
    """The access token from the first of `paths` holding a login that has not expired, else None."""
    from cryptography.fernet import Fernet, InvalidToken
    now = now or datetime.now(IST)
    f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(("kite-session:" + secret).encode()).digest()))
    for p in paths:
        try:
            with open(p) as fh:
                s = json.load(fh)
            if now < datetime.fromisoformat(s["until"]):
                return f.decrypt(s["token"].encode()).decode()
        except (OSError, ValueError, KeyError, TypeError, InvalidToken):
            continue
    return None
