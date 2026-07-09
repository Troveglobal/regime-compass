"""Market Geometry feed: TDA persistence-landscape norm + correlation-MST,
precomputed daily into data/analytics/topology.json.

Two topology-family measures over the non-crypto cross-asset panel:

  TDA landscape norm (Gidea & Katz 2018, "Topological data analysis of
  financial time series: Landscapes of crashes"): sliding window of the
  daily return point cloud -> Vietoris-Rips persistence (H1 loops) ->
  L1 norm of the persistence landscape. Rising norm = the geometry of
  market dynamics is developing persistent structure — an early-warning
  pattern that led the 2015-16 and 2021-22 drawdowns in our own audit.

  Correlation MST (Mantegna 1999; Onnela et al. 2003): rolling correlation
  matrix -> distance d = sqrt(2(1-rho)) -> minimum spanning tree. The
  tree's normalized length CONTRACTS when markets unify. Shipped as
  structure/visualisation; our audit found its statistical signal largely
  overlaps Financial Turbulence, and the page says so.

Crypto (BTC/ETH) is excluded: 24/7 calendars distort the joint geometry
and the literature panel is equities/commodities.

Heavy computation happens here in the pipeline; the frontend only renders
the JSON. Failures are caught by the scheduler wrapper so they never block
the daily regime update.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.sparse.csgraph import minimum_spanning_tree

from .analytics import align_calendar, trailing_percentile
from .config import DATA_DIR, INDICES, raw_path

OUT_PATH = DATA_DIR / "analytics" / "topology.json"

# Pinned universe: the 9 markets the published audit was run on. New board
# markets don't silently change audited history.
UNIVERSE = ("spx", "nasdaq", "gold", "silver", "stoxx50",
            "nifty", "nikkei", "kospi", "shcomp")
EXCLUDE = {"btc", "eth"}       # 24/7 markets distort the joint calendar/geometry
TDA_WINDOW = 60                # days per point cloud (Gidea-Katz use 50-100)
MST_WINDOW = 90                # rolling correlation window
PCTILE_WINDOW = 1260           # ~5 trading years
SMOOTH_DAYS = 10
HISTORY_YEARS = 16             # chart depth — show the full audited record
MIN_HISTORY_DAYS = int(3 * 252)

# Drawdown episodes from the validation study (scripts/topo_prototype.py),
# shaded on the history chart. Regenerate the study before editing.
EPISODES = [
    {"from": "2015-05-25", "to": "2016-01-21", "label": "2015–16 global"},
    {"from": "2018-01-26", "to": "2018-12-24", "label": "2018 Q4"},
    {"from": "2020-02-19", "to": "2020-03-23", "label": "COVID"},
    {"from": "2021-11-15", "to": "2022-09-30", "label": "2022"},
    {"from": "2025-02-18", "to": "2025-04-07", "label": "2025 Feb"},
]


def _load_returns() -> tuple[pd.DataFrame, list[str]]:
    closes, excluded = {}, []
    for key in UNIVERSE:
        cfg = INDICES[key]
        try:
            raw = pd.read_parquet(raw_path(key), columns=["price"])
        except FileNotFoundError:
            excluded.append(key)
            continue
        s = raw["price"].dropna()
        if len(s) < MIN_HISTORY_DAYS:
            excluded.append(key)
            continue
        closes[key] = s
    prices = align_calendar(closes, max_ffill=1)
    rets = np.log(prices / prices.shift(1)).dropna(how="any")
    return rets, excluded


def _landscape_l1(dgm: np.ndarray) -> float:
    """L1-type norm of the persistence landscape of an H1 diagram: sum of
    triangle areas ((death-birth)/2)^2 over all finite features."""
    if dgm is None or len(dgm) == 0:
        return 0.0
    finite = dgm[np.isfinite(dgm[:, 1])]
    if len(finite) == 0:
        return 0.0
    half_life = (finite[:, 1] - finite[:, 0]) / 2.0
    return float(np.sum(half_life ** 2))


def tda_series(rets: pd.DataFrame, window: int = TDA_WINDOW) -> pd.Series:
    """Daily L1 landscape norm of H1 persistence over the trailing window's
    return point cloud (points = days, dims = markets). Markets are scaled
    once by full-sample std so no single asset dominates; per-window vol
    structure is preserved — it IS the Gidea-Katz signal."""
    from ripser import ripser  # deferred: heavy import, pipeline-only

    X = rets.values
    Xs = X / X.std(axis=0, keepdims=True)
    vals, idx = [], []
    for t in range(window, len(rets)):
        dgms = ripser(Xs[t - window:t], maxdim=1)["dgms"]
        vals.append(_landscape_l1(dgms[1]))
        idx.append(rets.index[t - 1])
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name="tda")


def _mst_edges(corr: np.ndarray) -> list[tuple[int, int, float]]:
    """MST of the Mantegna distance matrix; returns (i, j, distance) edges."""
    D = np.sqrt(np.clip(2.0 * (1.0 - corr), 0.0, 4.0))
    # scipy MST treats 0 as "no edge" — distances here are strictly > 0
    # off-diagonal for any corr < 1, and the diagonal is excluded below.
    tree = minimum_spanning_tree(np.triu(D, k=1)).tocoo()
    return [(int(i), int(j), float(v)) for i, j, v in zip(tree.row, tree.col, tree.data)]


def mst_series(rets: pd.DataFrame, window: int = MST_WINDOW) -> pd.Series:
    """Rolling normalized tree length (mean MST edge distance). Contracts
    toward 0 as markets unify."""
    X = rets.values
    vals, idx = [], []
    for t in range(window, len(rets)):
        C = np.corrcoef(X[t - window:t].T)
        edges = _mst_edges(C)
        vals.append(float(np.mean([d for _, _, d in edges])))
        idx.append(rets.index[t - 1])
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name="mst_len")


def _series_points(s: pd.Series) -> list[list]:
    return [[d.strftime("%Y-%m-%d"), round(float(v), 5)] for d, v in s.items()]


def build() -> dict:
    rets, excluded = _load_returns()
    if rets.shape[1] < 5:
        raise RuntimeError(f"only {rets.shape[1]} markets available — need at least 5")

    tda = tda_series(rets)
    tda_smooth = tda.rolling(SMOOTH_DAYS).mean()
    mst_len = mst_series(rets)

    # current MST structure for the network visual
    C = np.corrcoef(rets.values[-MST_WINDOW:].T)
    edges = _mst_edges(C)
    keys = list(rets.columns)
    degrees = np.zeros(len(keys), dtype=int)
    for i, j, _ in edges:
        degrees[i] += 1
        degrees[j] += 1

    cutoff = tda.index[-1] - pd.DateOffset(years=HISTORY_YEARS)
    tda_hist = tda_smooth.dropna().loc[cutoff:]
    mst_hist = mst_len.rolling(SMOOTH_DAYS).mean().dropna().loc[cutoff:]

    cur_tda = float(tda_smooth.dropna().iloc[-1])
    cur_mst = float(mst_len.iloc[-1])
    tda_pct = trailing_percentile(tda_smooth.dropna(), cur_tda, window=PCTILE_WINDOW)
    # tightness percentile: how CONTRACTED is the tree vs its own history
    mst_tight_pct = trailing_percentile(-mst_len.dropna(), -cur_mst, window=PCTILE_WINDOW)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": tda.index[-1].strftime("%Y-%m-%d"),
        "params": {"tda_window": TDA_WINDOW, "mst_window": MST_WINDOW,
                   "smooth_days": SMOOTH_DAYS, "pctile_window": PCTILE_WINDOW},
        "markets": [{"key": k, "name": INDICES[k]["name"]} for k in keys],
        "excluded": sorted(EXCLUDE | set(excluded)),
        "tda": {
            "current": round(cur_tda, 5),
            "current_percentile": round(tda_pct, 1) if tda_pct is not None else None,
            "history": _series_points(tda_hist),
        },
        "mst": {
            "current_len": round(cur_mst, 5),
            "tightness_percentile": round(mst_tight_pct, 1) if mst_tight_pct is not None else None,
            "history": _series_points(mst_hist),
            "tree": {
                "nodes": [{"key": k, "name": INDICES[k]["name"], "degree": int(d)}
                          for k, d in zip(keys, degrees)],
                "edges": [{"a": keys[i], "b": keys[j], "d": round(d, 4),
                           "rho": round(float(1.0 - d * d / 2.0), 3)}
                          for i, j, d in edges],
            },
        },
        "episodes": EPISODES,
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"[topology] wrote {OUT_PATH} — TDA {out['tda']['current']} "
          f"(p{out['tda']['current_percentile']}), MST tightness "
          f"p{out['mst']['tightness_percentile']}", flush=True)
    return out


if __name__ == "__main__":
    refresh()
