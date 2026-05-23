"""Train a 3-state Gaussian HMM per index, with stable bear/neutral/bull labeling.

Label rule (vol-dominant composite):
  score_i = -z(mean_realized_vol_i) + 0.25 * z(mean_log_return_i)
  sorted ascending: lowest = bear (highest vol), highest = bull (lowest vol).

Rationale: for a regime indicator, vol is the most actionable axis (when to be
defensive), independent of price direction. Returns get a small weight as
tiebreaker. See docs/design.md for the full discussion.
"""
from __future__ import annotations

import pickle
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from hmmlearn import hmm
from sklearn.preprocessing import StandardScaler

from .config import (
    INDICES,
    MODELS_DIR,
    N_STATES,
    RANDOM_STATE,
    STATE_LABELS,
    features_path,
    model_path,
)
from .features import feature_cols_for


@dataclass
class ModelBundle:
    hmm: hmm.GaussianHMM
    scaler: StandardScaler
    feature_cols: list[str]
    permutation: list[int]  # raw_state_idx for [bear, neutral, bull]
    trained_at: str
    n_train_rows: int
    log_likelihood: float
    index_key: str
    # Stored for /api/indices interpretability:
    state_means: dict  # {"bear": {"log_return": ..., "realized_vol": ..., ...}, ...}
    transmat: list[list[float]]  # in [bear, neutral, bull] order

    def relabel_probs(self, probs: np.ndarray) -> np.ndarray:
        return probs[:, self.permutation]


def _stable_permutation(model: hmm.GaussianHMM, scaler: StandardScaler, feature_cols: list[str]) -> list[int]:
    means_orig = scaler.inverse_transform(model.means_)
    ret_idx = feature_cols.index("log_return")
    vol_idx = feature_cols.index("realized_vol")
    rets = means_orig[:, ret_idx]
    vols = means_orig[:, vol_idx]

    def z(x: np.ndarray) -> np.ndarray:
        mu, sd = x.mean(), x.std(ddof=0)
        return (x - mu) / (sd if sd > 0 else 1.0)

    score = -z(vols) + 0.25 * z(rets)
    return np.argsort(score).tolist()


def train_one(key: str) -> ModelBundle:
    feature_cols = feature_cols_for(key)
    features = pd.read_parquet(features_path(key))[feature_cols]
    X_raw = features.values
    scaler = StandardScaler().fit(X_raw)
    X = scaler.transform(X_raw)

    model = hmm.GaussianHMM(
        n_components=N_STATES,
        covariance_type="full",
        n_iter=200,
        tol=1e-4,
        random_state=RANDOM_STATE,
        init_params="stmc",
    )
    model.fit(X)
    if not model.monitor_.converged:
        print(f"[model:{key}] WARN: not converged in {model.monitor_.iter} iters", flush=True)

    perm = _stable_permutation(model, scaler, feature_cols)
    ll = float(model.score(X))

    # State means in original feature space, keyed by label
    means_orig = scaler.inverse_transform(model.means_)
    state_means = {}
    for label_idx, raw_idx in enumerate(perm):
        state_means[STATE_LABELS[label_idx]] = {
            col: float(means_orig[raw_idx, i]) for i, col in enumerate(feature_cols)
        }
    A = model.transmat_[np.ix_(perm, perm)]
    transmat = [[float(v) for v in row] for row in A]

    bundle = ModelBundle(
        hmm=model,
        scaler=scaler,
        feature_cols=feature_cols,
        permutation=perm,
        trained_at=datetime.now(timezone.utc).isoformat(),
        n_train_rows=len(features),
        log_likelihood=ll,
        index_key=key,
        state_means=state_means,
        transmat=transmat,
    )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with open(model_path(key), "wb") as f:
        pickle.dump(bundle, f)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with open(MODELS_DIR / f"hmm_{key}_{stamp}.pkl", "wb") as f:
        pickle.dump(bundle, f)

    _print_summary(bundle)
    return bundle


def _print_summary(b: ModelBundle) -> None:
    name = INDICES[b.index_key]["name"]
    print(f"\n[model:{b.index_key}] {name} -- trained on {b.n_train_rows} rows, LL={b.log_likelihood:,.1f}", flush=True)
    print(f"[model:{b.index_key}] features: {b.feature_cols}", flush=True)
    print(f"[model:{b.index_key}] state means:", flush=True)
    cols = b.feature_cols
    header = "  label    | " + " | ".join(f"{c:>14}" for c in cols)
    print(header, flush=True)
    print("  " + "-" * (len(header) - 2), flush=True)
    for label in STATE_LABELS:
        row = b.state_means[label]
        cells = " | ".join(f"{row[c]:>14.6f}" for c in cols)
        print(f"  {label:<8} | {cells}", flush=True)


def train_all() -> dict[str, ModelBundle]:
    out = {}
    for key in INDICES:
        try:
            out[key] = train_one(key)
        except Exception as e:
            print(f"[model:{key}] FAILED: {e}", file=sys.stderr, flush=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for k in sys.argv[1:]:
            train_one(k)
    else:
        train_all()
