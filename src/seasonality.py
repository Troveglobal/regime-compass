"""Monthly seasonality analysis — returns heatmap and stats from existing price data."""
from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

from .config import INDICES, raw_path


def monthly_returns(index_key: str) -> dict:
    path = raw_path(index_key)
    if not path.exists():
        return {"error": f"No data for {index_key}"}

    df = pd.read_parquet(path)
    prices = df["price"].dropna()
    prices.index = pd.to_datetime(prices.index)

    monthly = prices.resample("ME").last().dropna()
    rets = monthly.pct_change().dropna() * 100

    years = sorted(rets.index.year.unique())
    months = list(range(1, 13))

    heatmap = []
    for year in years:
        row = {"year": int(year)}
        for m in months:
            mask = (rets.index.year == year) & (rets.index.month == m)
            vals = rets[mask]
            row[calendar.month_abbr[m]] = round(float(vals.iloc[0]), 2) if len(vals) > 0 else None
        heatmap.append(row)

    month_stats = []
    for m in months:
        mask = rets.index.month == m
        vals = rets[mask]
        if len(vals) == 0:
            continue
        positive_pct = float((vals > 0).sum() / len(vals) * 100)
        month_stats.append({
            "month": calendar.month_abbr[m],
            "month_num": m,
            "mean": round(float(vals.mean()), 2),
            "median": round(float(vals.median()), 2),
            "std": round(float(vals.std()), 2),
            "min": round(float(vals.min()), 2),
            "max": round(float(vals.max()), 2),
            "positive_pct": round(positive_pct, 1),
            "count": int(len(vals)),
        })

    return {
        "index_key": index_key,
        "index_name": INDICES[index_key]["name"],
        "years": years,
        "months": [calendar.month_abbr[m] for m in months],
        "heatmap": heatmap,
        "month_stats": month_stats,
    }


def all_seasonality() -> list[dict]:
    results = []
    for key in INDICES:
        data = monthly_returns(key)
        if "error" not in data:
            results.append({
                "index_key": key,
                "index_name": data["index_name"],
                "month_stats": data["month_stats"],
            })
    return results
