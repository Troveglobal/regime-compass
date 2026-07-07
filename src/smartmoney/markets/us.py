"""
US — SEC Form 4 insider deals (the closest analog to NSE bulk deals anywhere:
entity name, role, buy/sell, qty, price, per deal, filed within 2 business days).

Daily flow: one EDGAR form-index request lists the day's Form 4 filings; each
filing's XML is fetched (throttled to SEC's published rate policy) and reduced
to material open-market transactions only — purchases/sales (codes P/S) with
value >= RECORD_FLOOR after netting per (insider, symbol, day). Grants, awards,
option exercises and small trades are discarded, so the per-day cache is tiny.

Cache: data/us/form4_YYYY-MM-DD.json (by FILING date; records carry TRADE date).
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

try:
    from . import common as X
    from . import engine as E
except ImportError:
    import common as X
    import engine as E

MKT = "us"
SEC_UA = {"User-Agent": "RegimeCompass smart-money research adityasahasrabuddhe1998@gmail.com"}
IDX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
DOC_URL = "https://www.sec.gov/Archives/{path}"
WORKERS = 4           # gentle sustained pace — SEC 429-blocks bursty/high-volume clients
PAUSE = 0.12          # per-request politeness delay inside each worker
RECORD_FLOOR = 200e3  # keep insider-day net trades >= $200k

CFG = {
    "market": MKT, "market_name": "United States (SEC insiders)",
    "currency": "USD", "unit": "US$m", "unit_div": 1e6,
    "min_value": 250e3,      # window floor per stock
    "significant": 5e6,      # flag >= $5m
    "source": "SEC Form 4 filings — open-market insider purchases and sales (codes P/S)",
}


def _txt(el, *tags):
    for t in tags:
        n = el.find(t)
        if n is not None:
            v = n.find("value")
            s = (v.text if v is not None else n.text) or ""
            if s.strip():
                return s.strip()
    return ""


def _role(rel):
    if rel is None:
        return "Insider"
    if (_txt(rel, "isTenPercentOwner") or "0") in ("1", "true"):
        return "10% Owner"
    if (_txt(rel, "isOfficer") or "0") in ("1", "true"):
        return _txt(rel, "officerTitle") or "Officer"
    if (_txt(rel, "isDirector") or "0") in ("1", "true"):
        return "Director"
    return "Insider"


def _parse_filing(raw):
    """Full-submission .txt -> list of open-market P/S transactions."""
    m = re.search(r"<ownershipDocument[^>]*>.*?</ownershipDocument>", raw, re.S)
    if not m:
        return []
    try:
        root = ET.fromstring(re.sub(r"&(?![a-zA-Z#])", "&amp;", m.group(0)))
    except ET.ParseError:
        return []
    issuer = root.find("issuer")
    if issuer is None:
        return []
    symbol = _txt(issuer, "issuerTradingSymbol").upper()
    name = _txt(issuer, "issuerName")
    if not symbol or symbol in ("NONE", "N/A"):
        return []
    owner = root.find("reportingOwner")
    who = _txt(owner.find("reportingOwnerId"), "rptOwnerName") if owner is not None else ""
    etype = _role(owner.find("reportingOwnerRelationship") if owner is not None else None)
    out = []
    table = root.find("nonDerivativeTable")
    for tx in table.findall("nonDerivativeTransaction") if table is not None else []:
        code = _txt(tx.find("transactionCoding"), "transactionCode") if tx.find("transactionCoding") is not None else ""
        if code not in ("P", "S"):
            continue
        amt = tx.find("transactionAmounts")
        if amt is None:
            continue
        qty = X.fnum(_txt(amt, "transactionShares"))
        px = X.fnum(_txt(amt, "transactionPricePerShare"))
        d = _txt(tx.find("transactionDate"), "value") or _txt(tx, "transactionDate")
        if not (qty and px and d):
            continue
        out.append({"symbol": symbol, "security": name, "entity": who.title(),
                    "etype": etype, "side": "BUY" if code == "P" else "SELL",
                    "qty": int(qty), "px": px, "value": qty * px, "date": d[:10]})
    return out


def _fetch_day(d: date):
    name = f"form4_{d.isoformat()}.json"
    cached = X.cache_load(MKT, name)
    if cached is not None:
        return cached
    ymd = d.strftime("%Y%m%d")
    q = (d.month - 1) // 3 + 1
    try:
        idx = X.get(IDX_URL.format(y=d.year, q=q, ymd=ymd), headers=SEC_UA)
    except Exception as e:
        if "404" in str(e) and d < date.today():
            idx = ""  # index never exists for holidays — cache the empty day
        else:
            return None  # rate-limited / transient — never cache, retry next run
    paths = []
    for line in idx.splitlines():
        if line.startswith("4 ") or line.startswith("4\t"):
            p = line.split()[-1]
            if p.endswith(".txt"):
                paths.append(p)
    paths = sorted(set(paths))
    if not paths and d >= date.today():
        return None  # today's index not out yet

    failures = []

    def grab(p):
        try:
            time.sleep(PAUSE)
            return _parse_filing(X.get(DOC_URL.format(path=p), headers=SEC_UA, retries=2))
        except Exception:
            failures.append(p)
            return []

    txns = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for res in ex.map(grab, paths):
            txns.extend(res)
    if paths and len(failures) > len(paths) * 0.1:
        return None  # too many failures (likely rate-limited mid-day) — don't cache a partial day

    # net per (insider, symbol, side, trade-date), then apply the floor
    agg = {}
    for t in txns:
        k = (t["entity"], t["symbol"], t["side"], t["date"])
        if k in agg:
            a = agg[k]
            a["value"] += t["value"]
            a["qty"] += t["qty"]
        else:
            agg[k] = dict(t)
    deals = [a for a in agg.values() if a["value"] >= RECORD_FLOOR]
    for a in deals:
        a["px"] = round(a["value"] / a["qty"], 2) if a["qty"] else a["px"]
        a["value"] = round(a["value"], 0)
    day = {"filings": len(paths), "deals": deals}
    X.cache_save(MKT, name, day)
    return day


def fetch(backfill_days=0):
    fetched = 0
    for d in X.trading_days_back(backfill_days):
        if X.cache_load(MKT, f"form4_{d.isoformat()}.json") is not None:
            continue
        got = _fetch_day(d)
        if got is not None:
            fetched += 1
            print(f"  [us] {d}: {got['filings']} filings -> {len(got['deals'])} material deals", flush=True)
    return fetched


def records():
    import glob
    out = []
    for p in sorted(glob.glob(X.cache_path(MKT, "form4_*.json"))):
        with open(p) as f:
            day = json.load(f)
        for r in day.get("deals", []):
            r = dict(r)
            r["date"] = datetime.strptime(r["date"], "%Y-%m-%d").date()
            out.append(r)
    return out


def build():
    return E.build_deals_feed(records(), CFG)


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    n = fetch(backfill_days=days)
    feed = build()
    print(f"fetched {n} new days; deals={feed['stats'].get('raw_deals')} "
          f"latest={feed['meta']['latest_date']}")
    print("wrote", X.write_feed(MKT, feed))
