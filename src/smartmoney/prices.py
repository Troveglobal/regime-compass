"""
Daily prices + sectoral index closes from NSE archives, for the returns layer.

- stock closes  : sec_bhavdata_full_<DDMMYYYY>.csv   (per-symbol EQ close)
- index closes  : ind_close_all_<DDMMYYYY>.csv        (all Nifty indices incl. sectorals)

Used to answer "how did the flavour perform" — return on accumulated names since the
smart money bought them, and the Nifty sectoral index move over the window.

Fetched files are cached as JSON in data/prices/ so re-runs are cheap/offline. Pure stdlib.
"""

import csv
import io
import json
import os
import ssl
import time
import urllib.request
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from .paths import PRICES_DIR as CACHE
except ImportError:  # standalone
    from paths import PRICES_DIR as CACHE

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "*/*",
      "Referer": "https://www.nseindia.com/"}

EQ_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
IDX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d}.csv"

# our sector -> NSE sectoral index name (must match ind_close 'Index Name')
SECTOR_INDEX = {
    "Financials — Banks": "Nifty Bank",
    "Financials — NBFC": "Nifty Financial Services",
    "Financials — Insurance": "Nifty Financial Services",
    "Financials — Capital Markets": "Nifty Capital Markets",
    "Healthcare": "Nifty Healthcare Index",
    "Realty": "Nifty Realty",
    "IT Services": "Nifty IT",
    "Auto & Components": "Nifty Auto",
    "Metals & Mining": "Nifty Metal",
    "FMCG": "Nifty FMCG",
    "Oil & Gas": "Nifty Oil & Gas",
    "Power / Utilities": "Nifty Power",
    "Power / Renewables": "Nifty Power",
    "Capital Goods": "Nifty Capital Goods",
    "Infrastructure": "Nifty Infrastructure",
    "InvIT / REIT": "Nifty REITs & InvITs",
    "New-Age / Consumer Tech": "Nifty India New Age Consumption",
    "Retail": "Nifty India Consumption",
    "Logistics": "Nifty India Infrastructure & Logistics",
    "Telecom": "Nifty India Digital",
}
BENCHMARK = "Nifty 50"


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_CTX),
                                       urllib.request.HTTPCookieProcessor())


def _get(url, retries=3):
    op = _opener()
    last = None
    for i in range(retries):
        try:
            if i:
                try:
                    op.open(urllib.request.Request("https://www.nseindia.com/", headers=_H), timeout=15).read()
                except Exception:
                    pass
                time.sleep(1.2)
            return op.open(urllib.request.Request(url, headers=_H), timeout=25).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.8)
    raise RuntimeError(f"{url}: {last!r}")


def _fnum(s):
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_eq(text):
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): v for k, v in row.items()}
        if (row.get("SERIES") or "").strip() not in ("EQ", "BE"):
            continue
        c = _fnum(row.get("CLOSE_PRICE"))
        if c:
            out[(row.get("SYMBOL") or "").strip()] = c
    return out


def _parse_idx(text):
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("Index Name") or "").strip()
        c = _fnum(row.get("Closing Index Value"))
        if name and c:
            out[name] = c
    return out


def _closes_for(kind, d: date):
    """Fetch+cache closes for an exact date. kind in {'eq','idx'}. None if absent."""
    os.makedirs(CACHE, exist_ok=True)
    iso = d.isoformat()
    cache = os.path.join(CACHE, f"{kind}_{iso}.json")
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    url = (EQ_URL if kind == "eq" else IDX_URL).format(d=d.strftime("%d%m%Y"))
    try:
        text = _get(url)
        data = _parse_eq(text) if kind == "eq" else _parse_idx(text)
    except Exception:
        data = None
    if data:
        with open(cache, "w") as f:
            json.dump(data, f)
    return data


def closes_on_or_before(kind, target: date, back=6):
    """Nearest trading-day closes at/<= target (steps back over weekends/holidays)."""
    for i in range(back + 1):
        d = target - timedelta(days=i)
        data = _closes_for(kind, d)
        if data:
            return d, data
    return None, None


def load_prices(latest: date, window_starts):
    """
    Returns:
      eq_latest : {symbol: close}
      idx       : {'latest': {name:close}, 'starts': {label: (date, {name:close})}}
    window_starts: dict label -> target start date.
    """
    _, eq_latest = closes_on_or_before("eq", latest)
    _, idx_latest = closes_on_or_before("idx", latest)
    starts = {}
    for label, sd in window_starts.items():
        starts[label] = closes_on_or_before("idx", sd)
    return eq_latest or {}, {"latest": idx_latest or {}, "starts": starts}


if __name__ == "__main__":
    today = date(2026, 6, 30)
    eq, idx = load_prices(today, {"month": today - timedelta(days=30)})
    print("eq symbols:", len(eq), "| sample LODHA:", eq.get("LODHA"))
    print("Nifty 50 latest:", idx["latest"].get("Nifty 50"))
    sd, sc = idx["starts"]["month"]
    print("month start:", sd, "Nifty 50:", sc.get("Nifty 50"))
