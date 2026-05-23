"""Fetch raw OHLC for one index (price + optional FX + optional VIX) via DataSource.

Resilience: if a fetch fails (yfinance flaky, Yahoo blocked us, network down),
we DO NOT overwrite the existing raw.parquet. The dashboard continues serving
yesterday's data with a "data delayed" indicator. A failure is logged for the
operator. Atomic write: write to temp then rename, so partial writes can't
corrupt the file.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from .config import (
    DATA_SOURCE,
    INDICES,
    START_DATE,
    index_dir,
    raw_path,
)
from .datasource import get_source


MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 3


def fetch_one(key: str, start: str = START_DATE) -> pd.DataFrame:
    if key not in INDICES:
        raise ValueError(f"Unknown index '{key}'. Available: {list(INDICES)}")
    cfg = INDICES[key]
    tickers = {col: tk for col, tk in cfg["tickers"].items() if tk is not None}

    index_dir(key).mkdir(parents=True, exist_ok=True)
    source = get_source(DATA_SOURCE)
    print(f"[fetch:{key}] source={source.name} tickers={tickers} from {start}", flush=True)

    df = None
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            df = source.fetch(tickers, start)
            break
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                print(f"[fetch:{key}] attempt {attempt + 1} failed: {e!r}; retrying...", file=sys.stderr, flush=True)
                time.sleep(RETRY_BACKOFF_SEC)
            else:
                print(f"[fetch:{key}] all {MAX_RETRIES + 1} attempts failed: {e!r}", file=sys.stderr, flush=True)

    if df is None:
        # All attempts failed -- keep existing raw.parquet if any
        out = raw_path(key)
        if out.exists():
            print(f"[fetch:{key}] keeping previous raw data ({out})", file=sys.stderr, flush=True)
            return pd.read_parquet(out)
        # No previous data at all -- re-raise
        raise RuntimeError(f"fetch failed and no cached data: {last_err}") from last_err

    # Align to price (the spine), ffill the others
    df = df[df["price"].notna()]
    for col in df.columns:
        if col != "price":
            df[col] = df[col].ffill()
    df.attrs["fetched_at"] = datetime.now(timezone.utc).isoformat()

    # Atomic write: tmp file then rename
    out = raw_path(key)
    tmp = out.with_suffix(out.suffix + ".tmp")
    df.to_parquet(tmp)
    os.replace(tmp, out)

    print(f"[fetch:{key}] wrote {len(df)} rows to {out}", flush=True)
    print(f"[fetch:{key}] range: {df.index.min().date()} → {df.index.max().date()}", flush=True)
    return df


def fetch_all(start: str = START_DATE) -> dict[str, pd.DataFrame]:
    out = {}
    for key in INDICES:
        try:
            out[key] = fetch_one(key, start)
        except Exception as e:
            print(f"[fetch:{key}] FAILED: {e!r}", file=sys.stderr, flush=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for k in sys.argv[1:]:
            fetch_one(k)
    else:
        fetch_all()
