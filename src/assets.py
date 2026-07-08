"""Asset hub feed: price-derived vitals for Bitcoin, Ethereum, Gold and
Silver, precomputed into data/analytics/assets.json.

All four are EXISTING HMM markets — regimes, prices and news tags are
reused; nothing here re-fetches or re-models them. New data in this module:
BTC dominance via CoinGecko's free /global endpoint (skipped with a note if
it fails) and the real-10y leg reused from the shared FRED fetcher.

Calendar convention (documented in page methodology and tested): crypto
trades 7 days a week — each asset's OWN stats (vol, drawdowns, MA distance,
weekend split) use all of its own trading days; CROSS-asset stats
(correlations to SPX / DXY / real yields) use the business-day intersection
of both series, consistent with the site's cross-market modules.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import fred
from .config import ASSETS, DATA_DIR, DB_PATH, INDICES, raw_path

OUT_PATH = DATA_DIR / "analytics" / "assets.json"
MACRO_PATH = DATA_DIR / "analytics" / "macro.json"
_CG_CACHE = DATA_DIR / "macro" / "coingecko_global.json"
_UA = "RegimeCompass/1.0 (+https://www.regimecompass.com)"

VOL_WINDOW = 20
CORR_WINDOW = 60


def _price(key: str) -> pd.Series:
    raw = pd.read_parquet(raw_path(key), columns=["price"])
    return raw["price"].dropna()


def realized_vol(s: pd.Series, window: int = VOL_WINDOW) -> pd.Series:
    """Annualized rolling vol on the asset's OWN calendar: 365 periods/year
    for 7-day assets, 252 for business-day assets (inferred from spacing)."""
    rets = np.log(s / s.shift(1)).dropna()
    per_year = 365 if _is_seven_day(s) else 252
    return rets.rolling(window).std() * np.sqrt(per_year)


def _is_seven_day(s: pd.Series) -> bool:
    tail = s.tail(120)
    return bool((pd.DatetimeIndex(tail.index).dayofweek >= 5).any())


def drawdowns(s: pd.Series) -> dict:
    cur = float(s.iloc[-1])
    ath = float(s.max())
    hi52 = float(s[s.index >= s.index[-1] - pd.Timedelta(days=365)].max())
    return {"from_ath": round((cur / ath - 1) * 100, 2),
            "from_52w_high": round((cur / hi52 - 1) * 100, 2),
            "ath": round(ath, 2)}


def ma200_distance(s: pd.Series) -> float | None:
    if len(s) < 200:
        return None
    ma = s.rolling(200).mean().iloc[-1]
    return round((float(s.iloc[-1]) / float(ma) - 1) * 100, 2)


def rolling_corr(asset: pd.Series, other: pd.Series, diff_other: bool = False,
                 window: int = CORR_WINDOW) -> pd.Series:
    """60d correlation on the business-day intersection of both series
    (cross-asset calendar convention). diff_other=True correlates against
    level CHANGES (for yields)."""
    a = np.log(asset / asset.shift(1))
    b = other.diff() if diff_other else np.log(other / other.shift(1))
    df = pd.concat([a.rename("a"), b.rename("b")], axis=1, join="inner").dropna()
    return df["a"].rolling(window).corr(df["b"])


def corr_stats(series: pd.Series) -> dict | None:
    s = series.dropna()
    if len(s) < 30:
        return None
    tail = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    return {"current": round(float(s.iloc[-1]), 2),
            "min_1y": round(float(tail.min()), 2),
            "max_1y": round(float(tail.max()), 2)}


def weekend_vol_split(s: pd.Series, days: int = 365) -> dict | None:
    """Weekend vs weekday annualized vol over the last year — only
    meaningful for 7-day assets."""
    if not _is_seven_day(s):
        return None
    rets = np.log(s / s.shift(1)).dropna()
    rets = rets[rets.index >= rets.index[-1] - pd.Timedelta(days=days)]
    dow = pd.DatetimeIndex(rets.index).dayofweek
    we, wd = rets[dow >= 5], rets[dow < 5]
    if len(we) < 20 or len(wd) < 50:
        return None
    return {"weekend_ann": round(float(we.std() * np.sqrt(365)) * 100, 1),
            "weekday_ann": round(float(wd.std() * np.sqrt(365)) * 100, 1)}


def _regime(con: sqlite3.Connection, key: str) -> dict | None:
    rows = con.execute(
        "SELECT date, hard_state, bear, neutral, bull FROM probabilities "
        "WHERE index_key = ? ORDER BY date DESC LIMIT 400", (key,)).fetchall()
    if not rows:
        return None
    date, state, bear, neutral, bull = rows[0]
    days = 0
    for r in rows:
        if r[1] != state:
            break
        days += 1
    conf = {"bear": bear, "neutral": neutral, "bull": bull}[state]
    return {"state": state, "date": date, "days": days, "confidence": round(float(conf) * 100, 1)}


def _pulse(s: pd.Series) -> dict:
    last = s.index[-1]
    cur = float(s.iloc[-1])

    def _chg(ref):
        base = s.asof(ref)
        return round((cur / float(base) - 1) * 100, 2) if pd.notna(base) else None

    return {
        "level": round(cur, 2),
        "chg_1d": round((cur / float(s.iloc[-2]) - 1) * 100, 2) if len(s) > 1 else None,
        "chg_1m": _chg(last - pd.Timedelta(days=30)),
        "chg_ytd": _chg(pd.Timestamp(year=last.year - 1, month=12, day=31)),
        "chg_1y": _chg(last - pd.Timedelta(days=365)),
        "as_of": last.strftime("%Y-%m-%d"),
    }


def btc_dominance() -> float | None:
    """BTC dominance from CoinGecko's free /global endpoint, cached daily.
    New fetch justification: market-cap shares exist nowhere in the pipeline
    (CoinMetrics free tier has no dominance metric)."""
    try:
        if _CG_CACHE.exists() and (time.time() - _CG_CACHE.stat().st_mtime) < 24 * 3600:
            data = json.loads(_CG_CACHE.read_text())
        else:
            req = urllib.request.Request("https://api.coingecko.com/api/v3/global",
                                         headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            _CG_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _CG_CACHE.write_text(json.dumps(data, separators=(",", ":")))
        return round(float(data["data"]["market_cap_percentage"]["btc"]), 1)
    except Exception:
        if _CG_CACHE.exists():
            try:
                return round(float(json.loads(_CG_CACHE.read_text())["data"]["market_cap_percentage"]["btc"]), 1)
            except Exception:
                pass
        return None


def build() -> dict:
    con = sqlite3.connect(DB_PATH)
    prices = {cfg["key"]: _price(cfg["key"]) for cfg in ASSETS.values()}
    spx = _price("spx")
    dxy = pd.read_parquet(raw_path("spx"), columns=["fx"])["fx"].dropna()
    r10_df = fred.fetch_series("DFII10", max_age_hours=24)
    real10 = r10_df.set_index("date")["value"] if not r10_df.empty else pd.Series(dtype=float)

    # shared extras
    dominance = btc_dominance()
    eth_btc = (prices["eth"] / prices["btc"]).dropna()
    gold_silver = (prices["gold"] / prices["silver"]).dropna()
    gs_tail = gold_silver  # full available history (site data starts 2010)
    copper_gold = None
    try:  # reuse the Copper/Gold gauge Part 1 already computes daily
        macro = json.loads(MACRO_PATH.read_text())
        copper_gold = next((g for g in macro["tracker"]["gauges"] if g["key"] == "copper_gold"), None)
    except Exception:
        pass

    assets, notes = {}, []
    if dominance is None:
        notes.append("BTC dominance unavailable (CoinGecko unreachable) — ETH/BTC ratio shown instead")

    for slug, cfg in ASSETS.items():
        key = cfg["key"]
        s = prices[key]
        vol = realized_vol(s)
        vol_tail5y = vol[vol.index >= vol.index[-1] - pd.Timedelta(days=5 * 365)].dropna()
        cur_vol = float(vol.iloc[-1]) if pd.notna(vol.iloc[-1]) else None

        corr = {
            "spx": corr_stats(rolling_corr(s, spx)),
            "dxy": corr_stats(rolling_corr(s, dxy)),
            "real10y": corr_stats(rolling_corr(s, real10, diff_other=True)) if not real10.empty else None,
        }

        crypto = None
        if cfg["asset_class"] == "crypto":
            crypto = {
                "btc_dominance": dominance,
                "eth_btc": round(float(eth_btc.iloc[-1]), 5),
                "weekend_vol": weekend_vol_split(s),
            }

        metals = None
        if cfg["asset_class"] == "metal":
            gs_cur = float(gold_silver.iloc[-1])
            gs_corr_hist = rolling_corr(prices["gold"], real10, diff_other=True).dropna() if not real10.empty else pd.Series(dtype=float)
            regime_note = None
            if len(gs_corr_hist) > 200:
                cur_c = float(gs_corr_hist.iloc[-1])
                pct = float((gs_corr_hist < cur_c).mean() * 100)
                regime_note = {
                    "corr": round(cur_c, 2), "pctile_hist": round(pct, 1),
                    "label": "decoupled" if pct >= 80 else "normal (negative)",
                }
            metals = {
                "gold_silver": {
                    "level": round(gs_cur, 1),
                    "pctile_hist": round(float((gs_tail < gs_cur).mean() * 100), 1),
                    "hist_years": round((gs_tail.index[-1] - gs_tail.index[0]).days / 365, 1),
                    "spark": [round(float(v), 1) for v in gold_silver.tail(90).values],
                },
                "copper_gold": copper_gold,
                "realyield_regime": regime_note,
            }

        assets[slug] = {
            "slug": slug, "key": key, "name": cfg["name"], "icon": cfg["icon"],
            "asset_class": cfg["asset_class"],
            "regime": _regime(con, key),
            "pulse": _pulse(s),
            "vitals": {
                "drawdown": drawdowns(s),
                "vol20_ann": round(cur_vol * 100, 1) if cur_vol is not None else None,
                "vol20_pctile_5y": round(float((vol_tail5y < cur_vol).mean() * 100), 1) if cur_vol is not None and len(vol_tail5y) > 200 else None,
                "ma200_dist": ma200_distance(s),
                "corr": corr,
                "headline_corr": cfg["headline_corr"],
                "seven_day": _is_seven_day(s),
            },
            "crypto": crypto,
            "metals": metals,
            "news_key": key,  # existing news tags reused as-is
        }
    con.close()

    compare = [{
        "slug": slug, "name": a["name"], "icon": a["icon"],
        "ret_1y": a["pulse"]["chg_1y"], "ret_1m": a["pulse"]["chg_1m"],
        "vol_pctile": a["vitals"]["vol20_pctile_5y"],
        "regime": (a["regime"] or {}).get("state"),
    } for slug, a in assets.items()]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calendar_rule": ("own-calendar stats use all of each asset's trading days "
                          "(7/week for crypto); cross-asset correlations use the "
                          "business-day intersection of both series"),
        "notes": notes,
        "assets": assets,
        "compare": compare,
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"[assets] wrote {OUT_PATH} — {len(out['assets'])} assets", flush=True)
    return out


if __name__ == "__main__":
    try:
        refresh()
    except Exception as e:
        print(f"[assets] FAILED: {e}", file=sys.stderr, flush=True)
        raise
