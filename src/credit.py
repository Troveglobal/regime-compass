"""Credit-stress read — US High-Yield OAS as a systemic-risk gauge and an
equity overlay.

WHY THIS PANE IS DIFFERENT FROM MA/EMA:
  A credit spread is NOT a price you hold — you don't "go long the spread"
  the way you go long an index above its moving average. And a spread is
  inverted: a *rising* spread means bonds falling / risk-off. So we do NOT
  put a tradeable equity curve on the spread itself.

  Instead the spread is read two ways:
    1. GAUGE  — current level (bps), percentile vs its own history, and a
       calm / elevated / stress state. Direction (widening vs tightening)
       and threshold-crossings are the signal, not price-vs-average.
    2. OVERLAY — a conditioned equity backtest: take the plain price-vs-MA
       equity strategy and add credit as an extra risk-off trigger (go to
       cash when the index is bearish OR credit is in stress). The question
       it answers is "does watching credit make the equity strategy safer?"
       — you are still only ever trading equities.

  The headline read is the CREDIT-vs-EQUITY DIVERGENCE: equities calm while
  credit is widening is the classic late-cycle warning.

DATA: FRED BAMLH0A0HYM2 (ICE BofA US HY OAS, in %). ICE redistribution
licensing caps this keyless endpoint to ~3 years of history, so the gauge
percentile and the overlay backtest are computed over a short window — the
UI is explicit about this.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fred, ma_regime
from .config import INDICES
from .ma_backtest import _annual_to_daily, _confirmed_positions, _equity_curve, _metrics

HY_SERIES = "BAMLH0A0HYM2"    # ICE BofA US HY OAS — most sensitive, but ICE caps free history to ~3y
BAA_SERIES = "BAA10Y"         # Moody's Baa corporate yield minus 10Y Treasury — daily, 1986+, no cap
CREDIT_MA = 50          # spread vs its own 50-day average = credit regime
EQUITY_INDEX = "spx"    # credit stress is a US/global read; benchmark against the S&P
EQUITY_PERIOD = 200
CONFIRM_DAYS = 2

# Selectable credit series for the overlay backtest. HY is the sharpest read
# but ICE licensing caps FRED's free feed at ~3 years; Baa (Moody's) is a
# slightly less jumpy investment-grade spread with 40 years of history, so it
# can test the overlay across many crises (2008, 2011, 2020, 2022...).
CREDIT_SERIES = {
    "baa": {"id": BAA_SERIES, "label": "Moody's Baa corporate spread (Baa yield − 10Y Treasury)",
            "short": "Baa spread", "history": "1986+"},
    "hy":  {"id": HY_SERIES, "label": "ICE BofA US High-Yield OAS",
            "short": "HY OAS", "history": "~3y (ICE-capped)"},
}

# Absolute HY-OAS bands in basis points. These are the classic reference
# levels: sub-350 is complacent, 350-550 normal-to-elevated, 550+ is real
# stress (2020 hit ~1100, 2008 ~2000).
_CALM_MAX = 350.0
_ELEVATED_MAX = 550.0


def _state(bps: float) -> str:
    if bps < _CALM_MAX:
        return "calm"
    if bps < _ELEVATED_MAX:
        return "elevated"
    return "stress"


def _load_series(series_id: str) -> pd.DataFrame:
    """A FRED spread series as DataFrame(date, spread_pct), oldest→newest.
    Served from the per-series parquet cache (no network in the common path)."""
    df = fred.fetch_series(series_id, max_age_hours=24)
    if df.empty:
        return df
    df = df.rename(columns={"value": "spread_pct"}).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_spread() -> pd.DataFrame:
    """The live gauge always reads HY OAS (its bands/percentile are HY-calibrated)."""
    return _load_series(HY_SERIES)


def _with_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Add the credit MA and the calm/stress regime (spread above its own MA
    = stress building)."""
    df = df.copy()
    df["ma"] = df["spread_pct"].rolling(CREDIT_MA).mean()
    df["regime"] = np.where(df["spread_pct"] > df["ma"], "stress", "calm")
    return df


def _ma_label(kind: str, period: int) -> str:
    return f"{period}-day {'EMA' if kind == 'ema' else 'SMA'}"


