"""Valuation metrics — PE/CAPE ratios and MVRV for crypto.

Data sources:
- India Nifty 50 PE: niftyindices.com API (daily, back to 1999)
- US S&P 500 CAPE: Robert Shiller's Yale dataset (monthly, back to 1881)
- BTC/ETH MVRV: CoinMetrics community API (daily, free)
- Korea/China: no free historical PE — snapshot only via ETF proxy
"""
from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .config import DATA_DIR, INDICES

log = logging.getLogger("regime_compass")

_CACHE_DIR = DATA_DIR / "valuation"


def _cache_path(name: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{name}.parquet"


def _is_fresh(path: Path, max_age_hours: int = 24) -> bool:
    if not path.exists():
        return False
    import time
    age = time.time() - path.stat().st_mtime
    return age < max_age_hours * 3600


def fetch_nifty_pe() -> pd.DataFrame:
    cache = _cache_path("nifty_pe")
    if _is_fresh(cache):
        return pd.read_parquet(cache)

    url = "https://www.niftyindices.com/Backpage.aspx/getpepbHistoricaldataDBtoString"
    end = datetime.now().strftime("%d-%b-%Y")
    payload = json.dumps({
        "cinfo": json.dumps({
            "name": "NIFTY 50",
            "startDate": "01-Jan-1999",
            "endDate": end,
            "indexName": "NIFTY 50",
        })
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log.warning("Failed to fetch Nifty PE: %s", e)
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()

    raw = json.loads(data.get("d", "[]"))
    if not raw:
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()

    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["DATE"], format="%d %b %Y")
    df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")
    df = df[["date", "pe", "pb"]].dropna(subset=["pe"]).sort_values("date").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df


def fetch_shiller_cape() -> pd.DataFrame:
    cache = _cache_path("shiller_cape")
    if _is_fresh(cache, max_age_hours=168):
        return pd.read_parquet(cache)

    url = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
    try:
        df = pd.read_excel(url, sheet_name="Data", header=7)
    except Exception as e:
        log.warning("Failed to fetch Shiller data: %s", e)
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()

    df = df.rename(columns={"Date": "date_raw", "CAPE": "cape", "Price": "price"})
    df = df[["date_raw", "price", "cape"]].dropna(subset=["cape"])
    df["cape"] = pd.to_numeric(df["cape"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")

    dates = []
    for val in df["date_raw"]:
        try:
            s = str(val)
            if "." in s:
                year, frac = s.split(".")
                month = int(round(float("0." + frac) * 12)) + 1
                month = min(month, 12)
            else:
                year = s
                month = 1
            dates.append(datetime(int(year), month, 1))
        except Exception:
            dates.append(pd.NaT)

    df["date"] = dates
    df = df[["date", "price", "cape"]].dropna().sort_values("date").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df


def fetch_mvrv(asset: str) -> pd.DataFrame:
    cache = _cache_path(f"mvrv_{asset}")
    if _is_fresh(cache):
        return pd.read_parquet(cache)

    url = (
        f"https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        f"?assets={asset}&metrics=CapMVRVCur&frequency=1d"
        f"&start_time=2010-01-01&end_time={datetime.now().strftime('%Y-%m-%d')}"
        f"&page_size=10000"
    )

    all_rows = []
    while url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "RegimeCompass/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            all_rows.extend(data.get("data", []))
            url = data.get("next_page_url")
        except Exception as e:
            log.warning("Failed to fetch MVRV for %s: %s", asset, e)
            break

    if not all_rows:
        if cache.exists():
            return pd.read_parquet(cache)
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df["date"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df["mvrv"] = pd.to_numeric(df["CapMVRVCur"], errors="coerce")
    df = df[["date", "mvrv"]].dropna().sort_values("date").reset_index(drop=True)
    df.to_parquet(cache, index=False)
    return df


# Equity indices → US-listed ETF proxy whose trailing PE approximates the index.
# Snapshot only (no free history) — used to put a current PE on every equity market.
_PE_PROXIES = {
    "spx":    ("SPY",  "SPDR S&P 500"),
    "nasdaq": ("QQQ",  "Invesco QQQ"),
    "nifty":  ("INDA", "iShares MSCI India"),
    "nikkei": ("EWJ",  "iShares MSCI Japan"),
    "kospi":  ("EWY",  "iShares MSCI South Korea"),
    "shcomp": ("FXI",  "iShares China Large-Cap"),
}


def fetch_pe_snapshots() -> dict:
    """Current trailing PE per equity index via ETF proxies (yfinance), cached 24h."""
    cache = _CACHE_DIR / "pe_snapshots.json"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if _is_fresh(cache):
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass

    import yfinance as yf

    out: dict = {}
    for key, (ticker, proxy_name) in _PE_PROXIES.items():
        try:
            pe = yf.Ticker(ticker).info.get("trailingPE")
            if pe:
                out[key] = {"pe": round(float(pe), 2), "proxy": ticker, "proxy_name": proxy_name}
        except Exception as e:  # noqa: BLE001 — one bad ticker shouldn't kill the rest
            log.warning("PE snapshot failed for %s (%s): %s", key, ticker, e)

    if out:
        cache.write_text(json.dumps(out))
    elif cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            pass
    return out


def valuation_data(index_key: str) -> dict:
    cfg = INDICES.get(index_key)
    if not cfg:
        return {"error": f"Unknown index {index_key}"}

    if index_key == "nifty":
        df = fetch_nifty_pe()
        if df.empty:
            return {"error": "No PE data available"}
        current = float(df["pe"].iloc[-1])
        hist_mean = float(df["pe"].mean())
        hist_median = float(df["pe"].median())
        hist_std = float(df["pe"].std())
        pct_rank = float((df["pe"] <= current).sum() / len(df) * 100)
        return {
            "index_key": index_key,
            "index_name": cfg["name"],
            "metric": "Trailing PE",
            "current": round(current, 2),
            "mean": round(hist_mean, 2),
            "median": round(hist_median, 2),
            "std": round(hist_std, 2),
            "percentile": round(pct_rank, 1),
            "data_start": str(df["date"].iloc[0].date()),
            "data_end": str(df["date"].iloc[-1].date()),
            "count": len(df),
            "series": [
                {"date": str(r["date"].date()), "value": round(float(r["pe"]), 2)}
                for _, r in df.iterrows()
            ],
        }

    if index_key == "spx":
        df = fetch_shiller_cape()
        if df.empty:
            return {"error": "No CAPE data available"}
        current = float(df["cape"].iloc[-1])
        hist_mean = float(df["cape"].mean())
        hist_median = float(df["cape"].median())
        hist_std = float(df["cape"].std())
        pct_rank = float((df["cape"] <= current).sum() / len(df) * 100)
        recent = df[df["date"] >= "2000-01-01"]
        return {
            "index_key": index_key,
            "index_name": cfg["name"],
            "metric": "Shiller CAPE",
            "current": round(current, 2),
            "mean": round(hist_mean, 2),
            "median": round(hist_median, 2),
            "std": round(hist_std, 2),
            "percentile": round(pct_rank, 1),
            "data_start": str(df["date"].iloc[0].date()),
            "data_end": str(df["date"].iloc[-1].date()),
            "count": len(df),
            "series": [
                {"date": str(r["date"].date()), "value": round(float(r["cape"]), 2)}
                for _, r in recent.iterrows()
            ],
        }

    if index_key in ("btc", "eth"):
        df = fetch_mvrv(index_key)
        if df.empty:
            return {"error": "No MVRV data available"}
        current = float(df["mvrv"].iloc[-1])
        hist_mean = float(df["mvrv"].mean())
        hist_median = float(df["mvrv"].median())
        hist_std = float(df["mvrv"].std())
        pct_rank = float((df["mvrv"] <= current).sum() / len(df) * 100)
        zone = "Overvalued" if current > 3.0 else "Undervalued" if current < 1.0 else "Fair value"
        return {
            "index_key": index_key,
            "index_name": cfg["name"],
            "metric": "MVRV Ratio",
            "current": round(current, 2),
            "mean": round(hist_mean, 2),
            "median": round(hist_median, 2),
            "std": round(hist_std, 2),
            "percentile": round(pct_rank, 1),
            "zone": zone,
            "data_start": str(df["date"].iloc[0].date()),
            "data_end": str(df["date"].iloc[-1].date()),
            "count": len(df),
            "series": [
                {"date": str(r["date"].date()), "value": round(float(r["mvrv"]), 2)}
                for _, r in df.iterrows()
            ],
        }

    return {
        "index_key": index_key,
        "index_name": cfg["name"],
        "metric": None,
        "error": f"No free valuation data source for {cfg['name']}. Historical PE for {cfg['country']} requires a paid data provider.",
    }


def valuation_summary() -> list[dict]:
    pe_snaps = fetch_pe_snapshots()
    results = []
    for key in INDICES:
        data = valuation_data(key)
        summary = {
            "index_key": key,
            "index_name": INDICES[key]["name"],
            "metric": data.get("metric"),
            "current": data.get("current"),
            "percentile": data.get("percentile"),
            "mean": data.get("mean"),
            "available": "error" not in data,
        }
        if "zone" in data:
            summary["zone"] = data["zone"]
        if key in pe_snaps:
            summary["pe"] = pe_snaps[key]["pe"]
            summary["pe_proxy"] = pe_snaps[key]["proxy"]
            summary["pe_proxy_name"] = pe_snaps[key]["proxy_name"]
        results.append(summary)
    return results
