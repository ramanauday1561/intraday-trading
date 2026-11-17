"""Download years of 5-minute candles from Kite into the archive format, for long backtests.

    ARCHIVE_DIR=/var/lib/intraday-history python -m engine.download_kite --days 1500

Needs today's Kite login (on the VPS: the shared morning login, KITE_SHARED_SESSION). Kite returns at most 100 days
of 5-minute candles a request, so each symbol is fetched in 100-day chunks, newest first, until a chunk comes back
empty or --days is reached. Results are merged into ARCHIVE_DIR (existing bars kept). Read-only: no orders.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta

import pandas as pd

from . import archive
from . import kite as kitemod
from .config import INDEX_SYMBOLS, IST, UNIVERSE

CHUNK_DAYS = 100


def symbols():
    return [u[0] + ".NS" for u in UNIVERSE] + sorted(set(INDEX_SYMBOLS.values()) | {u[3] for u in UNIVERSE})


def download(kc, ys, token, days, now):
    parts, end = [], now.replace(tzinfo=None)
    start_limit = end - timedelta(days=days)
    while end > start_limit:
        start = max(start_limit, end - timedelta(days=CHUNK_DAYS))
        try:
            df = kc.candles(token, "5minute", start, end)
        except kitemod.KiteError as e:
            print(f"  {ys}: {start:%Y-%m-%d}..{end:%Y-%m-%d} failed: {e}", file=sys.stderr)
            break
        if df is None or not len(df):
            break
        parts.append(df)
        end = start - timedelta(minutes=1)
    return pd.concat(parts).sort_index() if parts else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1500, help="calendar days back (default ~4 years)")
    ap.add_argument("--only", nargs="*", help="Yahoo-style symbols to fetch (default: universe + indices)")
    a = ap.parse_args()
    kc = kitemod.load_client()
    if not kc:
        raise SystemExit("No Kite session today: log in to Kite first (the morning login), then run this again.")
    nse = {f"NSE:{r['symbol']}": r["token"] for r in kc.instruments("NSE")}
    now = datetime.now(IST)
    t0 = time.time()
    for ys in (a.only or symbols()):
        tok = nse.get(kitemod.kite_symbol(ys))
        if tok is None:
            print(f"{ys}: no Kite instrument, skipped"); continue
        new = download(kc, ys, tok, a.days, now)
        if new is None:
            print(f"{ys}: nothing returned"); continue
        merged = archive.merge_bars(archive.load_bars(ys), new[archive.COLS])
        archive.save_bars(ys, merged)
        days = merged.index.normalize().nunique()
        print(f"{ys}: {merged.index[0]:%Y-%m-%d} .. {merged.index[-1]:%Y-%m-%d} · {days} sessions · {len(merged):,} bars", flush=True)
    print(f"done in {time.time() - t0:.0f}s -> {archive.ROOT}")


if __name__ == "__main__":
    main()