def gauge(kind: str = "sma", period: int = EQUITY_PERIOD) -> dict:
    """Current stress read: level, percentile, state, direction, and the
    credit-vs-equity divergence flag. The equity regime used for divergence
    uses the selected moving average (SMA/EMA × 50/100/200)."""
    df = _load_spread()
    if df.empty or len(df) < CREDIT_MA + 5:
        return {"error": "not enough credit data"}
    df = _with_regime(df)

    last = df.iloc[-1]
    spread_pct = float(last["spread_pct"])
    bps = spread_pct * 100.0
    state = _state(bps)

    # Percentile vs the available window (share of days at or below today)
    pctile = float((df["spread_pct"] <= spread_pct).mean() * 100.0)

    # Direction: change over the last ~20 trading days, in bps
    lookback = min(20, len(df) - 1)
    chg_20d_bps = (spread_pct - float(df["spread_pct"].iloc[-1 - lookback])) * 100.0
    direction = "widening" if chg_20d_bps > 2 else "tightening" if chg_20d_bps < -2 else "flat"

    # Days in current credit regime
    credit_regime = str(last["regime"])
    days = 1
    for i in range(len(df) - 2, -1, -1):
        if str(df.iloc[i]["regime"]) == credit_regime:
            days += 1
        else:
            break

    # Equity regime for divergence: SPX price vs the selected moving average
    eq = ma_regime.compute_regime(EQUITY_INDEX, period, kind)
    equity_regime = str(eq.iloc[-1]["regime"]) if not eq.empty else "n/a"

    # Divergence logic — the money read
    div_state, div_msg = _divergence(equity_regime, credit_regime, direction)

    return {
        "series": HY_SERIES,
        "as_of": last["date"].strftime("%Y-%m-%d"),
        "spread_pct": spread_pct,
        "spread_bps": bps,
        "state": state,
        "percentile": pctile,
        "chg_20d_bps": chg_20d_bps,
        "direction": direction,
        "credit_regime": credit_regime,
        "credit_ma_pct": float(last["ma"]),
        "days_in_regime": days,
        "equity_regime": equity_regime,
        "equity_index_name": INDICES[EQUITY_INDEX]["name"],
        "kind": kind,
        "period": period,
        "equity_ma_label": _ma_label(kind, period),
        "credit_ma_days": CREDIT_MA,
        "divergence": div_state,
        "divergence_msg": div_msg,
        "bands": {"calm_max_bps": _CALM_MAX, "elevated_max_bps": _ELEVATED_MAX},
        "window": {
            "start": df["date"].iloc[0].strftime("%Y-%m-%d"),
            "end": last["date"].strftime("%Y-%m-%d"),
            "n_days": int(len(df)),
        },
    }


def _divergence(equity_regime: str, credit_regime: str, direction: str) -> tuple[str, str]:
    """Compare the equity regime with the credit regime.

    The dangerous case is equities bullish while credit is stressed/widening —
    credit tends to crack before equities roll over.
    """
    if equity_regime == "bull" and credit_regime == "stress":
        return ("warning", "Equities bullish but credit is under stress — "
                            "credit usually widens before equities roll over. Watch closely.")
    if equity_regime == "bear" and credit_regime == "calm":
        return ("healing", "Equities still bearish but credit has calmed — "
                           "credit often heals before equities bottom.")
    if equity_regime == "bull" and credit_regime == "calm":
        return ("aligned_risk_on", "Equities bullish and credit calm — risk-on, no divergence.")
    if equity_regime == "bear" and credit_regime == "stress":
        return ("aligned_risk_off", "Equities bearish and credit stressed — risk-off, both agree.")
    return ("neutral", "No clear divergence signal.")


def history(days: int = 500) -> dict:
    """Spread + its MA + regime, alongside the SPX price (right axis) so the
    divergence is visible on one chart."""
    df = _load_spread()
    if df.empty:
        return {"error": "no credit data"}
    df = _with_regime(df).dropna(subset=["ma"]).tail(days)

    # SPX price aligned onto the spread's dates (forward-filled)
    eq = ma_regime.compute_regime(EQUITY_INDEX, EQUITY_PERIOD, "sma")[["date", "price"]].copy()
    eq["date"] = pd.to_datetime(eq["date"]).astype("datetime64[ns]")
    df["date"] = df["date"].astype("datetime64[ns]")
    merged = pd.merge_asof(df.sort_values("date"), eq.sort_values("date"),
                           on="date", direction="backward")

    return {
        "series": HY_SERIES,
        "equity_index_name": INDICES[EQUITY_INDEX]["name"],
        "dates": [d.strftime("%Y-%m-%d") for d in merged["date"]],
        "spread_pct": merged["spread_pct"].round(3).tolist(),
        "ma_pct": merged["ma"].round(3).tolist(),
        "regime": merged["regime"].tolist(),
        "equity_price": [None if pd.isna(v) else float(v) for v in merged["price"]],
    }


