"""Filtered probabilities per index, into a single SQLite db.

CRITICAL: filtered (causal) vs smoothed (uses future). Dashboard shows filtered.
We compute filtered via direct forward-algorithm pass.
"""
from __future__ import annotations

import pickle
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .config import DB_PATH, INDICES, STATE_LABELS, features_path, model_path, raw_path
from .model import ModelBundle


def _load_bundle(key: str) -> ModelBundle:
    with open(model_path(key), "rb") as f:
        return pickle.load(f)


def _filtered_probs(bundle: ModelBundle, X_scaled: np.ndarray) -> np.ndarray:
    m = bundle.hmm
    log_emiss = m._compute_log_likelihood(X_scaled)
    log_start = np.log(m.startprob_ + 1e-300)
    log_trans = np.log(m.transmat_ + 1e-300)
    T, K = log_emiss.shape
    log_alpha = np.zeros((T, K))
    log_alpha[0] = log_start + log_emiss[0]
    for t in range(1, T):
        a = log_alpha[t - 1][:, None] + log_trans
        m_ = a.max(axis=0)
        log_alpha[t] = log_emiss[t] + m_ + np.log(np.exp(a - m_[None, :]).sum(axis=0))
    row_max = log_alpha.max(axis=1)
    log_norm = row_max + np.log(np.exp(log_alpha - row_max[:, None]).sum(axis=1))
    return np.exp(log_alpha - log_norm[:, None])


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS probabilities (
            index_key TEXT NOT NULL,
            date TEXT NOT NULL,
            bear REAL NOT NULL,
            neutral REAL NOT NULL,
            bull REAL NOT NULL,
            hard_state TEXT NOT NULL,
            price_close REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (index_key, date)
        );
        CREATE TABLE IF NOT EXISTS metadata (
            index_key TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (index_key, key)
        );
        """
    )
    conn.commit()
    conn.close()


def compute_history_one(key: str) -> pd.DataFrame:
    bundle = _load_bundle(key)
    features = pd.read_parquet(features_path(key))[bundle.feature_cols]
    X = bundle.scaler.transform(features.values)
    probs_raw = _filtered_probs(bundle, X)
    probs = bundle.relabel_probs(probs_raw)

    raw = pd.read_parquet(raw_path(key))
    price = raw["price"].reindex(features.index)

    df = pd.DataFrame(
        {
            "bear": probs[:, 0],
            "neutral": probs[:, 1],
            "bull": probs[:, 2],
            "price_close": price.values,
        },
        index=features.index,
    )
    df["hard_state"] = pd.Categorical.from_codes(
        df[["bear", "neutral", "bull"]].values.argmax(axis=1), categories=STATE_LABELS
    )

    _init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM probabilities WHERE index_key = ?", (key,))
    rows = [
        (
            key,
            d.strftime("%Y-%m-%d"),
            float(r.bear),
            float(r.neutral),
            float(r.bull),
            str(r.hard_state),
            float(r.price_close),
            now,
        )
        for d, r in df.iterrows()
    ]
    conn.executemany(
        "INSERT INTO probabilities (index_key, date, bear, neutral, bull, hard_state, price_close, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (index_key, key, value) VALUES (?, ?, ?)",
        (key, "last_full_rebuild", now),
    )
    conn.commit()
    conn.close()
    print(f"[inference:{key}] wrote {len(df)} rows", flush=True)
    return df


def update_today_one(key: str) -> dict:
    from .features import build_one
    from .fetch import fetch_one

    fetch_one(key)
    build_one(key)
    bundle = _load_bundle(key)
    features = pd.read_parquet(features_path(key))[bundle.feature_cols]
    X = bundle.scaler.transform(features.values)
    probs_raw = _filtered_probs(bundle, X)
    probs = bundle.relabel_probs(probs_raw)
    last_idx = features.index[-1]
    last = probs[-1]
    raw = pd.read_parquet(raw_path(key))
    price_close = float(raw.loc[last_idx, "price"])
    hard = STATE_LABELS[int(np.argmax(last))]

    _init_db()
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO probabilities (index_key, date, bear, neutral, bull, hard_state, price_close, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(index_key, date) DO UPDATE SET "
        "bear=excluded.bear, neutral=excluded.neutral, bull=excluded.bull, "
        "hard_state=excluded.hard_state, price_close=excluded.price_close, updated_at=excluded.updated_at",
        (
            key,
            last_idx.strftime("%Y-%m-%d"),
            float(last[0]),
            float(last[1]),
            float(last[2]),
            hard,
            price_close,
            now,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (index_key, key, value) VALUES (?, ?, ?)",
        (key, "last_daily_update", now),
    )
    conn.commit()
    conn.close()
    out = {
        "index_key": key,
        "date": last_idx.strftime("%Y-%m-%d"),
        "bear": float(last[0]),
        "neutral": float(last[1]),
        "bull": float(last[2]),
        "hard_state": hard,
        "price_close": price_close,
    }
    print(f"[inference:{key}] today: {out}", flush=True)
    return out


def compute_history_all() -> None:
    for key in INDICES:
        try:
            compute_history_one(key)
        except Exception as e:
            print(f"[inference:{key}] FAILED: {e}", file=sys.stderr, flush=True)


def update_today_all() -> None:
    for key in INDICES:
        try:
            update_today_one(key)
        except Exception as e:
            print(f"[inference:{key}] FAILED: {e}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for k in sys.argv[1:]:
            compute_history_one(k)
    else:
        compute_history_all()
