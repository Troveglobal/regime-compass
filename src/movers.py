"""Regime Movers feed: markets ranked by how abnormal today's move is
relative to their CURRENT regime — not raw % change. Precomputed daily into
data/analytics/movers.json.

Prices and regime states both come from the SQLite probabilities table so
they are guaranteed aligned (same dates, same closes the HMM saw). States
are the filtered (causal) decode — see src/inference.py.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .analytics import MIN_STATE_OBS, regime_zscore, trailing_percentile
from .config import DATA_DIR, DB_PATH, INDICES

OUT_PATH = DATA_DIR / "analytics" / "movers.json"

VOL_WINDOW = 20            # 20-day realized vol, annualized
VOL_PCTILE_WINDOW = 1260   # ~5 trading years


def _market_frame(conn: sqlite3.Connection, key: str) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT date, hard_state, price_close FROM probabilities "
        "WHERE index_key = ? ORDER BY date", conn, params=(key,), parse_dates=["date"],
    ).set_index("date")
    return df[df["price_close"] > 0]


def _one_market(df: pd.DataFrame, key: str) -> dict | None:
    if len(df) < VOL_WINDOW + 2:
        return None
    ret = np.log(df["price_close"] / df["price_close"].shift(1))
    r_today = float(ret.iloc[-1])
    state = str(df["hard_state"].iloc[-1])

    # NO LOOKAHEAD / no self-reference: today's return is excluded from the
    # baseline sample it is scored against.
    hist_ret = ret.iloc[:-1]
    hist_state = df["hard_state"].iloc[:-1]
    stats = regime_zscore(
        r_today,
        state_returns=hist_ret[hist_state == state].to_numpy(),
        all_returns=hist_ret.to_numpy(),
        min_obs=MIN_STATE_OBS,
    )

    # 20d realized vol (annualized, %) and its percentile vs the market's
    # own trailing ~5-year vol history (trailing only — no future data).
    vol = ret.rolling(VOL_WINDOW).std(ddof=1) * np.sqrt(252) * 100
    vol = vol.dropna()
    vol_now = float(vol.iloc[-1]) if len(vol) else None
    vol_pct = trailing_percentile(vol, vol_now, VOL_PCTILE_WINDOW) if vol_now is not None else None

    cfg = INDICES[key]
    return {
        "index_key": key,
        "name": cfg["name"],
        "country": cfg["country"],
        "ticker": cfg["tickers"]["price"],
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "price": round(float(df["price_close"].iloc[-1]), 2),
        "pct_1d": round((np.exp(r_today) - 1) * 100, 2),
        "regime": state,
        "z": round(stats["z"], 2) if stats["z"] is not None else None,
        "percentile": round(stats["percentile"], 1) if stats["percentile"] is not None else None,
        "vol_20d": round(vol_now, 1) if vol_now is not None else None,
        "vol_percentile": round(vol_pct, 1) if vol_pct is not None else None,
        "fallback": bool(stats["fallback"]),
        "n_obs": stats["n_obs"],
    }


def build() -> dict:
    conn = sqlite3.connect(DB_PATH)
    rows = []
    try:
        for key in INDICES:
            try:
                df = _market_frame(conn, key)
                if df.empty:
                    continue
                entry = _one_market(df, key)
                if entry:
                    rows.append(entry)
            except Exception as e:  # one bad market must not sink the board
                print(f"[movers:{key}] skipped: {e}", file=sys.stderr, flush=True)
    finally:
        conn.close()
    if not rows:
        raise RuntimeError("no markets produced a movers entry")
    rows.sort(key=lambda r: -(abs(r["z"]) if r["z"] is not None else -1))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": max(r["date"] for r in rows),
        "source": "Yahoo Finance daily closes · HMM filtered states",
        "min_state_obs": MIN_STATE_OBS,
        "vol_window": VOL_WINDOW,
        "markets": rows,
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    top = out["markets"][0]
    print(f"[movers] wrote {OUT_PATH} — top: {top['name']} z={top['z']}", flush=True)
    return out


if __name__ == "__main__":
    refresh()
