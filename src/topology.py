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

from .analytics import align_business, trailing_percentile
from .config import DATA_DIR, INDICES, raw_path

OUT_PATH = DATA_DIR / "analytics" / "topology.json"

EXCLUDE = {"btc", "eth"}       # 24/7 markets distort the joint calendar/geometry
# Asset-class groups. Gauges and trees are computed PER GROUP: mixing rates/FX
# into one cloud muddies the risk read (a treasury rally during an equity
# selloff is flight-to-quality, not "structure"). Equities is the headline
# panel; cross-asset (all 18, the re-audited panel) remains available.
GROUPS = {
    "equities": ("spx", "nasdaq", "ftse", "stoxx50", "nifty", "nikkei",
                 "kospi", "shcomp", "hangseng", "taiex", "bovespa", "tadawul"),
    "commodities": ("gold", "silver", "wti", "copper"),
    "cross": ("spx", "nasdaq", "ftse", "stoxx50", "nifty", "nikkei",
              "kospi", "shcomp", "hangseng", "taiex", "bovespa", "tadawul",
              "gold", "silver", "wti", "copper", "us10y", "dxy"),
}
TDA_WINDOW = 60                # days per point cloud (Gidea-Katz use 50-100)
MST_WINDOW = 90                # rolling correlation window
PCTILE_WINDOW = 1260           # ~5 trading years
SMOOTH_DAYS = 10
HISTORY_YEARS = 16             # chart depth — show the full audited record
MIN_HISTORY_DAYS = int(3 * 252)

# Drawdown episodes from the validation study (scripts/topo_prototype.py),
# shaded on the history chart. Regenerate the study before editing.
EPISODES = [
    {"from": "2011-04-29", "to": "2011-10-03", "label": "2011 eurozone"},
    {"from": "2015-05-25", "to": "2016-01-20", "label": "2015–16 global"},
    {"from": "2018-01-26", "to": "2018-12-23", "label": "2018"},
    {"from": "2020-01-17", "to": "2020-03-23", "label": "COVID"},
    {"from": "2022-03-30", "to": "2022-09-30", "label": "2022"},
]


def _load_returns(keys) -> tuple[pd.DataFrame, list[str]]:
    closes, excluded = {}, []
    for key in keys:
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
    prices = align_business(closes, ffill_limit=3)
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
    for t in range(window, len(rets) + 1):
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


def mst_series(rets: pd.DataFrame, window: int = MST_WINDOW) -> pd.DataFrame:
    """Rolling MST metrics. Tree length (mean edge distance) contracts toward
    0 as markets unify; star-ness (max node degree / (N-1)) rises as the tree
    collapses toward a single hub — the configuration that preceded the worst
    forward drawdowns in the 18-market re-audit."""
    X = rets.values
    N = rets.shape[1]
    lens, stars, idx = [], [], []
    for t in range(window, len(rets) + 1):
        C = np.corrcoef(X[t - window:t].T)
        edges = _mst_edges(C)
        lens.append(float(np.mean([d for _, _, d in edges])))
        deg = np.zeros(N, dtype=int)
        for i, j, _ in edges:
            deg[i] += 1
            deg[j] += 1
        stars.append(float(deg.max() / (N - 1)))
        idx.append(rets.index[t - 1])
    return pd.DataFrame({"mst_len": lens, "mst_star": stars},
                        index=pd.DatetimeIndex(idx))


def _series_points(s: pd.Series) -> list[list]:
    return [[d.strftime("%Y-%m-%d"), round(float(v), 5)] for d, v in s.items()]


def _build_group(keys) -> dict:
    rets, excluded = _load_returns(keys)
    if rets.shape[1] < 3:
        raise RuntimeError(f"only {rets.shape[1]} markets available — need at least 3")

    tda = tda_series(rets)
    tda_smooth = tda.rolling(SMOOTH_DAYS).mean()
    mst = mst_series(rets)
    mst_len = mst["mst_len"]
    mst_star = mst["mst_star"]

    # current MST structure for the network visual
    C = np.corrcoef(rets.values[-MST_WINDOW:].T)
    edges = _mst_edges(C)
    cols = list(rets.columns)
    degrees = np.zeros(len(cols), dtype=int)
    for i, j, _ in edges:
        degrees[i] += 1
        degrees[j] += 1

    cutoff = tda.index[-1] - pd.DateOffset(years=HISTORY_YEARS)
    tda_hist = tda_smooth.dropna().loc[cutoff:]
    mst_hist = mst_len.rolling(SMOOTH_DAYS).mean().dropna().loc[cutoff:]
    star_smooth = mst_star.rolling(SMOOTH_DAYS).mean()

    cur_tda = float(tda_smooth.dropna().iloc[-1])
    cur_mst = float(mst_len.iloc[-1])
    cur_star = float(star_smooth.dropna().iloc[-1])
    tda_pct = trailing_percentile(tda_smooth.dropna(), cur_tda, window=PCTILE_WINDOW)
    mst_tight_pct = trailing_percentile(-mst_len.dropna(), -cur_mst, window=PCTILE_WINDOW)
    star_pct = trailing_percentile(star_smooth.dropna(), cur_star, window=PCTILE_WINDOW)

    def _cls(k):
        c = INDICES[k]["country"]
        return {"Commodity": "commodity", "Crypto": "crypto",
                "Rates": "rates", "FX": "fx"}.get(c, "equity")

    return {
        "as_of": tda.index[-1].strftime("%Y-%m-%d"),
        "markets": [{"key": k, "name": INDICES[k]["name"]} for k in cols],
        "tda": {
            "current": round(cur_tda, 5),
            "current_percentile": round(tda_pct, 1) if tda_pct is not None else None,
            "history": _series_points(tda_hist),
        },
        "mst": {
            "current_len": round(cur_mst, 5),
            "tightness_percentile": round(mst_tight_pct, 1) if mst_tight_pct is not None else None,
            "star_percentile": round(star_pct, 1) if star_pct is not None else None,
            "history": _series_points(mst_hist),
            "tree": {
                "nodes": [{"key": k, "name": INDICES[k]["name"], "degree": int(d),
                           "cls": _cls(k)} for k, d in zip(cols, degrees)],
                "edges": [{"a": cols[i], "b": cols[j], "d": round(d, 4),
                           "rho": round(float(1.0 - d * d / 2.0), 3)}
                          for i, j, d in edges],
            },
        },
    }


def build() -> dict:
    groups = {name: _build_group(keys) for name, keys in GROUPS.items()}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": groups["equities"]["as_of"],
        "params": {"tda_window": TDA_WINDOW, "mst_window": MST_WINDOW,
                   "smooth_days": SMOOTH_DAYS, "pctile_window": PCTILE_WINDOW},
        "excluded": sorted(EXCLUDE),
        "groups": groups,
        # legacy top-level fields point at the cross-asset (audited) panel
        "markets": groups["cross"]["markets"],
        "tda": groups["cross"]["tda"],
        "mst": groups["cross"]["mst"],
        "episodes": EPISODES,
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    eq = out["groups"]["equities"]
    print(f"[topology] wrote {OUT_PATH} — equities TDA p{eq['tda']['current_percentile']}, "
          f"star p{eq['mst']['star_percentile']}; cross TDA p{out['tda']['current_percentile']}", flush=True)
    return out


if __name__ == "__main__":
    refresh()
