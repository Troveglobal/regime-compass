"""Build per-index feature matrix. Feature set depends on which tickers were available."""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .config import INDICES, VOL_WINDOW, features_path, raw_path

# All possible feature names, in canonical order. Models save which subset they use.
ALL_FEATURE_COLS = ["log_return", "realized_vol", "fx_change", "vix"]


def build_one(key: str) -> pd.DataFrame:
    raw = pd.read_parquet(raw_path(key))
    df = pd.DataFrame(index=raw.index)
    df["log_return"] = np.log(raw["price"] / raw["price"].shift(1))
    df["realized_vol"] = df["log_return"].rolling(VOL_WINDOW).std()
    if "fx" in raw.columns:
        # Yahoo FX series occasionally carry decimal-glitch ticks (e.g. TWD=X
        # printing 1.8 between 30.0 closes). Drop values >20% off the local
        # median and carry the last good value forward.
        fx = raw["fx"]
        med = fx.rolling(7, center=True, min_periods=3).median()
        fx = fx.where(((fx / med) - 1.0).abs() < 0.20).ffill()
        df["fx_change"] = np.log(fx / fx.shift(1))
    if "vix" in raw.columns:
        df["vix"] = raw["vix"]
    df = df.dropna()
    out = features_path(key)
    df.to_parquet(out)
    cols = list(df.columns)
    print(f"[features:{key}] wrote {len(df)} rows, features={cols}", flush=True)
    return df


def feature_cols_for(key: str) -> list[str]:
    """The feature columns that will be produced for this index (based on available tickers)."""
    cfg = INDICES[key]
    cols = ["log_return", "realized_vol"]
    if cfg["tickers"].get("fx"):
        cols.append("fx_change")
    if cfg["tickers"].get("vix"):
        cols.append("vix")
    return cols


def build_all() -> dict[str, pd.DataFrame]:
    out = {}
    for key in INDICES:
        out[key] = build_one(key)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for k in sys.argv[1:]:
            build_one(k)
    else:
        build_all()
