"""Pure statistical functions shared by the analytics feeds (Regime Movers,
Systemic Risk). No I/O here — everything is unit-testable in isolation
(tests/test_analytics.py).

NO LOOKAHEAD: every function that estimates parameters for day t uses only
data through t (or t-1 where noted). The per-function comments mark where
this is enforced.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

# Minimum observations of a regime state before its own return distribution
# is considered estimable; below this we fall back to full history.
MIN_STATE_OBS = 100


def regime_zscore(r_today: float, state_returns: np.ndarray,
                  all_returns: np.ndarray, min_obs: int = MIN_STATE_OBS) -> dict:
    """Z-score of today's return conditional on the market's current regime.

    z = (r_today − μ_s) / σ_s where μ_s, σ_s come from historical daily
    returns on days classified in the same state s. If the state has fewer
    than `min_obs` observations, falls back to the full-history distribution
    and sets fallback=True.

    NO LOOKAHEAD: callers must pass only returns strictly before today in
    both samples — today's return must not be part of its own baseline.
    """
    state_returns = np.asarray(state_returns, dtype=float)
    state_returns = state_returns[np.isfinite(state_returns)]
    all_returns = np.asarray(all_returns, dtype=float)
    all_returns = all_returns[np.isfinite(all_returns)]

    fallback = len(state_returns) < min_obs
    sample = all_returns if fallback else state_returns
    out = {"z": None, "percentile": None, "mu": None, "sigma": None,
           "fallback": fallback, "n_obs": int(len(sample))}
    if len(sample) < 2 or not np.isfinite(r_today):
        return out
    mu = float(sample.mean())
    sigma = float(sample.std(ddof=1))
    out["mu"] = mu
    out["sigma"] = sigma
    if sigma <= 0 or not np.isfinite(sigma):  # constant-return guard
        return out
    out["z"] = float((r_today - mu) / sigma)
    out["percentile"] = float((sample < r_today).mean() * 100)
    return out


def align_calendar(closes: dict[str, pd.Series], max_ffill: int = 1) -> pd.DataFrame:
    """Common trading calendar across markets.

    Rule (documented per spec): outer-join all close series on date,
    forward-fill each series across single-day holiday gaps only
    (limit=max_ffill), then keep only dates where EVERY market has a value
    (inner join). Longer gaps — multi-day holidays, weekends for equities —
    drop out of the common calendar entirely rather than being interpolated.
    """
    df = pd.concat(
        {k: s.dropna() for k, s in closes.items()}, axis=1, join="outer"
    ).sort_index()
    df = df.ffill(limit=max_ffill)
    return df.dropna(how="any")


def turbulence_series(returns: pd.DataFrame, window: int = 500) -> pd.Series:
    """Financial Turbulence (Kritzman & Li 2010): Mahalanobis distance of the
    day's cross-asset return vector from its recent multivariate history.

        d_t = (r_t − μ)ᵀ Σ⁻¹ (r_t − μ)

    μ and Σ are estimated on the trailing `window` days ENDING t−1 — day t
    is never part of its own baseline (this is the no-lookahead guarantee).
    Σ uses Ledoit-Wolf shrinkage; the inverse is a pseudo-inverse for
    numerical stability.
    """
    X = returns.to_numpy(dtype=float)
    n, _ = X.shape
    idx, vals = [], []
    for t in range(window, n):
        hist = X[t - window:t]           # rows t-window .. t-1: excludes day t
        if not np.isfinite(hist).all() or not np.isfinite(X[t]).all():
            continue
        lw = LedoitWolf().fit(hist)
        inv = np.linalg.pinv(lw.covariance_)
        diff = X[t] - hist.mean(axis=0)
        idx.append(returns.index[t])
        vals.append(float(diff @ inv @ diff))
    return pd.Series(vals, index=idx, name="turbulence")


def absorption_series(returns: pd.DataFrame, window: int = 250,
                      n_components: int = 2) -> pd.Series:
    """Absorption Ratio (Kritzman, Li, Page & Rigobon 2011): fraction of total
    variance explained by the top `n_components` eigenvectors of the trailing
    covariance matrix. n_components = 2 ≈ 1/5 of 11 assets, per the paper.

    The window ENDS at t (inclusive) — AR_t is a description of the market's
    structure through today, using no future data.
    """
    X = returns.to_numpy(dtype=float)
    n, _ = X.shape
    idx, vals = [], []
    for t in range(window - 1, n):
        hist = X[t - window + 1:t + 1]   # rows t-window+1 .. t: through today only
        if not np.isfinite(hist).all():
            continue
        cov = np.cov(hist, rowvar=False)
        eig = np.linalg.eigvalsh(cov)    # ascending
        total = float(eig.sum())
        if total <= 0:
            continue
        idx.append(returns.index[t])
        vals.append(float(eig[-n_components:].sum() / total))
    return pd.Series(vals, index=idx, name="absorption_ratio")


def delta_ar(ar: pd.Series, short: int = 15, long: int = 252) -> pd.Series:
    """Standardized AR shift: (mean AR last `short` days − mean AR last `long`
    days) / std of AR over last `long` days. All windows trail — no lookahead."""
    m_short = ar.rolling(short).mean()
    m_long = ar.rolling(long).mean()
    s_long = ar.rolling(long).std(ddof=1)
    return ((m_short - m_long) / s_long.replace(0, np.nan)).rename("delta_ar")


def trailing_percentile(series: pd.Series, value: float, window: int = 1260) -> float | None:
    """Percentile of `value` within the last `window` observations of `series`
    (~5 trading years at 252 days). Trailing only — no future data."""
    tail = series.dropna().tail(window)
    if len(tail) < 2 or not np.isfinite(value):
        return None
    return float((tail < value).mean() * 100)
