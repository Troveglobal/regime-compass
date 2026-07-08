"""US Macro Pane: Macro Surprise Meter + Global Macro Tracker, precomputed
daily into data/analytics/macro.json.

FRAMING — this is a TREND-DEVIATION index: each series is scored against
its OWN recent trend (trailing 12-observation mean), not against economist
consensus. It is NOT a consensus-surprise index like Citi's CESI, and the
frontend says so prominently.

Lookahead honesty: FRED exposes observation dates, not release dates. Each
series is lagged by a per-series publication-delay constant measured from
the FRED observation date (monthly observation dates are the FIRST of the
reference month, so e.g. payrolls released ~1 week after month end carry a
~35-day lag from the observation date). No observation enters the composite
before observation_date + lag. The ~10y backfill therefore approximates
real-time availability and ignores revisions (first-print values are not
recoverable from this endpoint).

Failures are caught by the scheduler wrapper so they never block the daily
regime job.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import fred
from .config import DATA_DIR, DB_PATH, raw_path

OUT_PATH = DATA_DIR / "analytics" / "macro.json"
_YF_CACHE_DIR = DATA_DIR / "macro"

BACKFILL_YEARS = 10
TREND_OBS = 12            # trailing observations for the trend expectation
STALENESS_HALFLIFE = 45.0  # days for the 0.5^(age/45) weight
Z_CLIP = 3.0
MOMENTUM_DAYS = 63        # ~3 trading months

# Surprise basket. lag_days = typical publication delay measured from the
# FRED observation date (first of the reference month for monthly series,
# week-ending Saturday for ICSA). sign=-1 means a falling pulse is "hotter".
# sigma windows are in observations: 5y trailing std, minimum 3y.
SURPRISE_SERIES = [
    {"id": "PAYEMS",  "label": "Nonfarm payrolls",      "group": "labor",     "transform": "mom_diff",  "sign": +1, "lag_days": 35, "sigma_win": 60,  "sigma_min": 36},
    {"id": "UNRATE",  "label": "Unemployment rate",     "group": "labor",     "transform": "chg_3m",    "sign": -1, "lag_days": 35, "sigma_win": 60,  "sigma_min": 36},
    {"id": "ICSA",    "label": "Initial claims (4wk)",  "group": "labor",     "transform": "avg_4wk",   "sign": -1, "lag_days": 5,  "sigma_win": 260, "sigma_min": 156},
    {"id": "CPIAUCSL", "label": "CPI (3m ann.)",        "group": "inflation", "transform": "ann_3m",    "sign": +1, "lag_days": 43, "sigma_win": 60,  "sigma_min": 36},
    {"id": "CPILFESL", "label": "Core CPI (3m ann.)",   "group": "inflation", "transform": "ann_3m",    "sign": +1, "lag_days": 43, "sigma_win": 60,  "sigma_min": 36},
    {"id": "RSAFS",   "label": "Retail sales (3m/3m)",  "group": "consumer",  "transform": "m3m3_ann",  "sign": +1, "lag_days": 45, "sigma_win": 60,  "sigma_min": 36},
    {"id": "UMCSENT", "label": "UMich sentiment",       "group": "consumer",  "transform": "level",     "sign": +1, "lag_days": 28, "sigma_win": 60,  "sigma_min": 36},
    {"id": "INDPRO",  "label": "Industrial production", "group": "industry",  "transform": "ann_3m",    "sign": +1, "lag_days": 45, "sigma_win": 60,  "sigma_min": 36},
    {"id": "AMTMNO",  "label": "Mfg new orders",        "group": "industry",  "transform": "ann_3m",    "sign": +1, "lag_days": 65, "sigma_win": 60,  "sigma_min": 36},
    {"id": "GACDFSA066MSFRBPHI", "label": "Philly Fed", "group": "industry",  "transform": "level",     "sign": +1, "lag_days": 18, "sigma_win": 60,  "sigma_min": 36},
    {"id": "GACDISA066MSFRBNY",  "label": "Empire State", "group": "industry", "transform": "level",    "sign": +1, "lag_days": 14, "sigma_win": 60,  "sigma_min": 36},
    {"id": "HOUST",   "label": "Housing starts (3m avg YoY)", "group": "housing", "transform": "avg3_yoy", "sign": +1, "lag_days": 47, "sigma_win": 60, "sigma_min": 36},
]

GROWTH_GROUPS = {"labor", "consumer", "industry", "housing"}
INFLATION_GROUPS = {"inflation"}

# Global Macro Tracker (market-implied, daily). FRED series are all keyless;
# BAMLH0A0HYM2 history is capped to ~3y by ICE licensing (noted in output).
TRACKER_FRED = [
    {"key": "t10y2y",  "id": "T10Y2Y",       "label": "2s10s curve",        "group": "growth",     "unit": "pp",  "decimals": 2, "slope": True},
    {"key": "t10y3m",  "id": "T10Y3M",       "label": "3m10y curve",        "group": "growth",     "unit": "pp",  "decimals": 2, "slope": True},
    {"key": "real10y", "id": "DFII10",       "label": "Real 10y yield",     "group": "financial",  "unit": "%",   "decimals": 2},
    {"key": "bei10y",  "id": "T10YIE",       "label": "10y breakeven",      "group": "inflation",  "unit": "%",   "decimals": 2},
    {"key": "hyspread", "id": "BAMLH0A0HYM2", "label": "HY credit spread",  "group": "financial",  "unit": "pp",  "decimals": 2,
     "note": "History limited to ~3y on FRED's keyless endpoint (ICE licensing)."},
    {"key": "f5y5y",   "id": "T5YIFR",       "label": "5y5y fwd inflation", "group": "inflation",  "unit": "%",   "decimals": 2},
]


def _pulse(values: pd.Series, transform: str) -> pd.Series:
    """values: raw series indexed by observation date → pulse per the basket table."""
    if transform == "mom_diff":
        return values.diff()
    if transform == "chg_3m":
        return values.diff(3)
    if transform == "avg_4wk":
        return values.rolling(4).mean()
    if transform == "ann_3m":
        return ((values / values.shift(3)) ** 4 - 1.0) * 100.0
    if transform == "m3m3_ann":
        m3 = values.rolling(3).mean()
        return ((m3 / m3.shift(3)) ** 4 - 1.0) * 100.0
    if transform == "level":
        return values * 1.0
    if transform == "avg3_yoy":
        return values.rolling(3).mean().pct_change(12) * 100.0
    raise ValueError(f"unknown transform {transform}")


def surprise_z(pulse: pd.Series, sign: int, sigma_win: int, sigma_min: int,
               trend_obs: int = TREND_OBS, clip: float = Z_CLIP) -> pd.Series:
    """Per-observation surprise score.

    E_t = trailing mean of the previous `trend_obs` pulses (excludes latest);
    sigma = trailing std over `sigma_win` prior observations (min `sigma_min`);
    z = sign * (pulse - E_t) / sigma, clipped. NaN where history is short or
    sigma is ~0 (no lookahead: both moments use only prior observations).
    """
    prior = pulse.shift(1)
    expected = prior.rolling(trend_obs, min_periods=trend_obs).mean()
    sigma = prior.rolling(sigma_win, min_periods=sigma_min).std()
    sigma = sigma.where(sigma > 1e-12)
    z = (sign * (pulse - expected) / sigma).clip(-clip, clip)
    return z


def staleness_weight(age_days: pd.Series | np.ndarray | float) -> pd.Series | np.ndarray | float:
    return 0.5 ** (np.asarray(age_days, dtype=float) / STALENESS_HALFLIFE)


def _series_frame(cfg: dict, raw: pd.DataFrame) -> pd.DataFrame | None:
    """One series → DataFrame indexed by availability date with columns
    [z, pulse, expected, obs_date]. Availability = obs_date + lag_days."""
    if raw is None or raw.empty:
        return None
    s = raw.set_index("date")["value"].astype(float).sort_index()
    pulse = _pulse(s, cfg["transform"])
    prior = pulse.shift(1)
    expected = prior.rolling(TREND_OBS, min_periods=TREND_OBS).mean()
    z = surprise_z(pulse, cfg["sign"], cfg["sigma_win"], cfg["sigma_min"])
    df = pd.DataFrame({"z": z, "pulse": pulse, "expected": expected})
    df["obs_date"] = df.index
    df.index = df.index + pd.Timedelta(days=cfg["lag_days"])  # publication-lag enforcement
    return df.dropna(subset=["z"])


def build_composites(frames: dict[str, pd.DataFrame], days: pd.DatetimeIndex) -> pd.DataFrame:
    """Daily walk-forward composites from per-series availability frames.

    For each day, each series contributes its latest AVAILABLE z with weight
    0.5^(days_since_observation/45). Returns DataFrame(composite, growth,
    inflation) indexed by `days`.
    """
    groups = {c["id"]: c["group"] for c in SURPRISE_SERIES}
    zcols, wcols = {}, {}
    for sid, f in frames.items():
        if f is None or f.empty:
            continue
        f = f[~f.index.duplicated(keep="last")].sort_index()
        daily = f.reindex(days, method="ffill")
        age = (days - pd.DatetimeIndex(daily["obs_date"])).days
        zcols[sid] = daily["z"]
        wcols[sid] = pd.Series(staleness_weight(age), index=days).where(daily["z"].notna())

    if not zcols:
        raise RuntimeError("no surprise series available")
    Z = pd.DataFrame(zcols)
    W = pd.DataFrame(wcols)

    def _weighted(cols: list[str]) -> pd.Series:
        z, w = Z[cols], W[cols]
        num = (z * w).sum(axis=1, min_count=1)
        den = w.sum(axis=1, min_count=1)
        return num / den

    out = pd.DataFrame(index=days)
    out["composite"] = _weighted(list(Z.columns))
    out["growth"] = _weighted([c for c in Z.columns if groups[c] in GROWTH_GROUPS])
    out["inflation"] = _weighted([c for c in Z.columns if groups[c] in INFLATION_GROUPS])
    return out


def zone_label(x: float) -> str:
    if x >= 1.0:
        return "Hot"
    if x >= 0.3:
        return "Warm"
    if x > -0.3:
        return "Neutral"
    if x > -1.0:
        return "Cool"
    return "Cold"


def _equity_regime() -> dict | None:
    """Latest S&P 500 HMM state from the existing regime DB (read-only)."""
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT date, hard_state FROM probabilities WHERE index_key='spx' "
            "ORDER BY date DESC LIMIT 1").fetchone()
        con.close()
        if row:
            return {"state": row["hard_state"], "date": row["date"]}
    except sqlite3.Error:
        pass
    return None


def divergence(momentum: float | None, equity: dict | None) -> dict:
    if momentum is None or equity is None:
        return {"state": "unavailable", "text": "Divergence check unavailable (missing regime or momentum data)."}
    state, mom = equity["state"], momentum
    if state == "bull" and mom < -0.5:
        return {"state": "late_cycle", "equity_regime": state, "momentum": round(mom, 2),
                "text": "Late-cycle divergence: equities bullish while macro momentum deteriorates."}
    if state == "bear" and mom > 0.5:
        return {"state": "early_cycle", "equity_regime": state, "momentum": round(mom, 2),
                "text": "Early-cycle divergence: macro improving while the equity regime remains defensive."}
    return {"state": "aligned", "equity_regime": state, "momentum": round(mom, 2),
            "text": "Aligned: the equity regime and macro momentum point the same way."}


# ---------------------------------------------------------------- tracker

def _fetch_yf_daily(ticker: str, cache_name: str, start: str = "2005-01-01") -> pd.DataFrame:
    """Supporting daily closes via yfinance (site's standard provider).
    New pull justification: oil (CL=F) and copper (HG=F) exist nowhere in the
    pipeline; FRED's copper is monthly-only, so yfinance keeps both daily."""
    import yfinance as yf
    _YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _YF_CACHE_DIR / f"{cache_name}.parquet"
    if cache.exists() and (datetime.now().timestamp() - cache.stat().st_mtime) < 24 * 3600:
        return pd.read_parquet(cache)
    try:
        df = yf.download(ticker, start=start, progress=False)
        if df.empty:
            raise ValueError("empty")
        s = df["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.squeeze()
        idx = s.index.tz_localize(None) if s.index.tz else s.index
        out = pd.DataFrame({"date": idx, "value": s.values}).dropna().reset_index(drop=True)
        out.to_parquet(cache, index=False)
        return out
    except Exception:
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()


def _parquet_series(key: str, col: str) -> pd.DataFrame:
    """Reuse a column already pulled into an HMM market's raw.parquet."""
    try:
        raw = pd.read_parquet(raw_path(key), columns=[col])
    except Exception:
        return pd.DataFrame()
    s = raw[col].dropna()
    return pd.DataFrame({"date": s.index, "value": s.values}).reset_index(drop=True)


def _gauge(df: pd.DataFrame, key: str, label: str, group: str, unit: str,
           decimals: int = 2, slope: bool = False, note: str | None = None) -> dict | None:
    if df is None or df.empty or len(df) < 30:
        return None
    s = df.set_index("date")["value"].astype(float).sort_index()
    cur_date = s.index[-1]
    cur = float(s.iloc[-1])
    m1 = s.asof(cur_date - pd.Timedelta(days=30))
    chg_1m = cur - float(m1) if pd.notna(m1) else None
    tail_1y = s[s.index >= cur_date - pd.Timedelta(days=365)]
    pctile = float((tail_1y < cur).mean() * 100) if len(tail_1y) >= 60 else None
    spark = s[s.index >= cur_date - pd.Timedelta(days=90)]
    g = {
        "key": key, "label": label, "group": group, "unit": unit,
        "value": round(cur, decimals),
        "chg_1m": round(chg_1m, decimals) if chg_1m is not None else None,
        "pctile_1y": round(pctile, 1) if pctile is not None else None,
        "spark": [round(float(v), decimals + 1) for v in spark.values],
        "as_of": cur_date.strftime("%Y-%m-%d"),
    }
    if slope:
        if cur < 0:
            g["slope_label"] = "inverted"
        elif chg_1m is not None:
            g["slope_label"] = "steepening" if chg_1m > 0.02 else "flattening" if chg_1m < -0.02 else "flat"
    if note:
        g["note"] = note
    return g


def build_tracker() -> tuple[list[dict], list[str]]:
    gauges, skipped = [], []
    fred_data = fred.fetch_many([c["id"] for c in TRACKER_FRED])
    for cfg in TRACKER_FRED:
        g = _gauge(fred_data.get(cfg["id"]), cfg["key"], cfg["label"], cfg["group"],
                   cfg["unit"], cfg["decimals"], cfg.get("slope", False), cfg.get("note"))
        if g:
            g["source"] = f"FRED {cfg['id']}"
            gauges.append(g)
        else:
            skipped.append(cfg["label"])

    # DXY — reuse the fx column already pulled daily for every USD market.
    dxy = _parquet_series("spx", "fx")
    g = _gauge(dxy, "dxy", "Dollar (DXY)", "financial", "", 1)
    if g:
        g["source"] = "existing pipeline (DX-Y.NYB)"
        gauges.append(g)
    else:
        skipped.append("Dollar (DXY)")

    # Oil & copper — new yfinance pulls (justification in _fetch_yf_daily).
    oil = _fetch_yf_daily("CL=F", "oil_wti")
    g = _gauge(oil, "oil", "Oil (WTI)", "growth", "$", 1)
    if g:
        g["source"] = "yfinance CL=F"
        gauges.append(g)
    else:
        skipped.append("Oil (WTI)")

    copper = _fetch_yf_daily("HG=F", "copper")
    gold = _parquet_series("gold", "price")  # reuse the HMM gold market's closes
    if not copper.empty and not gold.empty:
        c = copper.set_index("date")["value"]
        au = gold.set_index("date")["value"]
        ratio = (c / au).dropna() * 1000.0  # copper $/lb per gold $/oz, x1000 for readability
        rdf = pd.DataFrame({"date": ratio.index, "value": ratio.values})
        g = _gauge(rdf, "copper_gold", "Copper/Gold (x1000)", "growth", "", 2)
        if g:
            g["source"] = "yfinance HG=F / existing gold pipeline"
            gauges.append(g)
    else:
        skipped.append("Copper/Gold ratio")
    return gauges, skipped


# ---------------------------------------------------------------- build

def build() -> dict:
    raw = fred.fetch_many([c["id"] for c in SURPRISE_SERIES])
    frames, excluded = {}, []
    for cfg in SURPRISE_SERIES:
        f = _series_frame(cfg, raw.get(cfg["id"]))
        if f is None or f.empty:
            excluded.append({"id": cfg["id"], "label": cfg["label"], "reason": "no data or insufficient history"})
            continue
        frames[cfg["id"]] = f

    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    days = pd.bdate_range(end=today, periods=int(BACKFILL_YEARS * 261) + MOMENTUM_DAYS)
    comps = build_composites(frames, days)
    comps["momentum"] = comps["growth"].diff(MOMENTUM_DAYS)
    comps = comps.dropna(subset=["composite"])
    if comps.empty:
        raise RuntimeError("composite came back empty")

    latest = comps.iloc[-1]
    cur = float(latest["composite"])
    momentum = float(latest["momentum"]) if pd.notna(latest["momentum"]) else None

    def _dir_3m(col: str) -> str | None:
        d = comps[col].diff(MOMENTUM_DAYS).iloc[-1]
        if pd.isna(d):
            return None
        return "up" if d > 0.1 else "down" if d < -0.1 else "flat"

    # Series breakdown as of today (latest available observation per series)
    breakdown = []
    for cfg in SURPRISE_SERIES:
        f = frames.get(cfg["id"])
        if f is None or f.empty:
            continue
        avail = f[f.index <= today]
        if avail.empty:
            continue
        r = avail.iloc[-1]
        age = int((today - r["obs_date"]).days)
        breakdown.append({
            "id": cfg["id"], "label": cfg["label"], "group": cfg["group"],
            "obs_date": r["obs_date"].strftime("%Y-%m-%d"),
            "pulse": round(float(r["pulse"]), 2),
            "expected": round(float(r["expected"]), 2),
            "z": round(float(r["z"]), 2),
            "weight": round(float(staleness_weight(age)), 3),
            "hotter": "higher" if cfg["sign"] > 0 else "lower",
            "lag_days": cfg["lag_days"],
        })
    breakdown.sort(key=lambda b: abs(b["z"]), reverse=True)

    # ~10y weekly history for the chart. W-FRI labels the bucket's Friday,
    # which for the current partial week lies in the future — clamp it.
    weekly = comps.resample("W-FRI").last().dropna(subset=["composite"])
    if len(weekly) and weekly.index[-1] > comps.index[-1]:
        weekly.index = weekly.index[:-1].append(comps.index[-1:])
    history = [[d.strftime("%Y-%m-%d"),
                round(float(r["composite"]), 3),
                round(float(r["growth"]), 3) if pd.notna(r["growth"]) else None,
                round(float(r["inflation"]), 3) if pd.notna(r["inflation"]) else None]
               for d, r in weekly.iterrows()]

    gauges, tracker_skipped = build_tracker()
    equity = _equity_regime()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": comps.index[-1].strftime("%Y-%m-%d"),
        "framing": ("Trend-deviation index: each series is scored against its own trailing "
                    "12-observation trend, not economist consensus. Not comparable to Citi's CESI."),
        "composite": {
            "current": round(cur, 2),
            "zone": zone_label(cur),
            "growth": {"current": round(float(latest["growth"]), 2) if pd.notna(latest["growth"]) else None,
                       "dir_3m": _dir_3m("growth")},
            "inflation": {"current": round(float(latest["inflation"]), 2) if pd.notna(latest["inflation"]) else None,
                          "dir_3m": _dir_3m("inflation")},
            "momentum": round(momentum, 2) if momentum is not None else None,
        },
        "divergence": divergence(momentum, equity),
        "history_weekly": history,
        "breakdown": breakdown,
        "series_excluded": excluded,
        "tracker": {"gauges": gauges, "skipped": tracker_skipped,
                    "groups": [{"key": "growth", "label": "Growth pulse"},
                               {"key": "inflation", "label": "Inflation pulse"},
                               {"key": "financial", "label": "Financial conditions"}]},
        "lags": {c["id"]: c["lag_days"] for c in SURPRISE_SERIES},
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    c = out["composite"]
    print(f"[macro] wrote {OUT_PATH} — composite {c['current']} ({c['zone']}), "
          f"growth {c['growth']['current']}, inflation {c['inflation']['current']}, "
          f"momentum {c['momentum']}, divergence {out['divergence']['state']}", flush=True)
    return out


if __name__ == "__main__":
    try:
        refresh()
    except Exception as e:
        print(f"[macro] FAILED: {e}", file=sys.stderr, flush=True)
        raise
