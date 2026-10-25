"""Growing on-disk archive of 5-minute bars and headlines (committed to the repo by a daily job).

Yahoo only serves ~60 sessions of 5-minute data; saving each day's bars means history keeps growing.
Headlines are stored with their own publication time so replays stay point-in-time."""
import gzip
import json
import os
from datetime import datetime, timezone

import pandas as pd

from .config import IST, INDEX_SYMBOLS, UNIVERSE

ROOT = os.environ.get("ARCHIVE_DIR", "archive")
COLS = ["open", "high", "low", "close", "volume"]


def _safe(sym):
    return sym.replace("^", "IDX_").replace("&", "and").replace(".", "_")


def _bars_path(sym):
    return os.path.join(ROOT, "5m", _safe(sym) + ".csv.gz")


def _news_path(sym):
    return os.path.join(ROOT, "news", _safe(sym) + ".jsonl.gz")


def load_bars(sym):
    p = _bars_path(sym)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(IST)
    return df[COLS]


def merge_bars(old, new):
    if old is None:
        return new
    if new is None:
        return old
    df = pd.concat([old, new])
    return df[~df.index.duplicated(keep="last")].sort_index()


def save_bars(sym, df):
    os.makedirs(os.path.dirname(_bars_path(sym)), exist_ok=True)
    out = df.copy()
    out.index = out.index.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    out.round(4).to_csv(_bars_path(sym), compression="gzip")


def load_news(sym):
    p = _news_path(sym)
    if not os.path.exists(p):
        return []
    items = []
    with gzip.open(p, "rt") as f:
        for line in f:
            d = json.loads(line)
            d["ts"] = datetime.fromisoformat(d["ts"])
            items.append(d)
    return items


def save_news(sym, items):
    os.makedirs(os.path.dirname(_news_path(sym)), exist_ok=True)
    merged = {i["title"]: i for i in load_news(sym) + items}
    rows = sorted(merged.values(), key=lambda i: i["ts"])
    with gzip.open(_news_path(sym), "wt") as f:
        for i in rows:
            f.write(json.dumps({**i, "ts": i["ts"].astimezone(timezone.utc).isoformat()}) + "\n")
    return len(rows)


def all_symbols():
    idx = sorted(set(INDEX_SYMBOLS.values()) | {u[3] for u in UNIVERSE})
    return [u[0] + ".NS" for u in UNIVERSE] + idx


def update():
    """Fetch the latest Yahoo window + headlines and merge into the archive."""
    from . import news as newsmod
    from .market import fetch_many
    syms = all_symbols()
    got = fetch_many(syms, "60d", "5m")
    added = 0
    for s in syms:
        new = got.get(s)
        if new is None:
            continue
        old = load_bars(s)
        merged = merge_bars(old, new)
        added += len(merged) - (0 if old is None else len(old))
        save_bars(s, merged)
    general = []
    for src, url in newsmod.GENERAL_FEEDS:
        general += newsmod.fetch_feed(url, src)
    import re
    n_news = 0
    for sym, name, *_ in UNIVERSE:
        items = newsmod.fetch_symbol_news(name, sym)
        key = re.compile(r"\b(%s|%s)\b" % (re.escape(name.split()[0]), re.escape(sym)), re.I)
        items += [i for i in general if key.search(i["title"])]
        n_news = save_news(sym, items)
    print(f"archive: +{added} bars across {len(syms)} symbols; RELIANCE-style news rows now {n_news}")


if __name__ == "__main__":
    update()
