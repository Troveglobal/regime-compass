"""Yield curve data — US Treasury yields via yfinance, India from FRED monthly.

US yields (yfinance, daily):
  - ^IRX: 13-Week T-Bill
  - ^FVX: 5-Year Treasury
  - ^TNX: 10-Year Treasury
  - ^TYX: 30-Year Treasury

No free 2-year ticker in yfinance, so we use 13-week as the short end
and compute the 3m-10y spread (also a valid recession indicator).

India: INDIRLTLT01STM from FRED (monthly, ~2 month lag)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

from . import fred
from .config import DATA_DIR

log = logging.getLogger("regime_compass")

_CACHE_DIR = DATA_DIR / "yields"


def _cache_path(name: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{name}.parquet"


def _is_fresh(path: Path, max_age_hours: int = 24) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < max_age_hours * 3600


def _fetch_yield_yf(ticker: str, name: str) -> pd.DataFrame:
    cache = _cache_path(name)
    if _is_fresh(cache):
        return pd.read_parquet(cache)

    try:
        df = yf.download(ticker, start="2000-01-01", progress=False)
        if df.empty:
            raise ValueError("empty")
        series = df["Close"]
        if isinstance(series.columns, pd.MultiIndex):
            series = series[ticker]
        series = series.squeeze()
        result = pd.DataFrame({
            "date": series.index.tz_localize(None) if series.index.tz else series.index,
            "value": series.values,
        }).dropna().reset_index(drop=True)
        result.to_parquet(cache, index=False)
        return result
    except Exception as e:
        log.warning("Failed to fetch %s via yfinance: %s", ticker, e)
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()


def _fetch_fred(series_id: str, cache_name: str, max_age: int = 24) -> pd.DataFrame:
    # Delegates to the shared FRED fetcher (src/fred.py); cache_name is
    # unused now that caching is per series id in data/fred/.
    return fred.fetch_series(series_id, max_age_hours=max_age)


def us_yield_curve() -> dict:
    y3m = _fetch_yield_yf("^IRX", "us_3m")
    y5y = _fetch_yield_yf("^FVX", "us_5y")
    y10 = _fetch_yield_yf("^TNX", "us_10y")
    y30 = _fetch_yield_yf("^TYX", "us_30y")

    if y10.empty or y3m.empty:
        return {"error": "Could not fetch US yield data"}

    merged = y3m.merge(y10, on="date", suffixes=("_3m", "_10y"))
    if not y5y.empty:
        merged = merged.merge(y5y.rename(columns={"value": "value_5y"}), on="date", how="left")
    if not y30.empty:
        merged = merged.merge(y30.rename(columns={"value": "value_30y"}), on="date", how="left")

    merged["spread_3m10y"] = merged["value_10y"] - merged["value_3m"]

    current_3m = float(y3m["value"].iloc[-1])
    current_5y = float(y5y["value"].iloc[-1]) if not y5y.empty else None
    current_10y = float(y10["value"].iloc[-1])
    current_30y = float(y30["value"].iloc[-1]) if not y30.empty else None
    current_spread = current_10y - current_3m

    inversions = merged[merged["spread_3m10y"] < 0]
    inversion_periods = []
    if not inversions.empty:
        groups = (inversions["date"].diff() > pd.Timedelta(days=30)).cumsum()
        for _, grp in inversions.groupby(groups):
            inversion_periods.append({
                "start": str(grp["date"].iloc[0].date()),
                "end": str(grp["date"].iloc[-1].date()),
                "days": len(grp),
                "min_spread": round(float(grp["spread_3m10y"].min()), 3),
            })

    series = []
    for _, r in merged.iterrows():
        row = {
            "date": str(r["date"].date()),
            "y3m": round(float(r["value_3m"]), 3),
            "y10": round(float(r["value_10y"]), 3),
            "spread": round(float(r["spread_3m10y"]), 3),
        }
        if "value_5y" in r and pd.notna(r.get("value_5y")):
            row["y5y"] = round(float(r["value_5y"]), 3)
        if "value_30y" in r and pd.notna(r.get("value_30y")):
            row["y30"] = round(float(r["value_30y"]), 3)
        series.append(row)

    inverted_now = current_spread < 0

    return {
        "country": "US",
        "spread_label": "3m-10y",
        "current": {
            "y3m": round(current_3m, 3),
            "y5y": round(current_5y, 3) if current_5y else None,
            "y10": round(current_10y, 3),
            "y30": round(current_30y, 3) if current_30y else None,
            "spread": round(current_spread, 3),
            "inverted": inverted_now,
            "date": str(y10["date"].iloc[-1].date()),
        },
        "inversions": inversion_periods,
        "data_start": str(merged["date"].iloc[0].date()),
        "data_end": str(merged["date"].iloc[-1].date()),
        "series": series,
    }


def india_yield() -> dict:
    df = _fetch_fred("INDIRLTLT01STM", "india_10y", max_age=168)

    if df.empty:
        return {"error": "Could not fetch India yield data"}

    current = float(df["value"].iloc[-1])
    hist_mean = float(df["value"].mean())

    recent = df[df["date"] >= "2012-01-01"]
    series = [
        {"date": str(r["date"].date()), "value": round(float(r["value"]), 3)}
        for _, r in recent.iterrows()
    ]

    return {
        "country": "India",
        "metric": "10-Year G-Sec Yield",
        "current": round(current, 3),
        "mean": round(hist_mean, 3),
        "date": str(df["date"].iloc[-1].date()),
        "note": "Monthly data from FRED, approximately 2 month lag",
        "series": series,
    }
