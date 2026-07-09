"""Cross-market analytics: regime concordance, correlations, volatility monitor.

All three read only local data (SQLite probabilities + raw parquet prices),
so they are cheap — but each carries a short in-memory TTL cache because the
correlation/vol paths load every index's parquet.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import numpy as np
import pandas as pd

from .config import DB_PATH, INDICES, raw_path

_CACHE_TTL_SEC = 1800
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, builder):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_SEC:
            return hit[1]
    value = builder()
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)
    return value


# ============================================================
# Regime concordance — "how many markets agree?"
# ============================================================

def _state_counts_frame() -> pd.DataFrame:
    """Daily bull/neutral/bear counts across all indices (ffilled per index)."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql(
        "SELECT date, index_key, hard_state FROM probabilities ORDER BY date",
        conn, parse_dates=["date"],
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    pivot = df.pivot_table(index="date", columns="index_key", values="hard_state", aggfunc="last")
    # breadth counts exclude inverse-risk markets (rates/FX), same as the dial
    keep = [k for k in pivot.columns if INDICES.get(k, {}).get("breadth", True)]
    pivot = pivot[keep]
    # Carry each market's last known regime across its non-trading days
    pivot = pivot.ffill()
    counts = pd.DataFrame({
        "bull": (pivot == "bull").sum(axis=1),
        "neutral": (pivot == "neutral").sum(axis=1),
        "bear": (pivot == "bear").sum(axis=1),
        "n": pivot.notna().sum(axis=1),
    })
    # Only rows where most markets have data (early history is sparse)
    return counts[counts["n"] >= max(4, counts["n"].max() - 2)]


def concordance() -> dict:
    return _cached("concordance", _build_concordance)


def _build_concordance() -> dict:
    conn = sqlite3.connect(DB_PATH)
    markets = []
    for key, cfg in INDICES.items():
        # rates/FX regimes are inverse risk signals (bond/dollar bull = risk-off
        # elsewhere) — they get full regime coverage but stay out of the breadth dial
        if not cfg.get("breadth", True):
            continue
        row = conn.execute(
            "SELECT date, hard_state, bear, neutral, bull FROM probabilities "
            "WHERE index_key = ? ORDER BY date DESC LIMIT 1", (key,),
        ).fetchone()
        if not row:
            continue
        markets.append({
            "index_key": key,
            "index_name": cfg["name"],
            "country": cfg["country"],
            "date": row[0],
            "state": row[1],
            "confidence": round(max(row[2], row[3], row[4]) * 100, 1),
        })
    conn.close()

    counts = {"bull": 0, "neutral": 0, "bear": 0}
    for m in markets:
        counts[m["state"]] += 1
    n = len(markets)
    # Breadth: -100 (all bear) .. +100 (all bull)
    breadth = round((counts["bull"] - counts["bear"]) / n * 100, 1) if n else 0.0

    hist = _state_counts_frame()
    history, last_seen = [], None
    if not hist.empty:
        tail = hist.tail(365)
        history = [
            {"date": d.strftime("%Y-%m-%d"), "bull": int(r["bull"]),
             "neutral": int(r["neutral"]), "bear": int(r["bear"])}
            for d, r in tail.iterrows()
        ]
        # Last time bull-count was at least today's level, before the current streak
        cur_bulls = counts["bull"]
        at_level = hist["bull"] >= cur_bulls
        # Walk back over the current contiguous streak, then find the previous occurrence
        idx = list(hist.index)
        i = len(idx) - 1
        while i >= 0 and at_level.iloc[i]:
            i -= 1
        streak_start = idx[i + 1] if i + 1 < len(idx) else idx[-1]
        prev = hist.iloc[:i + 1]
        prev_dates = prev.index[at_level.iloc[:i + 1]] if i >= 0 else []
        last_seen = {
            "bull_count": cur_bulls,
            "streak_since": streak_start.strftime("%Y-%m-%d"),
            "previous": prev_dates[-1].strftime("%Y-%m-%d") if len(prev_dates) else None,
        }

    if n and counts["bull"] >= 0.7 * n:
        label = "broad risk-on"
    elif n and counts["bear"] >= 0.7 * n:
        label = "broad risk-off"
    elif counts["bear"] > counts["bull"]:
        label = "tilting defensive"
    elif counts["bull"] > counts["bear"]:
        label = "tilting constructive"
    else:
        label = "split"

    return {
        "n_markets": n,
        "counts": counts,
        "breadth": breadth,
        "label": label,
        "markets": markets,
        "last_seen": last_seen,
        "history": history,
    }


