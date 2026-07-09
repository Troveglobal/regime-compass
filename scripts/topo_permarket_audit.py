#!/usr/bin/env python3
"""Per-market TDA audit: does single-market topology beat that market's own
realized-volatility percentile?

Method: Takens time-delay embedding of one market's daily returns
(dim=4, tau=1) -> sliding 60-point cloud -> Rips H1 -> landscape L1 norm.
Incumbent: 20d realized vol. Same protocol as the joint audit:
trailing percentiles only, event study on the market's own worst drawdowns,
incremental forward-drawdown test controlling for vol.

Run: ./venv/bin/python scripts/topo_permarket_audit.py
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
warnings.filterwarnings("ignore")

from ripser import ripser  # noqa: E402
from src.config import raw_path, INDICES  # noqa: E402

MARKETS = ["spx", "nifty", "stoxx50", "nikkei"]
EMB_DIM = 4
TAU = 1
WINDOW = 60          # embedded points per cloud
VOL_WIN = 20
PCTL_WIN = 1260
FWD = 60


def landscape_l1(dgm):
    if dgm is None or len(dgm) == 0:
        return 0.0
    finite = dgm[np.isfinite(dgm[:, 1])]
    if len(finite) == 0:
        return 0.0
    return float(np.sum(((finite[:, 1] - finite[:, 0]) / 2.0) ** 2))


def takens(x: np.ndarray, dim: int, tau: int) -> np.ndarray:
    n = len(x) - (dim - 1) * tau
    return np.column_stack([x[i * tau:i * tau + n] for i in range(dim)])


def rolling_pctl(s: pd.Series, window: int = PCTL_WIN) -> pd.Series:
    return s.rolling(window, min_periods=252).apply(
        lambda w: (w[:-1] < w[-1]).mean() * 100 if len(w) > 1 else np.nan, raw=True)


def worst_episodes(level: pd.Series, k: int = 4) -> list[dict]:
    dd = level - level.cummax()
    eps, used = [], pd.Series(False, index=dd.index)
    for trough in dd.sort_values().index:
        if len(eps) >= k:
            break
        if used.loc[trough]:
            continue
        pk = level.loc[:trough].idxmax()
        span = (dd.index >= pk - pd.Timedelta(days=120)) & (dd.index <= trough + pd.Timedelta(days=120))
        if used[span].any():
            continue
        used[span] = True
        eps.append({"peak": pk, "trough": trough,
                    "dd": float((np.exp(dd.loc[trough]) - 1) * 100)})
    return sorted(eps, key=lambda e: e["peak"])


def audit(key: str) -> None:
    px = pd.read_parquet(raw_path(key), columns=["price"])["price"].dropna()
    r = np.log(px / px.shift(1)).dropna()
    x = (r / r.std()).values
    emb = takens(x, EMB_DIM, TAU)
    emb_idx = r.index[(EMB_DIM - 1) * TAU:]

    vals, idx = [], []
    for t in range(WINDOW, len(emb)):
        vals.append(landscape_l1(ripser(emb[t - WINDOW:t], maxdim=1)["dgms"][1]))
        idx.append(emb_idx[t - 1])
    tda = pd.Series(vals, index=pd.DatetimeIndex(idx)).rolling(10).mean()

    vol = r.rolling(VOL_WIN).std() * np.sqrt(252)
    df = pd.DataFrame({"tda": tda, "vol": vol}).dropna()
    p = pd.DataFrame({c: rolling_pctl(df[c]) for c in df}).dropna()

    level = np.log(px).reindex(p.index)
    fwd_dd = pd.Series(
        [float(level.iloc[i + 1:i + FWD + 1].min() - level.iloc[i]) if i + FWD + 1 <= len(level) else np.nan
         for i in range(len(level))], index=level.index) * 100
    j = p.join(fwd_dd.rename("fwd")).dropna()

    name = INDICES[key]["name"]
    rho = df["tda"].corr(df["vol"], method="spearman")
    print(f"\n════ {name} ({key}) — {len(j)} days ════")
    print(f"overlap with own 20d vol (Spearman): {rho:.2f}")

    eps = worst_episodes(np.log(px))
    print(f"{'episode':<26}{'depth':>7}{'tda@-60':>9}{'tda@-20':>9}{'vol@-60':>9}{'vol@-20':>9}")
    for e in eps:
        row = [f"{e['peak'].date()} → {e['trough'].date()}"[:25], f"{e['dd']:.0f}%"]
        for nm in ["tda", "vol"]:
            for lead in [60, 20]:
                loc = p.index.searchsorted(e["peak"]) - 1 - lead
                row.append(f"p{p[nm].iloc[loc]:.0f}" if 0 <= loc < len(p) else "–")
        print(f"{row[0]:<26}{row[1]:>7}{row[2]:>9}{row[3]:>9}{row[4]:>9}{row[5]:>9}")

    calm = j["vol"] < 70
    print(f"{'state':<34}{'avg fwd 60d dd':>15}")
    print(f"{'TDA hot >90 (all days)':<34}{j.loc[j.tda > 90, 'fwd'].mean():>14.2f}%")
    print(f"{'TDA calm <50 (all days)':<34}{j.loc[j.tda < 50, 'fwd'].mean():>14.2f}%")
    print(f"{'TDA hot >90 | vol calm <70':<34}{j.loc[(j.tda > 90) & calm, 'fwd'].mean():>14.2f}%")
    print(f"{'TDA calm <50 | vol calm <70':<34}{j.loc[(j.tda < 50) & calm, 'fwd'].mean():>14.2f}%")
    print(f"{'vol hot >90 (all days)':<34}{j.loc[j.vol > 90, 'fwd'].mean():>14.2f}%")


if __name__ == "__main__":
    for k in MARKETS:
        audit(k)
