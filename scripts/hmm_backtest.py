"""Backtest + accuracy audit of the Regime Compass HMM.

Two runs per index:
  1. IN-SAMPLE   -- exactly what the dashboard shows today: HMM + scaler fitted on the
                    FULL history, filtered probs computed back over that same history.
  2. WALK-FORWARD -- honest version: start with the first TRAIN_MIN_OBS days, refit the
                    HMM every RETRAIN_EVERY trading days using ONLY data up to that point,
                    then produce filtered states for the next block. No future data ever
                    touches the model or the scaler.

Strategy (identical for both): state[t] is known at close of day t; it decides the
position for day t+1. Long the index unless state == bear, else cash at the index's
cash_rate. Metrics gross and net of 10 bps per switch.

Predictive-power stats: next-day return/vol conditional on today's state (causal),
Welch t-stat bear-vs-bull next-day returns, state persistence, flips/year.

Usage: python scripts/hmm_backtest.py [index_key ...]   (default: all)
Output: scripts/backtest_out/<key>.json + summary.json
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from hmmlearn import hmm
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import INDICES, N_STATES, RANDOM_STATE, STATE_LABELS, features_path, raw_path
from src.features import feature_cols_for
from src.inference import _filtered_probs

warnings.filterwarnings("ignore")

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "validation"
TRAIN_MIN_OBS = 750       # ~3 years before first out-of-sample prediction
RETRAIN_EVERY = 63        # refit quarterly
COST_PER_SWITCH = 0.001   # 10 bps each time we move index<->cash


class _Shim:
    """Minimal stand-in so we can reuse src.inference._filtered_probs."""
    def __init__(self, model):
        self.hmm = model


def _fit(X: np.ndarray):
    model = hmm.GaussianHMM(
        n_components=N_STATES, covariance_type="full", n_iter=200,
        tol=1e-4, random_state=RANDOM_STATE, init_params="stmc",
    )
    model.fit(X)
    return model


def _permutation(model, scaler, feature_cols) -> list[int]:
    means_orig = scaler.inverse_transform(model.means_)
    rets = means_orig[:, feature_cols.index("log_return")]
    vols = means_orig[:, feature_cols.index("realized_vol")]

    def z(x):
        sd = x.std(ddof=0)
        return (x - x.mean()) / (sd if sd > 0 else 1.0)

    score = -z(vols) + 0.25 * z(rets)
    return np.argsort(score).tolist()


def in_sample_states(feats: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    X_raw = feats.values
    scaler = StandardScaler().fit(X_raw)
    X = scaler.transform(X_raw)
    model = _fit(X)
    perm = _permutation(model, scaler, feature_cols)
    probs = _filtered_probs(_Shim(model), X)[:, perm]
    return probs.argmax(axis=1)


def walk_forward_states(feats: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    """states[t] = filtered hard state at t, model fitted only on data < block start."""
    X_all = feats.values
    T = len(X_all)
    states = np.full(T, -1, dtype=int)
    n_fits = 0
    for t0 in range(TRAIN_MIN_OBS, T, RETRAIN_EVERY):
        t1 = min(t0 + RETRAIN_EVERY, T)
        scaler = StandardScaler().fit(X_all[:t0])
        model = _fit(scaler.transform(X_all[:t0]))
        perm = _permutation(model, scaler, feature_cols)
        # filter over full past + current block (causal: alpha[t] uses obs <= t)
        probs = _filtered_probs(_Shim(model), scaler.transform(X_all[:t1]))[:, perm]
        states[t0:t1] = probs[t0:t1].argmax(axis=1)
        n_fits += 1
    print(f"  walk-forward: {n_fits} refits", flush=True)
    return states


def strategy_metrics(dates, prices, states, cash_rate_annual):
    """states[t] known at close t -> position for return t->t+1. states==-1 -> long (warmup)."""
    n = len(prices)
    idx_ret = np.zeros(n)
    idx_ret[1:] = prices[1:] / prices[:-1] - 1.0
    cash_daily = (1 + cash_rate_annual) ** (1 / 252) - 1

    pos = np.ones(n, dtype=bool)  # pos[t] = long during day t's return
    for t in range(1, n):
        s = states[t - 1]
        pos[t] = (s != 0)  # 0 = bear

    strat = np.where(pos, idx_ret, cash_daily)
    switches = np.abs(np.diff(pos.astype(int)))
    strat_net = strat.copy()
    strat_net[1:] -= switches * COST_PER_SWITCH

    def m(r):
        r = r[1:]
        eq = np.cumprod(1 + r)
        yrs = len(r) / 252
        rm = np.maximum.accumulate(eq)
        return {
            "total_return_pct": float((eq[-1] - 1) * 100),
            "cagr_pct": float((eq[-1] ** (1 / yrs) - 1) * 100),
            "ann_vol_pct": float(r.std(ddof=1) * np.sqrt(252) * 100),
            "sharpe": float(r.mean() * 252 / (r.std(ddof=1) * np.sqrt(252))) if r.std(ddof=1) > 0 else 0.0,
            "max_drawdown_pct": float(((eq - rm) / rm).min() * 100),
        }

    eq_strat = np.cumprod(1 + strat_net[1:]) * 100
    eq_bh = np.cumprod(1 + idx_ret[1:]) * 100
    return {
        "strategy_gross": m(strat),
        "strategy_net": m(strat_net),
        "buy_hold": m(idx_ret),
        "n_switches": int(switches.sum()),
        "pct_time_long": float(pos[1:].mean() * 100),
        "equity": {
            "dates": [d.strftime("%Y-%m-%d") for d in dates[1:]],
            "strategy": [round(float(v), 3) for v in eq_strat],
            "buy_hold": [round(float(v), 3) for v in eq_bh],
        },
    }


def predictive_stats(prices, states):
    """Does today's state say anything about TOMORROW? (fully causal)"""
    ret = np.zeros(len(prices))
    ret[1:] = prices[1:] / prices[:-1] - 1.0
    nxt = ret[1:]           # return t->t+1
    st = states[:-1]        # state at t
    mask = st >= 0
    nxt, st = nxt[mask], st[mask]

    by_state = {}
    for i, lab in enumerate(STATE_LABELS):
        r = nxt[st == i]
        if len(r) < 20:
            by_state[lab] = None
            continue
        by_state[lab] = {
            "n_days": int(len(r)),
            "mean_next_day_ret_bps": float(r.mean() * 1e4),
            "ann_return_pct": float(r.mean() * 252 * 100),
            "ann_vol_pct": float(r.std(ddof=1) * np.sqrt(252) * 100),
            "pct_days_down_gt_1pct": float((r < -0.01).mean() * 100),
        }

    rb, rbl = nxt[st == 0], nxt[st == 2]
    t_stat = None
    if len(rb) > 20 and len(rbl) > 20:
        se = np.sqrt(rb.var(ddof=1) / len(rb) + rbl.var(ddof=1) / len(rbl))
        t_stat = float((rbl.mean() - rb.mean()) / se) if se > 0 else None

    valid = states[states >= 0]
    flips = int((np.diff(valid) != 0).sum())
    runs = flips + 1
    return {
        "by_state": by_state,
        "t_stat_bull_minus_bear_nextday": t_stat,
        "vol_ratio_bear_over_bull": (
            float(by_state["bear"]["ann_vol_pct"] / by_state["bull"]["ann_vol_pct"])
            if by_state.get("bear") and by_state.get("bull") else None
        ),
        "avg_regime_length_days": float(len(valid) / runs),
        "flips_per_year": float(flips / (len(valid) / 252)),
        "state_share_pct": {
            lab: float((valid == i).mean() * 100) for i, lab in enumerate(STATE_LABELS)
        },
    }