# ============================================================
# Cross-asset correlation matrix
# ============================================================

CORR_WINDOWS = (30, 90, 365)


def correlations(window: int = 90) -> dict:
    if window not in CORR_WINDOWS:
        window = 90
    return _cached(f"corr:{window}", lambda: _build_correlations(window))


def _build_correlations(window: int) -> dict:
    rets = {}
    for key in INDICES:
        try:
            raw = pd.read_parquet(raw_path(key), columns=["price"])
        except FileNotFoundError:
            continue
        rets[key] = np.log(raw["price"] / raw["price"].shift(1)).rename(key)
    if len(rets) < 2:
        return {"window": window, "keys": [], "names": {}, "matrix": [], "avg_by_market": [], "avg_overall": None}

    df = pd.concat(rets.values(), axis=1)
    # Crypto trades weekends; equities don't. Restrict to dates where at least
    # half the markets traded so weekend crypto rows don't dilute the window.
    df = df[df.notna().sum(axis=1) >= len(df.columns) // 2]
    df = df.tail(window)
    corr = df.corr(min_periods=max(10, window // 3))

    keys = [k for k in INDICES if k in corr.columns]
    corr = corr.loc[keys, keys]
    matrix = [[None if pd.isna(corr.iat[i, j]) else round(float(corr.iat[i, j]), 2)
               for j in range(len(keys))] for i in range(len(keys))]

    # Average pairwise correlation per market (excluding self)
    avg_by_market = []
    off_diag = []
    for i, k in enumerate(keys):
        vals = [matrix[i][j] for j in range(len(keys)) if j != i and matrix[i][j] is not None]
        avg_by_market.append({
            "index_key": k,
            "index_name": INDICES[k]["name"],
            "avg_corr": round(float(np.mean(vals)), 2) if vals else None,
        })
        off_diag.extend(vals)
    avg_by_market.sort(key=lambda r: (r["avg_corr"] is None, r["avg_corr"]))

    start = df.index.min()
    end = df.index.max()
    return {
        "window": window,
        "windows": list(CORR_WINDOWS),
        "keys": keys,
        "names": {k: INDICES[k]["name"] for k in keys},
        "matrix": matrix,
        "avg_by_market": avg_by_market,
        "avg_overall": round(float(np.mean(off_diag)), 2) if off_diag else None,
        "period": {
            "start": start.strftime("%Y-%m-%d") if pd.notna(start) else None,
            "end": end.strftime("%Y-%m-%d") if pd.notna(end) else None,
        },
    }


# ============================================================
# Volatility monitor — realized vol percentile for every market
# ============================================================

VOL_WINDOW_DAYS = 20


def vol_monitor() -> dict:
    return _cached("vol", _build_vol_monitor)


def _vol_label(pctile: float) -> str:
    if pctile < 25:
        return "low"
    if pctile < 60:
        return "normal"
    if pctile < 85:
        return "elevated"
    return "extreme"


def _build_vol_monitor() -> dict:
    out = []
    for key, cfg in INDICES.items():
        try:
            raw = pd.read_parquet(raw_path(key))
        except FileNotFoundError:
            continue
        logret = np.log(raw["price"] / raw["price"].shift(1))
        vol = logret.rolling(VOL_WINDOW_DAYS).std() * np.sqrt(252) * 100
        vol = vol.dropna()
        if vol.empty:
            continue
        current = float(vol.iloc[-1])
        pctile = float((vol < current).mean() * 100)
        avg_1y = float(vol.tail(252).mean())

        entry = {
            "index_key": key,
            "index_name": cfg["name"],
            "country": cfg["country"],
            "date": vol.index[-1].strftime("%Y-%m-%d"),
            "realized_vol": round(current, 1),
            "avg_vol_1y": round(avg_1y, 1),
            "percentile": round(pctile, 1),
            "vol_regime": _vol_label(pctile),
            "spark": [round(float(v), 1) for v in vol.tail(120).tolist()],
        }
        # Implied vol where an index has a VIX-style series
        if "vix" in raw.columns and raw["vix"].notna().any():
            vix = raw["vix"].dropna()
            vix_cur = float(vix.iloc[-1])
            entry["implied"] = {
                "value": round(vix_cur, 2),
                "percentile": round(float((vix < vix_cur).mean() * 100), 1),
            }
        out.append(entry)

    out.sort(key=lambda r: -r["percentile"])
    return {"window_days": VOL_WINDOW_DAYS, "annualization": 252, "markets": out}
