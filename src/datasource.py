"""Pluggable data source. Swap yfinance for a paid feed by adding a new class and flipping config.DATA_SOURCE."""
from __future__ import annotations

from typing import Protocol

import pandas as pd
import yfinance as yf


class DataSource(Protocol):
    def fetch(self, symbols: dict[str, str], start: str) -> pd.DataFrame:
        """Return a DataFrame indexed by date with one column per symbol key."""
        ...


class YFinanceSource:
    """Free, unofficial. Fine for personal/dev use. Not for commercial public hosting."""

    name = "yfinance"

    def fetch(self, symbols: dict[str, str], start: str) -> pd.DataFrame:
        series = {}
        for key, ticker in symbols.items():
            df = yf.download(
                ticker, start=start, progress=False, auto_adjust=False, threads=False
            )
            if df.empty:
                raise RuntimeError(f"yfinance returned empty frame for {ticker}")
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            series[key] = df["Close"].rename(key)
        out = pd.concat(series.values(), axis=1, sort=True)
        out.columns = list(series.keys())
        out.index = pd.to_datetime(out.index).tz_localize(None)
        return out.sort_index()


_SOURCES = {"yfinance": YFinanceSource}


def get_source(name: str) -> DataSource:
    if name not in _SOURCES:
        raise ValueError(f"Unknown data source '{name}'. Available: {list(_SOURCES)}")
    return _SOURCES[name]()
