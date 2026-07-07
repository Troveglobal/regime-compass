"""
Taiwan — TWSE block trades (鉅額交易), per-deal, published daily after close.

Source: https://www.twse.com.tw/rwd/en/block/BFIAUU  (single-security deals:
symbol, paired/single classification, price, volume, value in TWD).
Names + market closes come from the same-day MI_INDEX quotes file, so each
block print carries a premium/discount vs the market close.

Two requests per trading day, cached under data/tw/.
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

MKT = "tw"
BLOCK_URL = "https://www.twse.com.tw/rwd/en/block/BFIAUU?date={d}&selectType=S&response=json"
QUOTES_URL = "https://www.twse.com.tw/rwd/en/afterTrading/MI_INDEX?date={d}&type=ALLBUT0999&response=json"
NAMES_URL = "https://isin.twse.com.tw/isin/e_C_public.jsp?strMode=2"  # English registry
THROTTLE = 2.5  # TWSE rate-limits aggressive clients

CFG = {
    "market": MKT, "market_name": "Taiwan (TWSE)",
    "currency": "TWD", "unit": "NT$m", "unit_div": 1e6,
    "min_value": 20e6,        # keep stocks with >= NT$20m of blocks in window
    "significant": 300e6,     # flag >= NT$300m
    "source": "TWSE daily block-trade disclosures (single-security deals)",
}


def _closes(ds):
    """{code: closing price} for one YYYYMMDD from the MI_INDEX quotes file."""
    quotes = {}
    try:
        q = json.loads(X.get(QUOTES_URL.format(d=ds)))
        for t in q.get("tables", []):
            f = t.get("fields") or []
            if "Closing Price" in f and "Security Code" in f:
                ic, iclose = f.index("Security Code"), f.index("Closing Price")
                for row in t.get("data", []):
                    quotes[row[ic]] = X.fnum(row[iclose])
    except Exception:
        pass  # premium layer degrades gracefully
    return quotes


def _names():
    """{code: [english name, industry]} from the ISIN registry; cached ~30 days."""
    import os
    import re
    p = X.cache_path(MKT, "names_en.json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 30 * 86400:
        return X.cache_load(MKT, "names_en.json")
    names = {}
    try:
        html = X.get(NAMES_URL, timeout=60, encoding="big5")
        rows = re.findall(r"<tr><td[^>]*>([0-9A-Z]{4,6})　([^<]*?)\s*</td>"
                          r"<td[^>]*>[^<]*</td><td[^>]*>[^<]*</td><td[^>]*>[^<]*</td>"
                          r"<td[^>]*>([^<]*)</td>", html)
        for code, name, industry in rows:
            names[code] = [name.strip(), industry.strip()]
    except Exception:
        return X.cache_load(MKT, "names_en.json") or {}
    if names:
        X.cache_save(MKT, "names_en.json", names)
    return names


def _fetch_day(d: date, throttle=True):
    """Cache {'deals': [...]} or {'deals': []} (holiday) for one date."""
    name = f"blocks_{d.isoformat()}.json"
    cached = X.cache_load(MKT, name)
    if cached is not None:
        return cached
    ds = d.strftime("%Y%m%d")
    day = {"deals": [], "quotes": {}}
    try:
        blk = json.loads(X.get(BLOCK_URL.format(d=ds)))
    except Exception:
        return None  # transient — don't cache failure
    if not (blk.get("stat") == "OK" and blk.get("data")) and d >= date.today():
        return None  # today's file not out yet — retry on the next run
    if blk.get("stat") == "OK" and blk.get("data"):
        if throttle:
            time.sleep(THROTTLE)
        closes = _closes(ds)
        for row in blk["data"]:
            code, _cls, px, vol, val = row[0], row[1], X.fnum(row[2]), X.fnum(row[3]), X.fnum(row[4])
            if not code or not code[0].isdigit() or len(code) > 6:
                continue  # summary rows ("Total") — real codes start with a digit
            day["deals"].append({"symbol": code, "security": "", "px": px,
                                 "qty": int(vol), "value": val,
                                 "close": closes.get(code, 0.0)})
    X.cache_save(MKT, name, day)
    return day


def fetch(backfill_days=0):
    fetched = 0
    for d in X.trading_days_back(backfill_days):
        name = f"blocks_{d.isoformat()}.json"
        if X.cache_load(MKT, name) is not None:
            continue
        if _fetch_day(d) is not None:
            fetched += 1
        time.sleep(THROTTLE)
    return fetched


def records():
    import glob
    import os
    names = _names()
    out = []
    for p in sorted(glob.glob(X.cache_path(MKT, "blocks_*.json"))):
        d = datetime.strptime(os.path.basename(p)[7:17], "%Y-%m-%d").date()
        with open(p) as f:
            day = json.load(f)
        for r in day.get("deals", []):
            if not r["symbol"][0].isdigit() or len(r["symbol"]) > 6:
                continue  # summary rows in caches written before the parse guard
            nm = names.get(r["symbol"])
            r["security"] = nm[0] if nm else r.get("security", "")
            r["sector"] = nm[1] if nm else ""
            r["date"] = d
            r["deals"] = 1
            out.append(r)
    return out


def repair_closes():
    """Backfill closing prices into day caches fetched before the quotes fix."""
    import glob
    import os
    for p in sorted(glob.glob(X.cache_path(MKT, "blocks_*.json"))):
        with open(p) as f:
            day = json.load(f)
        deals = day.get("deals", [])
        if not deals or any(x.get("close") for x in deals):
            continue
        ds = os.path.basename(p)[7:17].replace("-", "")
        closes = _closes(ds)
        if not closes:
            continue
        for x in deals:
            x["close"] = closes.get(x["symbol"], 0.0)
        with open(p, "w") as f:
            json.dump(day, f)
        print(f"  [tw] repaired closes {ds}", flush=True)
        time.sleep(THROTTLE)


def build():
    return E.build_blocks_feed(records(), CFG)


if __name__ == "__main__":
    n = fetch(backfill_days=95)
    feed = build()
    print(f"fetched {n} new days; deals={feed['stats'].get('raw_deals')} "
          f"latest={feed['meta']['latest_date']}")
    print("wrote", X.write_feed(MKT, feed))
