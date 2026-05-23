"""Backtest of a price-vs-DMA regime strategy with confirmation debounce.

STRATEGY:
  - When the regime has been BULL for `confirm_days` consecutive days, go long the index.
  - When the regime has been BEAR for `confirm_days` consecutive days, move to cash.
  - Cash earns the country's risk-free / liquid fund daily rate (annualised in config).

TIMING:
  - Regime[t] is computed from price[t] (after market close).
  - Decision for day t's holding period uses regimes[t-confirm_days : t]
    (i.e., it does NOT use regime[t] itself -- avoids look-ahead).
  - On day t we earn either close[t]/close[t-1] - 1 (if long) or daily cash rate (if in cash).

OUTPUT:
  - Strategy metrics (return, CAGR, vol, Sharpe, max DD)
  - Buy & hold metrics (same)
  - List of every buy/sell trade with date and trigger
  - Equity curves for both
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .config import INDICES, raw_path
from . import ma_regime


def _annual_to_daily(annual: float) -> float:
    return (1.0 + annual) ** (1.0 / 252.0) - 1.0


def _confirmed_positions(regimes: list[str], confirm_days: int, initial: str = "long") -> list[str]:
    """Position series. Switches only after `confirm_days` of a new regime *before* today.

    positions[t] tells you what position is in force during day t (close[t-1] -> close[t]).
    The decision uses regimes[t - confirm_days : t] only (no peek at regime[t]).
    """
    positions = []
    current = initial
    for t in range(len(regimes)):
        if t >= confirm_days:
            window = regimes[t - confirm_days:t]
            if all(r == "bull" for r in window):
                current = "long"
            elif all(r == "bear" for r in window):
                current = "cash"
        positions.append(current)
    return positions


def _equity_curve(daily_returns: np.ndarray, start: float = 100.0) -> np.ndarray:
    return start * np.cumprod(1.0 + daily_returns)


def _max_drawdown(equity: np.ndarray) -> float:
    running_max = np.maximum.accumulate(equity)
    dd = (equity - running_max) / running_max
    return float(dd.min())


def _metrics(returns: np.ndarray) -> dict:
    if len(returns) == 0:
        return {}
    eq = _equity_curve(returns)
    n_years = len(returns) / 252.0
    total_pct = float(eq[-1] / 100.0 - 1.0) * 100.0
    cagr_pct = (float((eq[-1] / 100.0) ** (1.0 / n_years) - 1.0) * 100.0) if n_years > 0 else 0.0
    ann_vol_pct = (float(returns.std(ddof=1)) * np.sqrt(252) * 100.0) if len(returns) > 1 else 0.0
    sharpe = float((returns.mean() * 252.0) / (returns.std(ddof=1) * np.sqrt(252))) if returns.std(ddof=1) > 0 else 0.0
    max_dd_pct = _max_drawdown(eq) * 100.0
    return {
        "total_return_pct": total_pct,
        "cagr_pct": cagr_pct,
        "ann_vol_pct": ann_vol_pct,
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd_pct,
        "final_equity": float(eq[-1]),
    }


def backtest(index_key: str, period: int, confirm_days: int = 2, kind: str = "sma") -> dict:
    cfg = INDICES[index_key]
    cash_rate_annual = float(cfg.get("cash_rate", 0.0))
    cash_daily = _annual_to_daily(cash_rate_annual)
    cash_label = cfg.get("cash_label", "cash")

    # Get regime history
    df = ma_regime.compute_regime(index_key, period, kind).reset_index(drop=True)
    if len(df) < confirm_days + 2:
        return {"error": "not enough data"}

    dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
    prices = df["price"].values
    regimes = df["regime"].tolist()

    # Confirmed position per day
    positions = _confirmed_positions(regimes, confirm_days)

    # Daily index returns (close-to-close)
    index_daily = np.zeros(len(prices))
    index_daily[1:] = prices[1:] / prices[:-1] - 1.0

    # Strategy daily returns
    strat_daily = np.zeros(len(prices))
    for t in range(1, len(prices)):
        if positions[t] == "long":
            strat_daily[t] = index_daily[t]
        else:  # cash
            strat_daily[t] = cash_daily

    # Drop the first row (no return) for fair compounding
    strat_returns = strat_daily[1:]
    bh_returns = index_daily[1:]
    eq_strat = _equity_curve(strat_returns)
    eq_bh = _equity_curve(bh_returns)

    # Trade log: transitions in `positions`
    trades = []
    n_buys = 0
    n_sells = 0
    for t in range(1, len(positions)):
        if positions[t] != positions[t - 1]:
            ttype = "buy" if positions[t] == "long" else "sell"
            # The trigger was the last confirm_days of consistent regime ending at t-1
            trigger_start = max(0, t - confirm_days)
            trigger_end = t - 1
            trades.append({
                "date": dates[t],
                "type": ttype,
                "price": float(prices[t]),
                "trigger_start_date": dates[trigger_start],
                "trigger_end_date": dates[trigger_end],
            })
            if ttype == "buy":
                n_buys += 1
            else:
                n_sells += 1

    # Time-in-market %
    days_long = sum(1 for p in positions[1:] if p == "long")
    days_cash = sum(1 for p in positions[1:] if p == "cash")
    total_days = len(positions) - 1

    # Metrics
    strat_metrics = _metrics(strat_returns)
    bh_metrics = _metrics(bh_returns)

    # Vol comparison: cleaner numbers
    vol_reduction_pct = ((bh_metrics["ann_vol_pct"] - strat_metrics["ann_vol_pct"]) / bh_metrics["ann_vol_pct"] * 100.0) if bh_metrics["ann_vol_pct"] > 0 else 0.0
    dd_reduction_pct = ((abs(bh_metrics["max_drawdown_pct"]) - abs(strat_metrics["max_drawdown_pct"])) / abs(bh_metrics["max_drawdown_pct"]) * 100.0) if bh_metrics["max_drawdown_pct"] != 0 else 0.0

    return {
        "index_key": index_key,
        "index_name": cfg["name"],
        "period": period,
        "kind": kind,
        "confirm_days": confirm_days,
        "cash_rate_annual": cash_rate_annual,
        "cash_label": cash_label,
        "date_range": {"start": dates[1], "end": dates[-1]},
        "n_trading_days": total_days,
        "n_buys": n_buys,
        "n_sells": n_sells,
        "n_trades_total": n_buys + n_sells,
        "days_long": days_long,
        "days_cash": days_cash,
        "pct_time_long": (days_long / total_days * 100.0) if total_days else 0.0,
        "strategy_metrics": strat_metrics,
        "buy_hold_metrics": bh_metrics,
        "vol_reduction_pct": vol_reduction_pct,
        "drawdown_reduction_pct": dd_reduction_pct,
        "equity_curve": {
            "dates": dates[1:],
            "strategy": eq_strat.tolist(),
            "buy_hold": eq_bh.tolist(),
        },
        "trades": trades,
    }
