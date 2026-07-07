"""
Indonesia — IDX negotiated-market deals (block/crossing trades), daily.

Source: the IDX trading summary API — one request returns every stock's day
including its NonRegular (negotiated market) volume/value/frequency. We keep
only stocks with material negotiated value, i.e. genuine block activity, and
discard the rest, so the store stays tiny.

Cloudflare-fronted: fetched via curl_cffi browser impersonation.
One request per trading day, cached under data/id/.
"""

import json
import time
from datetime import date, datetime

try:
    from . import common as X
    from . import engine as E
except ImportError:
    import common as X
    import engine as E

MKT = "id"
URL = ("https://www.idx.co.id/primary/TradingSummary/GetStockSummary"
       "?length=9999&start=0&date={d}")
THROTTLE = 1.5

CFG = {
    "market": MKT, "market_name": "Indonesia (IDX)",
    "currency": "IDR", "unit": "IDR bn", "unit_div": 1e9,
    "min_value": 10e9,       # keep stocks with >= IDR 10 bn negotiated in window
    "significant": 100e9,    # flag >= IDR 100 bn
    "source": "IDX daily trading summary — negotiated (non-regular) market deals",
}

# keep only stocks whose negotiated value that day clears this floor
DAY_FLOOR = 5e9


def _fetch_day(d: date):
    name = f"nego_{d.isoformat()}.json"
    cached = X.cache_load(MKT, name)
    if cached is not None:
        return cached
    try:
        resp = json.loads(X.get_impersonated(URL.format(d=d.strftime("%Y%m%d"))))
    except Exception:
        return None
    rows = resp.get("data") or []
    if not rows and d >= date.today():
        return None  # not published yet
    day = {"deals": []}
    for r in rows:
        val = X.fnum(r.get("NonRegularValue"))
        if val < DAY_FLOOR:
            continue
        vol = X.fnum(r.get("NonRegularVolume"))
        close = X.fnum(r.get("Close"))
        day["deals"].append({
            "symbol": r.get("StockCode", ""), "security": r.get("StockName", ""),
            "px": round(val / vol, 2) if vol else None, "qty": int(vol),
            "value": val, "close": close,
            "deals": int(X.fnum(r.get("NonRegularFrequency"))) or 1,
        })
    X.cache_save(MKT, name, day)
    return day


def fetch(backfill_days=0):
    fetched = 0
    for d in X.trading_days_back(backfill_days):
        if X.cache_load(MKT, f"nego_{d.isoformat()}.json") is not None:
            continue
        if _fetch_day(d) is not None:
            fetched += 1
        time.sleep(THROTTLE)
    return fetched


def records():
    import glob
    import os
    out = []
    for p in sorted(glob.glob(X.cache_path(MKT, "nego_*.json"))):
        d = datetime.strptime(os.path.basename(p)[5:15], "%Y-%m-%d").date()
        with open(p) as f:
            day = json.load(f)
        for r in day.get("deals", []):
            r["date"] = d
            out.append(r)
    return out


def build():
    return E.build_blocks_feed(records(), CFG)


if __name__ == "__main__":
    n = fetch(backfill_days=95)
    feed = build()
    print(f"fetched {n} new days; deals={feed['stats'].get('raw_deals')} "
          f"latest={feed['meta']['latest_date']}")
    print("wrote", X.write_feed(MKT, feed))
