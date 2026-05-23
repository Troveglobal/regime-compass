"""Risk-on / risk-off composite across all indices and all regime models.

For each index, we compute a "risk score" in [-100, +100]:
  HMM score:  (bull_prob - bear_prob) * 100
  SMA score:  +100 if price > 200-SMA, else -100
  EMA score:  +100 if price > 200-EMA, else -100

Per-index score = mean of the 3 sub-scores (any None contributions skipped).
Composite     = mean across all indices, then mapped to a 0-100 gauge.
   gauge = (composite + 100) / 2   so 0 = full bear, 50 = neutral, 100 = full bull.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd

from . import ma_regime
from .config import DB_PATH, INDICES


COMPOSITE_PERIOD = 200  # which SMA/EMA period we use for the composite (long-term trend)


def _hmm_score_from_probs(bear: float, neutral: float, bull: float) -> float:
    return (bull - bear) * 100


def _ma_score(regime: str) -> float:
    if regime == "bull":
        return 100.0
    if regime == "bear":
        return -100.0
    return 0.0


def composite_today() -> dict:
    """Today's composite score plus per-index, per-model breakdown."""
    # HMM probabilities (latest row per index from SQLite)
    conn = sqlite3.connect(DB_PATH)
    hmm_rows = {}
    for key in INDICES:
        cur = conn.execute(
            "SELECT date, bear, neutral, bull, hard_state, price_close "
            "FROM probabilities WHERE index_key = ? ORDER BY date DESC LIMIT 1",
            (key,),
        )
        row = cur.fetchone()
        if row:
            hmm_rows[key] = {
                "date": row[0], "bear": row[1], "neutral": row[2], "bull": row[3],
                "hard_state": row[4], "price_close": row[5],
            }
    conn.close()

    breakdown = []
    score_sum = 0.0
    score_n = 0
    for key, cfg in INDICES.items():
        hmm = hmm_rows.get(key)
        hmm_score = None
        if hmm:
            hmm_score = _hmm_score_from_probs(hmm["bear"], hmm["neutral"], hmm["bull"])

        sma_today = ma_regime.today(key, COMPOSITE_PERIOD, kind="sma")
        ema_today = ma_regime.today(key, COMPOSITE_PERIOD, kind="ema")
        sma_score = _ma_score(sma_today["regime"])
        ema_score = _ma_score(ema_today["regime"])

        sub_scores = [s for s in [hmm_score, sma_score, ema_score] if s is not None]
        idx_score = sum(sub_scores) / len(sub_scores) if sub_scores else 0.0

        breakdown.append({
            "index_key": key,
            "index_name": cfg["name"],
            "country": cfg["country"],
            "score": round(idx_score, 1),
            "hmm_score": round(hmm_score, 1) if hmm_score is not None else None,
            "sma_score": round(sma_score, 1),
            "ema_score": round(ema_score, 1),
            "sma_regime": sma_today["regime"],
            "ema_regime": ema_today["regime"],
            "hmm_state": hmm["hard_state"] if hmm else None,
            "price": hmm["price_close"] if hmm else sma_today.get("price"),
            "sma_gap_pct": sma_today.get("gap_pct"),
            "ema_gap_pct": ema_today.get("gap_pct"),
        })
        score_sum += idx_score
        score_n += 1

    composite_score = score_sum / score_n if score_n else 0.0
    gauge_value = (composite_score + 100.0) / 2.0  # map [-100, 100] -> [0, 100]

    # Classify the composite into a banner
    if gauge_value >= 70:
        regime_label = "risk-on"
        regime_color = "bull"
    elif gauge_value <= 30:
        regime_label = "risk-off"
        regime_color = "bear"
    else:
        regime_label = "mixed"
        regime_color = "neutral"

    return {
        "score": round(composite_score, 1),
        "gauge": round(gauge_value, 1),
        "regime_label": regime_label,
        "regime_color": regime_color,
        "composite_period": COMPOSITE_PERIOD,
        "n_indices": score_n,
        "breakdown": breakdown,
    }


def composite_history(days: int = 180) -> dict:
    """Composite over last N trading days, computed from historical regime data.

    For SMA/EMA contributions we recompute the regime series from raw prices.
    For HMM contributions we read from the SQLite probabilities table.
    Returns dates (using SPX trading calendar as the spine, which is densest)
    and the composite score per day.
    """
    # Build per-index regime series for the last `days`
    series_per_index: dict[str, pd.DataFrame] = {}

    conn = sqlite3.connect(DB_PATH)
    for key in INDICES:
        # HMM history
        hmm_df = pd.read_sql(
            "SELECT date, bear, neutral, bull FROM probabilities "
            "WHERE index_key = ? ORDER BY date DESC LIMIT ?",
            conn, params=(key, days + 50), parse_dates=["date"],
        )
        hmm_df = hmm_df.set_index("date").sort_index()

        # SMA + EMA regime history
        sma_df = ma_regime.compute_regime(key, COMPOSITE_PERIOD, kind="sma")
        ema_df = ma_regime.compute_regime(key, COMPOSITE_PERIOD, kind="ema")
        sma_df = sma_df.set_index("date").rename(columns={"regime": "sma_regime"})[["sma_regime"]]
        ema_df = ema_df.set_index("date").rename(columns={"regime": "ema_regime"})[["ema_regime"]]

        merged = hmm_df.join(sma_df, how="outer").join(ema_df, how="outer").sort_index()
        merged = merged.ffill()  # carry last known regime forward across non-trading-day gaps

        merged["hmm_score"] = (merged["bull"] - merged["bear"]) * 100
        merged["sma_score"] = merged["sma_regime"].map({"bull": 100.0, "bear": -100.0})
        merged["ema_score"] = merged["ema_regime"].map({"bull": 100.0, "bear": -100.0})

        score = merged[["hmm_score", "sma_score", "ema_score"]].mean(axis=1, skipna=True)
        series_per_index[key] = score.dropna().to_frame(name=key)
    conn.close()

    # Outer-join all per-index score series, then average across indices each day
    if not series_per_index:
        return {"dates": [], "composite": [], "gauge": []}
    combined = pd.concat(series_per_index.values(), axis=1).sort_index().ffill()
    # Require at least 4 of 6 indices to have data on a row
    combined = combined[combined.notna().sum(axis=1) >= 4]
    avg_score = combined.mean(axis=1, skipna=True)
    avg_gauge = (avg_score + 100.0) / 2.0

    tail = avg_score.tail(days)
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in tail.index],
        "composite": [round(float(v), 2) for v in tail.values],
        "gauge": [round(float(v), 2) for v in avg_gauge.loc[tail.index].values],
    }
