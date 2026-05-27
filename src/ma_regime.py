"""Moving-average regime detection. Supports SMA and EMA via the `kind` param.

Logic:
  price > MA(period)  =>  BULL regime
  price < MA(period)  =>  BEAR regime

No neutral state. No probabilities. Just a binary indicator based on whether
today's close is above or below a trailing N-day moving average of closes.

Supports any positive integer period; standard inputs are 50, 100, 200.
The `kind` parameter chooses between:
  - "sma": simple/arithmetic average over the last N days (uniform weight)
  - "ema": exponential average with span=N (recent days weighted more heavily)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import INDICES, raw_path


SUPPORTED_PERIODS = (50, 100, 200)
SUPPORTED_KINDS = ("sma", "ema")


def _validate_index(index_key: str) -> None:
    if index_key not in INDICES:
        raise ValueError(f"Unknown index '{index_key}'")


def _validate_period(period: int) -> None:
    if period not in SUPPORTED_PERIODS:
        raise ValueError(f"Period must be one of {SUPPORTED_PERIODS}, got {period}")


def _validate_kind(kind: str) -> str:
    kind = kind.lower()
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind}")
    return kind


def _compute_ma(prices: pd.Series, period: int, kind: str) -> pd.Series:
    if kind == "ema":
        return prices.ewm(span=period, min_periods=period, adjust=False).mean()
    return prices.rolling(period).mean()


def compute_regime(index_key: str, period: int, kind: str = "sma") -> pd.DataFrame:
    """Return a DataFrame of [date, price, ma, regime] for the requested index/period/kind."""
    _validate_index(index_key)
    _validate_period(period)
    kind = _validate_kind(kind)
    raw = pd.read_parquet(raw_path(index_key))
    prices = raw["price"]
    ma = _compute_ma(prices, period, kind)
    valid = ma.notna()
    df = pd.DataFrame({
        "date": prices.index,
        "price": prices.values,
        "ma": ma.values,
    })
    df = df[valid.values].copy()
    df["regime"] = np.where(df["price"] > df["ma"], "bull", "bear")
    return df


def today(index_key: str, period: int, kind: str = "sma") -> dict:
    df = compute_regime(index_key, period, kind)
    if df.empty:
        return {"error": "no data"}
    last = df.iloc[-1]
    current = str(last["regime"])
    days = 1
    for i in range(len(df) - 2, -1, -1):
        if df.iloc[i]["regime"] == current:
            days += 1
        else:
            break
    cfg = INDICES[index_key]
    return {
        "index_key": index_key,
        "index_name": cfg["name"],
        "index_currency": cfg["currency"],
        "period": period,
        "kind": kind,
        "date": last["date"].strftime("%Y-%m-%d"),
        "price": float(last["price"]),
        "ma": float(last["ma"]),
        "gap_pct": (float(last["price"]) / float(last["ma"]) - 1.0) * 100.0,
        "regime": current,
        "days_in_regime": days,
    }


def history(index_key: str, period: int, days: int = 365, kind: str = "sma") -> list[dict]:
    df = compute_regime(index_key, period, kind)
    df = df.tail(days)
    return [
        {
            "date": r["date"].strftime("%Y-%m-%d"),
            "price": float(r["price"]),
            "ma": float(r["ma"]),
            "regime": str(r["regime"]),
        }
        for _, r in df.iterrows()
    ]


def regime_runs(index_key: str, period: int, min_days: int = 1, kind: str = "sma") -> list[dict]:
    df = compute_regime(index_key, period, kind)
    if df.empty:
        return []
    df = df.reset_index(drop=True)
    df["change"] = df["regime"] != df["regime"].shift()
    df["run_id"] = df["change"].cumsum()
    runs = df.groupby("run_id").agg(
        state=("regime", "first"),
        start=("date", "first"),
        end=("date", "last"),
        days=("date", "count"),
        start_price=("price", "first"),
        end_price=("price", "last"),
    )
    runs = runs[runs["days"] >= min_days]
    out = []
    for _, r in runs.iterrows():
        out.append({
            "state": str(r["state"]),
            "start": r["start"].strftime("%Y-%m-%d"),
            "end": r["end"].strftime("%Y-%m-%d"),
            "days": int(r["days"]),
            "return_pct": (float(r["end_price"]) / float(r["start_price"]) - 1.0) * 100.0,
        })
    return out


def snapshot(kind: str = "sma") -> dict:
    out = {"indices": [], "periods": list(SUPPORTED_PERIODS), "kind": kind}
    for key, cfg in INDICES.items():
        row = {
            "index_key": key,
            "index_name": cfg["name"],
            "country": cfg["country"],
            "currency": cfg["currency"],
            "cells": {},
        }
        try:
            for period in SUPPORTED_PERIODS:
                t = today(key, period, kind)
                row["cells"][period] = {
                    "regime": t["regime"],
                    "days_in_regime": t["days_in_regime"],
                    "price": t["price"],
                    "ma": t["ma"],
                    "gap_pct": t["gap_pct"],
                    "date": t["date"],
                }
            out["indices"].append(row)
        except (FileNotFoundError, KeyError):
            pass
    return out


def stats(index_key: str, period: int, kind: str = "sma") -> dict:
    df = compute_regime(index_key, period, kind)
    if df.empty:
        return {"error": "no data"}
    total = len(df)
    bull = int((df["regime"] == "bull").sum())
    bear = total - bull
    flips = int((df["regime"] != df["regime"].shift()).sum() - 1)
    df_r = df.reset_index(drop=True).copy()
    df_r["change"] = df_r["regime"] != df_r["regime"].shift()
    df_r["run_id"] = df_r["change"].cumsum()
    runs = df_r.groupby("run_id").agg(state=("regime", "first"), days=("regime", "count"))
    avg_run = float(runs["days"].mean())
    avg_bull_run = float(runs[runs["state"] == "bull"]["days"].mean()) if (runs["state"] == "bull").any() else 0
    avg_bear_run = float(runs[runs["state"] == "bear"]["days"].mean()) if (runs["state"] == "bear").any() else 0
    longest_bull = int(runs[runs["state"] == "bull"]["days"].max()) if (runs["state"] == "bull").any() else 0
    longest_bear = int(runs[runs["state"] == "bear"]["days"].max()) if (runs["state"] == "bear").any() else 0

    gap_pct_series = (df["price"] / df["ma"] - 1.0) * 100.0
    gap_mean = float(gap_pct_series.mean())
    gap_std = float(gap_pct_series.std(ddof=1))
    gap_min = float(gap_pct_series.min())
    gap_max = float(gap_pct_series.max())
    current_gap = float(gap_pct_series.iloc[-1])
    current_z = (current_gap - gap_mean) / gap_std if gap_std > 0 else 0.0
    bull_mask = df["regime"] == "bull"
    bear_mask = df["regime"] == "bear"
    bull_gap_mean = float(gap_pct_series[bull_mask].mean()) if bull_mask.any() else 0.0
    bear_gap_mean = float(gap_pct_series[bear_mask].mean()) if bear_mask.any() else 0.0

    if len(df) > 20:
        ma_now = float(df["ma"].iloc[-1])
        ma_20d_ago = float(df["ma"].iloc[-21])
        ma_slope_20d_pct = (ma_now / ma_20d_ago - 1.0) * 100.0
    else:
        ma_slope_20d_pct = 0.0

    log_ret = np.log(df["price"] / df["price"].shift(1)).dropna()
    ann_vol_pct = float(log_ret.std(ddof=1) * np.sqrt(252) * 100.0) if len(log_ret) > 1 else 0.0

    years = total / 252.0
    crossings_per_year = flips / years if years > 0 else 0.0

    return {
        "index_key": index_key,
        "period": period,
        "kind": kind,
        "total_days": total,
        "bull_days": bull,
        "bear_days": bear,
        "bull_pct": (bull / total) * 100.0,
        "bear_pct": (bear / total) * 100.0,
        "n_flips": flips,
        "n_runs": len(runs),
        "avg_run_days": avg_run,
        "avg_bull_run_days": avg_bull_run,
        "avg_bear_run_days": avg_bear_run,
        "longest_bull_days": longest_bull,
        "longest_bear_days": longest_bear,
        "crossings_per_year": crossings_per_year,
        "gap_mean_pct": gap_mean,
        "gap_std_pct": gap_std,
        "gap_min_pct": gap_min,
        "gap_max_pct": gap_max,
        "current_gap_pct": current_gap,
        "current_z_score": current_z,
        "bull_gap_mean_pct": bull_gap_mean,
        "bear_gap_mean_pct": bear_gap_mean,
        "ma_slope_20d_pct": ma_slope_20d_pct,
        "ann_vol_pct": ann_vol_pct,
        "date_range": {
            "start": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "end": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        },
    }