def run_one(key: str) -> dict:
    cfg = INDICES[key]
    feature_cols = feature_cols_for(key)
    feats = pd.read_parquet(features_path(key))[feature_cols]
    raw = pd.read_parquet(raw_path(key))
    prices = raw["price"].reindex(feats.index).values
    dates = feats.index

    print(f"[{key}] {cfg['name']}: {len(feats)} rows {dates[0].date()} -> {dates[-1].date()}", flush=True)

    st_is = in_sample_states(feats, feature_cols)
    st_wf = walk_forward_states(feats, feature_cols)

    # evaluate both on the SAME window (where walk-forward has predictions)
    w = st_wf >= 0
    first = int(np.argmax(w))
    d, p = dates[first:], prices[first:]

    res = {
        "index_key": key,
        "index_name": cfg["name"],
        "cash_rate": cfg["cash_rate"],
        "cash_label": cfg["cash_label"],
        "n_rows": int(len(feats)),
        "eval_start": str(d[0].date()),
        "eval_end": str(d[-1].date()),
        "features": feature_cols,
        "in_sample": {
            "backtest": strategy_metrics(d, p, st_is[first:], cfg["cash_rate"]),
            "stats": predictive_stats(p, st_is[first:]),
        },
        "walk_forward": {
            "backtest": strategy_metrics(d, p, st_wf[first:], cfg["cash_rate"]),
            "stats": predictive_stats(p, st_wf[first:]),
        },
        "disagreement_pct": float((st_is[first:] != st_wf[first:]).mean() * 100),
    }
    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / f"{key}.json", "w") as f:
        json.dump(res, f)
    bt_w, bt_i = res["walk_forward"]["backtest"], res["in_sample"]["backtest"]
    print(f"  in-sample  net CAGR {bt_i['strategy_net']['cagr_pct']:6.2f}%  Sharpe {bt_i['strategy_net']['sharpe']:.2f}  maxDD {bt_i['strategy_net']['max_drawdown_pct']:6.1f}%", flush=True)
    print(f"  walk-fwd   net CAGR {bt_w['strategy_net']['cagr_pct']:6.2f}%  Sharpe {bt_w['strategy_net']['sharpe']:.2f}  maxDD {bt_w['strategy_net']['max_drawdown_pct']:6.1f}%", flush=True)
    print(f"  buy&hold       CAGR {bt_w['buy_hold']['cagr_pct']:6.2f}%  Sharpe {bt_w['buy_hold']['sharpe']:.2f}  maxDD {bt_w['buy_hold']['max_drawdown_pct']:6.1f}%", flush=True)
    return res


def main(keys):
    summary = []
    for k in keys:
        try:
            r = run_one(k)
            summary.append({
                "key": k,
                "name": r["index_name"],
                "eval_start": r["eval_start"],
                "eval_end": r["eval_end"],
                "disagreement_pct": r["disagreement_pct"],
                "in_sample_net": r["in_sample"]["backtest"]["strategy_net"],
                "walk_forward_net": r["walk_forward"]["backtest"]["strategy_net"],
                "buy_hold": r["walk_forward"]["backtest"]["buy_hold"],
                "wf_stats": {kk: vv for kk, vv in r["walk_forward"]["stats"].items() if kk != "by_state"},
            })
        except Exception as e:
            print(f"[{k}] FAILED: {e}", file=sys.stderr, flush=True)
    from datetime import datetime, timezone
    with open(OUT_DIR / "summary.json", "w") as f:
        json.dump({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": {
                "train_min_obs": TRAIN_MIN_OBS,
                "retrain_every": RETRAIN_EVERY,
                "cost_per_switch": COST_PER_SWITCH,
            },
            "markets": summary,
        }, f, indent=2)
    print(f"\nwrote {OUT_DIR}/summary.json ({len(summary)} indices)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:] or list(INDICES))
