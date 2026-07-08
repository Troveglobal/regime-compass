"""IMF DataMapper client — annual macro vitals + WEO forecasts per country.

One request per indicator returns ALL countries (plus WEO aggregates like
EURO), so a full refresh is 6 HTTP calls. Free, keyless. IMF updates the WEO
~twice a year, so the cache is aggressive (7 days) and any fetch failure
serves the last good cache.

Forecast flagging: DataMapper returns actuals and WEO projections in one
per-year series without markers. Rule (disclosed in page methodology): years
strictly greater than the latest completed calendar year are flagged as IMF
forecasts.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("regime_compass")

_CACHE_DIR = DATA_DIR / "imf"
_BASE = "https://www.imf.org/external/datamapper/api/v1"
_UA = "RegimeCompass/1.0 (+https://www.regimecompass.com)"
CACHE_HOURS = 7 * 24

INDICATORS = {
    "NGDP_RPCH": {"label": "Real GDP growth", "unit": "%", "chart": True},
    "PCPIPCH": {"label": "Inflation (avg CPI)", "unit": "%", "chart": True},
    "LUR": {"label": "Unemployment", "unit": "%"},
    "GGXWDG_NGDP": {"label": "Gov gross debt", "unit": "% of GDP"},
    "BCA_NGDPD": {"label": "Current account", "unit": "% of GDP"},
    "NGDPDPC": {"label": "GDP per capita", "unit": "USD"},
}


def _cache_path(indicator: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{indicator}.json"


def fetch_indicator(indicator: str, max_age_hours: float = CACHE_HOURS) -> dict:
    """Raw DataMapper payload for one indicator (all countries). Cached;
    stale cache served on any failure; {} only when there is no cache."""
    cache = _cache_path(indicator)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600:
        return json.loads(cache.read_text())

    url = f"{_BASE}/{indicator}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
        if "values" not in payload or indicator not in payload["values"]:
            raise ValueError("unexpected response shape")
    except Exception as e:
        log.warning("[imf] fetch %s failed: %s", indicator, e)
        if cache.exists():
            return json.loads(cache.read_text())
        return {}

    cache.write_text(json.dumps(payload, separators=(",", ":")))
    return payload


def series_for(payload: dict, indicator: str, iso3: str) -> dict[int, float]:
    """{year: value} for one country/group out of a DataMapper payload."""
    try:
        raw = payload["values"][indicator][iso3]
    except (KeyError, TypeError):
        return {}
    out = {}
    for y, v in raw.items():
        try:
            out[int(y)] = float(v)
        except (TypeError, ValueError):
            continue
    return dict(sorted(out.items()))


def latest_completed_year(now: datetime | None = None) -> int:
    return (now or datetime.now(timezone.utc)).year - 1


def vitals(payloads: dict[str, dict], iso3: str, history_years: int = 15,
           now: datetime | None = None) -> dict:
    """Assembled vitals for one country: per-indicator latest actual,
    current-year estimate, next-year forecast, and chart history with
    forecast flags."""
    cutoff = latest_completed_year(now)
    cur_year, next_year = cutoff + 1, cutoff + 2
    out = {"iso3": iso3, "forecast_from": cutoff + 1, "indicators": {}}
    for ind, meta in INDICATORS.items():
        s = series_for(payloads.get(ind, {}), ind, iso3)
        if not s:
            continue
        actual_years = [y for y in s if y <= cutoff]
        entry = {
            "label": meta["label"], "unit": meta["unit"],
            "latest_actual": {"year": actual_years[-1], "value": s[actual_years[-1]]} if actual_years else None,
            "current_year": {"year": cur_year, "value": s[cur_year]} if cur_year in s else None,
            "next_year": {"year": next_year, "value": s[next_year]} if next_year in s else None,
        }
        if meta.get("chart"):
            first = max(min(s), cutoff - history_years + 1)
            entry["series"] = [
                {"year": y, "value": v, "forecast": y > cutoff}
                for y, v in s.items() if y >= first
            ]
        out["indicators"][ind] = entry
    return out


def fetch_all(max_age_hours: float = CACHE_HOURS) -> dict[str, dict]:
    """All indicators (each covering all countries), politely spaced."""
    out = {}
    for i, ind in enumerate(INDICATORS):
        cache = _cache_path(ind)
        fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600
        out[ind] = fetch_indicator(ind, max_age_hours)
        if not fresh:
            time.sleep(1.0)
    return out
