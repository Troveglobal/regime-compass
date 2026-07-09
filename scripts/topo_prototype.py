#!/usr/bin/env python3
"""Prototype + validation study for two topology-family risk indicators.

1. TDA persistence-landscape norm (Gidea & Katz 2018):
   sliding window of the 11-market daily return cloud -> Vietoris-Rips
   persistence (H1 loops) -> L1 norm of the persistence landscape.
   Rising norm = the geometry of market dynamics is developing persistent
   structure, an early-warning pattern documented before 2000/2008.

2. MST topology (Mantegna 1999; Onnela et al. 2003):
   rolling correlation matrix -> distance d = sqrt(2(1-rho)) -> minimum
   spanning tree. Normalized tree length CONTRACTS in stress; degree
   concentration (star-ness) rises as the tree collapses toward a hub.

Validation protocol (audited, not asserted):
  - Spearman overlap with the site's existing gauges (turbulence, absorption)
  - Event study around the worst drawdown episodes in the panel
  - Incremental signal: future drawdowns conditioned on each indicator
    AFTER controlling for turbulence

Run:  ./venv/bin/python scripts/topo_prototype.py
Outputs: data/analytics/topo_proto.json (+ printed study)
"""
from __future__ import annotations

import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
warnings.filterwarnings("ignore")

from ripser import ripser  # noqa: E402
import networkx as nx  # noqa: E402

from src.systemic import _load_closes  # noqa: E402
from src.analytics import align_calendar, turbulence_series, absorption_series  # noqa: E402

# ── parameters (kept close to the literature) ──────────────────────
TDA_WINDOW = 60        # days per point cloud (Gidea-Katz use 50-100)
MST_WINDOW = 90        # rolling correlation window
PCTL_WINDOW = 1260     # ~5y trailing percentile
STEP = 1               # compute daily

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "data", "analytics", "topo_proto.json")


def landscape_l1(dgm: np.ndarray) -> float:
    """L1 norm of the first persistence landscape of an H1 diagram.
    For triangle functions peaking at (b+d)/2 with height (d-b)/2, the
    area under each is ((d-b)/2)^2 — summing areas gives an L1-type norm
    over all landscape levels (equivalent to persim's landscape integral)."""
    if dgm is None or len(dgm) == 0:
        return 0.0
    finite = dgm[np.isfinite(dgm[:, 1])]
    if len(finite) == 0:
        return 0.0
    half_life = (finite[:, 1] - finite[:, 0]) / 2.0
    return float(np.sum(half_life ** 2))


def tda_norm_series(rets: pd.DataFrame, window: int = TDA_WINDOW) -> pd.Series:
    """Daily L1 landscape norm of the H1 persistence of the trailing
    `window`-day return point cloud (points = days, dims = markets).
    Returns are z-scored per window so no single asset's vol scale
    dominates the geometry."""
    vals, idx = [], []
    X = rets.values
    n = len(rets)
    Xs = X / X.std(axis=0, keepdims=True)  # equalize market scales once;
    # per-window vol structure is preserved — it IS the Gidea-Katz signal
    for t in range(window, n, STEP):
        W = Xs[t - window:t]
        dgms = ripser(W, maxdim=1)["dgms"]
        vals.append(landscape_l1(dgms[1]))
        idx.append(rets.index[t - 1])
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name="tda_l1")


def mst_series(rets: pd.DataFrame, window: int = MST_WINDOW) -> pd.DataFrame:
    """Rolling MST metrics: normalized tree length (mean edge distance)
    and star-ness (max node degree / (N-1))."""
    lengths, stars, idx = [], [], []
    n = len(rets)
    cols = rets.columns
    N = len(cols)
    for t in range(window, n, STEP):
        C = np.corrcoef(rets.values[t - window:t].T)
        D = np.sqrt(np.clip(2.0 * (1.0 - C), 0.0, 4.0))
        G = nx.Graph()
        for i in range(N):
            for j in range(i + 1, N):
                G.add_edge(cols[i], cols[j], weight=float(D[i, j]))
        T = nx.minimum_spanning_tree(G)
        edges = [d["weight"] for _, _, d in T.edges(data=True)]
        lengths.append(float(np.mean(edges)))
        stars.append(max(dict(T.degree()).values()) / (N - 1))
        idx.append(rets.index[t - 1])
    return pd.DataFrame({"mst_len": lengths, "mst_star": stars},
                        index=pd.DatetimeIndex(idx))


def rolling_pctl(s: pd.Series, window: int = PCTL_WINDOW) -> pd.Series:
    """Trailing percentile of each value within its own past `window` obs."""
    return s.rolling(window, min_periods=252).apply(
        lambda w: (w[:-1] < w[-1]).mean() * 100 if len(w) > 1 else np.nan, raw=True)


