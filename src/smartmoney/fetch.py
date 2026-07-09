"""
Daily NSE fetch — pulls the latest trading day's bulk & block deals straight
from NSE's public archive CSVs and writes one file per (deal_type, date) into
data/raw/daily/. Idempotent: re-running the same day just overwrites that file.

NSE serves these without auth, but is anti-bot, so we warm up a cookie session
and retry. Pure standard library.

Run:  python3 fetch.py            # fetch today's files
      python3 fetch.py --since 2026-06-01   # (placeholder) bounded backfill note
"""

import csv
import io
import os
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from .paths import DAILY_DIR
except ImportError:  # standalone
    from paths import DAILY_DIR

SOURCES = {
    "block": "https://nsearchives.nseindia.com/content/equities/block.csv",
    "bulk": "https://nsearchives.nseindia.com/content/equities/bulk.csv",
}
HOMEPAGE = "https://www.nseindia.com/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/market-data/large-deals",
}

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# Canonical output header (matches the historical export the pipeline expects).
OUT_HEADER = ["Date", "Symbol", "Security Name", "Client Name", "Buy / Sell",
              "Quantity Traded", "Trade Price / Wght. Avg. Price", "Remarks"]


def _opener():
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_CTX),
        urllib.request.HTTPCookieProcessor(),
    )


def _warm(opener):
    """Visit the homepage to pick up cookies NSE expects on archive requests."""
    try:
        req = urllib.request.Request(HOMEPAGE, headers=HEADERS)
        opener.open(req, timeout=15).read()
    except Exception:
        pass


def _get(url, retries=3):
    opener = _opener()
    last = None
    for i in range(retries):
        try:
            if i:
                _warm(opener)
                time.sleep(1.5)
            req = urllib.request.Request(url, headers=HEADERS)
            return opener.open(req, timeout=20).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0)
    raise RuntimeError(f"fetch failed for {url}: {last!r}")


def _val(row, *aliases):
    norm = {k.lower().replace(" ", "").replace(".", ""): v for k, v in row.items()}
    for a in aliases:
        k = a.lower().replace(" ", "").replace(".", "")
        if k in norm:
            return (norm[k] or "").strip()
    return ""


def _rows(text):
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {(k or "").strip(): v for k, v in row.items()}
        sym, date = _val(row, "Symbol"), _val(row, "Date")
        if not sym or not date:
            continue
        out.append({
            "Date": date, "Symbol": sym,
            "Security Name": _val(row, "Security Name"),
            "Client Name": _val(row, "Client Name"),
            "Buy / Sell": _val(row, "Buy / Sell", "Buy/Sell"),
            "Quantity Traded": _val(row, "Quantity Traded", "Quantity"),
            "Trade Price / Wght. Avg. Price": _val(row, "Trade Price / Wght. Avg. Price", "Trade Price"),
            "Remarks": _val(row, "Remarks"),
        })
    return out


def _write(deal_type, date, rows):
    os.makedirs(DAILY_DIR, exist_ok=True)
    iso = datetime.strptime(date, "%d-%b-%Y").date().isoformat()
    path = os.path.join(DAILY_DIR, f"{deal_type}_{iso}.csv")
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_HEADER)
        w.writeheader()
        w.writerows(rows)
    return path, iso


def fetch():
    written = []
    for deal_type, url in SOURCES.items():
        try:
            rows = _rows(_get(url))
        except Exception as e:  # noqa: BLE001
            print(f"  [{deal_type}] ERROR {e}")
            continue
        by_date = defaultdict(list)
        for r in rows:
            by_date[r["Date"]].append(r)
        for date, drows in by_date.items():
            path, iso = _write(deal_type, date, drows)
            written.append((deal_type, iso, len(drows)))
            print(f"  [{deal_type}] {iso}: {len(drows)} deals -> {os.path.relpath(path, HERE)}")
    if not written:
        print("  nothing fetched (holiday / market closed / NSE unreachable)")
    return written


if __name__ == "__main__":
    print(f"NSE fetch @ {datetime.now().isoformat(timespec='seconds')}")
    fetch()
