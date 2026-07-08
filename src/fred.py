"""Shared FRED fetcher — keyless CSV endpoint, one cache per series.

All FRED access in the pipeline goes through here (fredgraph.csv needs no
API key; it is the same endpoint yieldcurve.py has always used for the
India 10y series). Parquet cache per series under data/fred/, stale-cache
fallback on any fetch failure so a FRED outage never blanks a feed.

Note: ICE BofA series (e.g. BAMLH0A0HYM2) are capped to ~3 years of
history on this endpoint due to ICE redistribution licensing — callers
needing deep history must account for that.
"""
from __future__ import annotations

import csv
import io
import logging
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import DATA_DIR

log = logging.getLogger("regime_compass")

_CACHE_DIR = DATA_DIR / "fred"
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_UA = "RegimeCompass/1.0 (+https://www.regimecompass.com)"


def _cache_path(series_id: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{series_id}.parquet"


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def parse_fred_csv(text: str) -> pd.DataFrame:
    """Parse a fredgraph.csv body into DataFrame(date, value).

    Missing observations arrive as '.' or empty string and are dropped.
    """
    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        date_str = row.get("DATE") or row.get("observation_date", "")
        val_str = list(row.values())[-1] if len(row) > 1 else ""
        if val_str == "." or not val_str:
            continue
        try:
            rows.append({"date": datetime.strptime(date_str, "%Y-%m-%d"), "value": float(val_str)})
        except (ValueError, TypeError):
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def fetch_series(series_id: str, max_age_hours: float = 24) -> pd.DataFrame:
    """Fetch one FRED series (full history) as DataFrame(date, value).

    Cached per series; on any failure the last good cache is served.
    Returns an empty DataFrame only when there is no cache at all.
    """
    cache = _cache_path(series_id)
    if _is_fresh(cache, max_age_hours):
        return pd.read_parquet(cache)

    url = f"{_FRED_CSV}?id={series_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode()
        df = parse_fred_csv(text)
        if df.empty:
            raise ValueError("no parsable observations")
    except Exception as e:
        log.warning("[fred] fetch %s failed: %s", series_id, e)
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()

    df.to_parquet(cache, index=False)
    return df


def fetch_many(series_ids: list[str], max_age_hours: float = 24,
               pause_sec: float = 1.5) -> dict[str, pd.DataFrame]:
    """Fetch several series sequentially with a small politeness pause
    between cache misses (FRED occasionally resets rapid-fire requests)."""
    out = {}
    for sid in series_ids:
        had_fresh = _is_fresh(_cache_path(sid), max_age_hours)
        out[sid] = fetch_series(sid, max_age_hours)
        if not had_fresh:
            time.sleep(pause_sec)
    return out
