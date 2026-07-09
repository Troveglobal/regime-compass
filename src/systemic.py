"""Systemic Risk feed: Financial Turbulence + Absorption Ratio across the
full cross-asset universe, precomputed daily into data/analytics/systemic.json.

References:
  Kritzman & Li (2010), "Skulls, Financial Turbulence, and Risk Management"
  Kritzman, Li, Page & Rigobon (2011), "Principal Components as a Measure
  of Systemic Risk"

Heavy computation happens here in the pipeline; the frontend only renders
the JSON. Failures are caught by the scheduler wrapper so they never block
the daily regime update.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .analytics import (
    absorption_series,
    align_business,
    align_calendar,
    delta_ar,
    trailing_percentile,
    turbulence_series,
)
from .config import DATA_DIR, INDICES, raw_path

OUT_PATH = DATA_DIR / "analytics" / "systemic.json"

TURB_WINDOW = 500          # trailing estimation window for μ, Σ (ends t-1)
AR_WINDOW = 250            # trailing covariance window for the eigendecomposition
AR_TOP_N = None            # resolved at build time: max(2, round(N/5)) per Kritzman
AR_SHORT, AR_LONG = 15, 252
SMOOTH_DAYS = 10
PCTILE_WINDOW = 1260       # ~5 trading years
HISTORY_YEARS = 10         # chart depth
MIN_HISTORY_DAYS = int(3 * 252)  # markets with < 3y of data are excluded


def _load_closes() -> tuple[dict[str, pd.Series], list[dict]]:
    """Full market board (config.INDICES). Extended from the original 11 to
    the whole cross-asset universe in Jul 2026 — a deliberate methodology
    change, noted on the page; AR components scale with Kritzman's 1/5 rule."""
    closes, excluded = {}, []
    for key, cfg in INDICES.items():
        try:
            raw = pd.read_parquet(raw_path(key), columns=["price"])
        except FileNotFoundError:
            excluded.append({"index_key": key, "name": cfg["name"], "reason": "no data file"})
            continue
        s = raw["price"].dropna()
        if len(s) < MIN_HISTORY_DAYS:
            excluded.append({"index_key": key, "name": cfg["name"],
                             "reason": f"under 3 years of history ({len(s)} days)"})
            continue
        closes[key] = s
    return closes, excluded


def _series_points(raw: pd.Series, smooth: pd.Series) -> list[list]:
    return [[d.strftime("%Y-%m-%d"),
             round(float(raw.loc[d]), 3),
             round(float(smooth.loc[d]), 3) if pd.notna(smooth.loc[d]) else None]
            for d in raw.index]


def build() -> dict:
    closes, excluded = _load_closes()
    if len(closes) < 3:
        raise RuntimeError(f"only {len(closes)} markets available — need at least 3")

    # Common calendar: inner-join on dates where all series have data,
    # forward-filling single-day holiday gaps only (rule in analytics.align_calendar).
    prices = align_business(closes, ffill_limit=3)
    rets = np.log(prices / prices.shift(1)).dropna(how="any")

    turb = turbulence_series(rets, window=TURB_WINDOW)
    turb_smooth = turb.rolling(SMOOTH_DAYS).mean()
    n_comp = max(2, round(rets.shape[1] / 5))  # Kritzman's ~1/5-of-assets rule
    ar = absorption_series(rets, window=AR_WINDOW, n_components=n_comp)
    dar = delta_ar(ar, short=AR_SHORT, long=AR_LONG)
    if turb.empty or ar.empty:
        raise RuntimeError("turbulence/absorption series came back empty")

    # Trailing 5y percentiles + reference thresholds (annotations for the chart)
    turb_tail = turb.tail(PCTILE_WINDOW)
    cur_turb = float(turb.iloc[-1])
    cur_ar = float(ar.iloc[-1])
    cur_dar = float(dar.iloc[-1]) if pd.notna(dar.iloc[-1]) else None

    cutoff = turb.index[-1] - pd.DateOffset(years=HISTORY_YEARS)

    def _weekly(s: pd.Series, sm: pd.Series) -> list[list]:
        s10 = s[s.index >= cutoff]
        w = s10.resample("W-FRI").last().dropna()
        wsm = sm.reindex(w.index)
        return _series_points(w, wsm)

    def _daily90(s: pd.Series, sm: pd.Series) -> list[list]:
        d = s.tail(90)
        return _series_points(d, sm.reindex(d.index))

    ar_smooth = ar.rolling(SMOOTH_DAYS).mean()

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": turb.index[-1].strftime("%Y-%m-%d"),
        "source": "Yahoo Finance daily closes",
        "markets_included": [{"index_key": k, "name": INDICES[k]["name"]} for k in prices.columns],
        "markets_excluded": excluded,
        "calendar": {
            "rule": ("inner join on dates where all markets have data; "
                     "single-day holiday gaps forward-filled (max 1 day)"),
            "start": rets.index[0].strftime("%Y-%m-%d"),
            "end": rets.index[-1].strftime("%Y-%m-%d"),
            "n_days": int(len(rets)),
        },
        "turbulence": {
            "window": TURB_WINDOW,
            "smooth_days": SMOOTH_DAYS,
            "current": round(cur_turb, 3),
            "current_smooth": round(float(turb_smooth.iloc[-1]), 3),
            "current_percentile": round(trailing_percentile(turb, cur_turb, PCTILE_WINDOW), 1),
            "p90": round(float(turb_tail.quantile(0.90)), 3),
            "p99": round(float(turb_tail.quantile(0.99)), 3),
            "weekly": _weekly(turb, turb_smooth),
            "daily_90d": _daily90(turb, turb_smooth),
        },
        "absorption": {
            "window": AR_WINDOW,
            "n_components": n_comp,
            "n_assets": int(len(prices.columns)),
            "current": round(cur_ar, 4),
            "current_percentile": round(trailing_percentile(ar, cur_ar, PCTILE_WINDOW), 1),
            "delta_ar": round(cur_dar, 2) if cur_dar is not None else None,
            "delta_windows": {"short": AR_SHORT, "long": AR_LONG},
            "weekly": _weekly(ar, ar_smooth),
            "daily_90d": _daily90(ar, ar_smooth),
        },
    }
    return out


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"[systemic] wrote {OUT_PATH} — turbulence {out['turbulence']['current']} "
          f"(p{out['turbulence']['current_percentile']}), AR {out['absorption']['current']}, "
          f"ΔAR {out['absorption']['delta_ar']}", flush=True)
    return out


if __name__ == "__main__":
    try:
        refresh()
    except Exception as e:
        print(f"[systemic] FAILED: {e}", file=sys.stderr, flush=True)
        raise