def worst_episodes(prices: pd.DataFrame, k: int = 6) -> list[dict]:
    """K worst non-overlapping drawdown episodes of the equal-weight panel."""
    eq = np.log(prices / prices.shift(1)).dropna(how="any").mean(axis=1)
    level = eq.cumsum()
    peak = level.cummax()
    dd = level - peak
    episodes, used = [], pd.Series(False, index=dd.index)
    order = dd.sort_values().index
    for trough in order:
        if len(episodes) >= k:
            break
        if used.loc[trough]:
            continue
        pk = level.loc[:trough].idxmax()
        span = (dd.index >= pk - pd.Timedelta(days=120)) & (dd.index <= trough + pd.Timedelta(days=120))
        if used[span].any():
            continue
        used[span] = True
        episodes.append({"peak": pk, "trough": trough,
                         "dd_pct": float((np.exp(dd.loc[trough]) - 1) * 100)})
    return sorted(episodes, key=lambda e: e["peak"])


EXCLUDE = {"btc", "eth"}  # 24/7 markets distort the joint calendar & geometry


def main() -> None:
    closes, excluded = _load_closes()
    closes = {k: v for k, v in closes.items() if k not in EXCLUDE}
    prices = align_calendar(closes, max_ffill=1)
    rets = np.log(prices / prices.shift(1)).dropna(how="any")
    print(f"panel: {rets.shape[1]} markets x {len(rets)} days "
          f"({rets.index[0].date()} → {rets.index[-1].date()})")
    if excluded:
        print("excluded:", [e["index_key"] for e in excluded])

    print("computing TDA landscape norms…")
    tda = tda_norm_series(rets)
    print("computing MST metrics…")
    mst = mst_series(rets)
    print("computing incumbent gauges…")
    turb = turbulence_series(rets, window=500).rolling(10).mean()
    ar = absorption_series(rets, window=250, n_components=2)

    df = pd.DataFrame({
        "tda": tda, "mst_len": mst["mst_len"], "mst_star": mst["mst_star"],
        "turb": turb, "ar": ar,
    }).dropna()
    # sign convention: higher = more fragile (MST length CONTRACTS in stress)
    df["mst_tight"] = -df["mst_len"]

    # trailing percentiles for everything (no look-ahead)
    p = pd.DataFrame({c: rolling_pctl(df[c]) for c in
                      ["tda", "mst_tight", "mst_star", "turb", "ar"]}).dropna()

    print("\n== 1) Overlap with incumbents (Spearman, levels) ==")
    corr = df[["tda", "mst_tight", "mst_star", "turb", "ar"]].corr(method="spearman")
    print(corr.round(2).to_string())

    print("\n== 2) Event study: indicator percentile N days BEFORE the peak ==")
    eps = worst_episodes(prices)
    rows = []
    for e in eps:
        row = {"episode": f"{e['peak'].date()} → {e['trough'].date()}",
               "dd": f"{e['dd_pct']:.0f}%"}
        for name in ["tda", "mst_tight", "turb", "ar"]:
            for lead in [60, 20, 0]:
                loc = p.index.searchsorted(e["peak"]) - 1 - lead
                row[f"{name}@-{lead}"] = round(float(p[name].iloc[loc]), 0) if 0 <= loc < len(p) else None
        rows.append(row)
    ev = pd.DataFrame(rows).set_index("episode")
    print(ev.to_string())

    print("\n== 3) Incremental signal: future 60d panel drawdown by indicator state ==")
    eq = rets.mean(axis=1)
    lvl = eq.cumsum()
    fwd_dd = pd.Series(
        [float(lvl.iloc[i + 1:i + 61].min() - lvl.iloc[i]) if i + 61 <= len(lvl) else np.nan
         for i in range(len(lvl))], index=lvl.index) * 100
    j = p.join(fwd_dd.rename("fwd_dd")).dropna()
    calm_turb = j["turb"] < 70  # incremental test: only days incumbents call calm
    print(f"{'indicator':<10} {'hot>90 (all)':>14} {'calm<50 (all)':>14} "
          f"{'hot>90 | turb<70':>18} {'calm<50 | turb<70':>18}   (avg fwd 60d dd, %)")
    for name in ["tda", "mst_tight", "mst_star", "turb", "ar"]:
        hot = j.loc[j[name] > 90, "fwd_dd"].mean()
        calm = j.loc[j[name] < 50, "fwd_dd"].mean()
        hot_c = j.loc[(j[name] > 90) & calm_turb, "fwd_dd"].mean()
        calm_c = j.loc[(j[name] < 50) & calm_turb, "fwd_dd"].mean()
        print(f"{name:<10} {hot:>14.2f} {calm:>14.2f} {hot_c:>18.2f} {calm_c:>18.2f}")

    # persist series for charting / integration
    out = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "params": {"tda_window": TDA_WINDOW, "mst_window": MST_WINDOW},
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "series": {c: [round(float(v), 5) for v in df[c]] for c in
                   ["tda", "mst_len", "mst_star", "turb", "ar"]},
        "episodes": [{"peak": str(e["peak"].date()), "trough": str(e["trough"].date()),
                      "dd_pct": round(e["dd_pct"], 1)} for e in eps],
    }
    with open(OUT, "w") as f:
        json.dump(out, f)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