def overlay_backtest(kind: str = "sma", period: int = EQUITY_PERIOD,
                     series: str = "baa") -> dict:
    """Conditioned equity backtest: does adding a credit-stress risk-off
    trigger improve the plain price-vs-MA equity strategy?

    Runnable across all six equity moving averages (SMA/EMA × 50/100/200) and
    two credit series (`baa` = Moody's Baa, 40y history; `hy` = ICE HY OAS, ~3y).

    Three tracks over the overlapping window:
      - buy_hold     : just hold the index
      - equity_only  : long when SPX confirmed bull, else cash (the existing strategy)
      - credit_overlay: long only when SPX confirmed bull AND credit not in stress
    """
    ser = CREDIT_SERIES.get(series, CREDIT_SERIES["baa"])
    spread = _with_regime(_load_series(ser["id"]))
    if spread.empty:
        return {"error": "no credit data"}

    eq = ma_regime.compute_regime(EQUITY_INDEX, period, kind).reset_index(drop=True)
    eq["date"] = pd.to_datetime(eq["date"])

    # Align credit onto equity trading days (forward-fill last known spread/regime),
    # then restrict to the window where credit actually exists.
    cr = spread[["date", "spread_pct", "regime"]].rename(columns={"regime": "credit_regime"})
    eq["date"] = eq["date"].astype("datetime64[ns]")
    cr["date"] = cr["date"].astype("datetime64[ns]")
    m = pd.merge_asof(eq.sort_values("date"), cr.sort_values("date"),
                      on="date", direction="backward")
    m = m[m["spread_pct"].notna()].reset_index(drop=True)
    if len(m) < CONFIRM_DAYS + 5:
        return {"error": "not enough overlapping data"}

    cfg = INDICES[EQUITY_INDEX]
    cash_daily = _annual_to_daily(float(cfg.get("cash_rate", 0.0)))

    dates = m["date"].dt.strftime("%Y-%m-%d").tolist()
    prices = m["price"].values
    regimes = m["regime"].tolist()
    credit_stress = (m["credit_regime"] == "stress").tolist()

    # Base equity positions (long/cash) with 2-day confirmation — same engine as MA pane
    eq_positions = _confirmed_positions(regimes, CONFIRM_DAYS)
    # Overlay: force cash whenever credit is in stress
    ov_positions = [
        "long" if (p == "long" and not credit_stress[t]) else "cash"
        for t, p in enumerate(eq_positions)
    ]

    index_daily = np.zeros(len(prices))
    index_daily[1:] = prices[1:] / prices[:-1] - 1.0

    def _strat_returns(positions):
        out = np.zeros(len(prices))
        for t in range(1, len(prices)):
            out[t] = index_daily[t] if positions[t] == "long" else cash_daily
        return out[1:]

    bh = index_daily[1:]
    eq_only = _strat_returns(eq_positions)
    overlay = _strat_returns(ov_positions)

    def _time_in(positions):
        long_days = sum(1 for p in positions[1:] if p == "long")
        return long_days, len(positions) - 1

    ov_long, ov_total = _time_in(ov_positions)
    eq_long, _ = _time_in(eq_positions)
    # trades in overlay
    ov_trades = sum(1 for t in range(1, len(ov_positions)) if ov_positions[t] != ov_positions[t - 1])
    # days credit forced an extra exit (equity said long, overlay said cash)
    credit_saves = sum(1 for t in range(1, len(prices))
                       if eq_positions[t] == "long" and ov_positions[t] == "cash")

    return {
        "series": ser["id"],
        "credit_series": series if series in CREDIT_SERIES else "baa",
        "credit_series_label": ser["label"],
        "credit_series_short": ser["short"],
        "credit_series_history": ser["history"],
        "equity_index_name": cfg["name"],
        "kind": kind,
        "period": period,
        "equity_ma_label": _ma_label(kind, period),
        "credit_ma_days": CREDIT_MA,
        "confirm_days": CONFIRM_DAYS,
        "window": {"start": dates[1], "end": dates[-1], "n_days": ov_total},
        "buy_hold_metrics": _metrics(bh),
        "equity_only_metrics": _metrics(eq_only),
        "credit_overlay_metrics": _metrics(overlay),
        "eq_pct_time_long": (eq_long / ov_total * 100.0) if ov_total else 0.0,
        "overlay_pct_time_long": (ov_long / ov_total * 100.0) if ov_total else 0.0,
        "overlay_trades": ov_trades,
        "credit_forced_exits_days": credit_saves,
        "equity_curve": {
            "dates": dates[1:],
            "buy_hold": _equity_curve(bh).tolist(),
            "equity_only": _equity_curve(eq_only).tolist(),
            "credit_overlay": _equity_curve(overlay).tolist(),
        },
    }
