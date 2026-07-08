"""Country hub feed: per-country regime snapshot, market pulse, 10y yield,
and IMF macro vitals, precomputed into data/analytics/countries.json.

Everything here REUSES existing pulls: index and currency series come from
the per-market raw.parquet files the HMM pipeline already maintains; regimes
come from the probabilities table; bond yields go through the shared FRED
fetcher; IMF vitals through src/imf.py (6 cached calls covering all
countries). Failures never block the daily regime job (scheduler wrapper).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

import pandas as pd

from . import fred, imf
from .config import COUNTRIES, DATA_DIR, DB_PATH, INDICES, raw_path

OUT_PATH = DATA_DIR / "analytics" / "countries.json"


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


def _index_pulse(key: str) -> dict | None:
    try:
        raw = pd.read_parquet(raw_path(key), columns=["price"])
    except Exception:
        return None
    s = raw["price"].dropna()
    if len(s) < 30:
        return None
    last = s.index[-1]
    cur = float(s.iloc[-1])

    def _chg(ref) -> float | None:
        base = s.asof(ref)
        return round((cur / float(base) - 1) * 100, 2) if pd.notna(base) else None

    prev_year_end = pd.Timestamp(year=last.year - 1, month=12, day=31)
    return {
        "level": round(cur, 2),
        "chg_1d": round((cur / float(s.iloc[-2]) - 1) * 100, 2) if len(s) > 1 else None,
        "chg_1m": _chg(last - pd.Timedelta(days=30)),
        "chg_ytd": _chg(prev_year_end),
        "as_of": last.strftime("%Y-%m-%d"),
    }


def _currency_pulse(key: str) -> dict | None:
    """Currency leg from the market's raw.parquet fx column (already pulled
    daily): level, 1M % change, 1Y percentile of the level."""
    try:
        raw = pd.read_parquet(raw_path(key), columns=["fx"])
    except Exception:
        return None
    s = raw["fx"].dropna()
    if len(s) < 60:
        return None
    last = s.index[-1]
    cur = float(s.iloc[-1])
    m1 = s.asof(last - pd.Timedelta(days=30))
    tail = s[s.index >= last - pd.Timedelta(days=365)]
    return {
        "level": round(cur, 3),
        "chg_1m_pct": round((cur / float(m1) - 1) * 100, 2) if pd.notna(m1) else None,
        "pctile_1y": round(float((tail < cur).mean() * 100), 1) if len(tail) >= 60 else None,
        "as_of": last.strftime("%Y-%m-%d"),
    }


def _bond(cfg: dict | None) -> dict | None:
    if not cfg:
        return None
    df = fred.fetch_series(cfg["series"], max_age_hours=24)
    if df is None or df.empty:
        return None
    s = df.set_index("date")["value"]
    last = s.index[-1]
    cur = float(s.iloc[-1])
    m3 = s.asof(last - pd.Timedelta(days=92))
    return {
        "label": cfg["label"], "freq": cfg["freq"],
        "value": round(cur, 2),
        "chg_3m": round(cur - float(m3), 2) if pd.notna(m3) else None,
        "as_of": last.strftime("%Y-%m-%d"),
        "source": f"FRED {cfg['series']}",
    }


def build() -> dict:
    con = sqlite3.connect(DB_PATH)
    payloads = imf.fetch_all()
    countries, bond_skips = {}, []

    for slug, cfg in COUNTRIES.items():
        indices = []
        for key in cfg["indices"]:
            pulse = _index_pulse(key) or {}
            indices.append({
                "key": key,
                "name": INDICES[key]["name"],
                "regime": _regime(con, key),
                **pulse,
            })
        v = imf.vitals(payloads, cfg["iso3"])
        bond = _bond(cfg.get("bond"))
        if cfg.get("bond") and not bond:
            bond_skips.append(cfg["name"])
        elif not cfg.get("bond"):
            bond_skips.append(f"{cfg['name']} (no clean free series)")
        countries[slug] = {
            "slug": slug, "name": cfg["name"], "flag": cfg["flag"], "iso3": cfg["iso3"],
            "is_group": bool(cfg.get("group")),
            "primary_index": cfg["primary_index"],
            "indices": indices,
            "currency": {"label": cfg["currency_label"], **(_currency_pulse(cfg["primary_index"]) or {})},
            "bond": bond,
            "vitals": v if v["indicators"] else None,
            "smartmoney": cfg.get("smartmoney"),
            "news_key": slug,
        }
    con.close()

    # Compare strip: next-year GDP forecast + regime vs the covered median
    items = []
    for slug, c in countries.items():
        gdp = ((c.get("vitals") or {}).get("indicators", {}).get("NGDP_RPCH") or {})
        ny = gdp.get("next_year")
        items.append({
            "slug": slug, "name": c["name"], "flag": c["flag"],
            "gdp_next": ny["value"] if ny else None,
            "gdp_next_year": ny["year"] if ny else None,
            "regime": (c["indices"][0].get("regime") or {}).get("state"),
        })
    vals = sorted(i["gdp_next"] for i in items if i["gdp_next"] is not None)
    median = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2 if vals else None

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forecast_rule": f"years > {imf.latest_completed_year()} are IMF WEO forecasts",
        "imf_source": "IMF World Economic Outlook (DataMapper API)",
        "bond_skipped": bond_skips,
        "countries": countries,
        "compare": {"median_gdp_next": round(median, 2) if median is not None else None, "items": items},
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    n = len(out["countries"])
    print(f"[countries] wrote {OUT_PATH} — {n} countries, median next-yr GDP "
          f"{out['compare']['median_gdp_next']}%", flush=True)
    return out


if __name__ == "__main__":
    try:
        refresh()
    except Exception as e:
        print(f"[countries] FAILED: {e}", file=sys.stderr, flush=True)
        raise
