"""
Senate periodic transaction reports (PTRs) from efdsearch.senate.gov.

The site sits behind Akamai TLS fingerprinting: plain urllib/requests get 403,
curl_cffi with Chrome impersonation passes. Flow: accept the prohibition
agreement (CSRF form) -> DataTables JSON search for report type 11 (PTR) ->
parse each e-filed report's HTML table. Scanned paper filings are skipped.

Cache: data/congress/senate/<uuid>.json per filing + an index of seen UUIDs.
"""

import json
import os
import re
import time
from datetime import datetime

try:
    from ..markets import common as X
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "markets"))
    import common as X

MKT = "congress"
ROOT = "https://efdsearch.senate.gov"
SINCE = "01/01/2025 00:00:00"
THROTTLE = 1.2


def _session():
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    r = s.get(f"{ROOT}/search/home/", timeout=30)
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
    if not m:
        raise RuntimeError("eFD: no CSRF token on agreement page")
    s.post(f"{ROOT}/search/home/",
           data={"prohibition_agreement": "1", "csrfmiddlewaretoken": m.group(1)},
           headers={"Referer": f"{ROOT}/search/home/"}, timeout=30)
    return s


def _list_ptrs(s):
    """All PTR filings since SINCE: [(first, last, filed_date, uuid)]."""
    out, start, page = [], 0, 100
    token = s.cookies.get("csrftoken", "")
    while True:
        r = s.post(f"{ROOT}/search/report/data/", data={
            "start": str(start), "length": str(page),
            "report_types": "[11]", "filer_types": "[]",
            "submitted_start_date": SINCE, "submitted_end_date": "",
            "candidate_state": "", "senator_state": "", "office_id": "",
            "first_name": "", "last_name": "",
        }, headers={"Referer": f"{ROOT}/search/", "X-CSRFToken": token}, timeout=30)
        data = r.json()
        for first, last, who, link, filed in data.get("data", []):
            if "(Senator)" not in who:
                continue  # skip candidates / former filers
            m = re.search(r"/search/view/(ptr|paper)/([0-9a-f-]+)/", link)
            if not m or m.group(1) != "ptr":
                continue  # paper filings are scanned images — unparseable
            out.append((first.strip(), last.strip(), filed.strip(), m.group(2)))
        start += page
        if start >= data.get("recordsTotal", 0):
            break
        time.sleep(0.5)
    return out


AMOUNT_RE = re.compile(r"\$([\d,]+)(?:\s*-\s*\$([\d,]+))?")


def parse_amount(txt):
    m = AMOUNT_RE.search(txt or "")
    if not m:
        return 0, 0
    lo = int(m.group(1).replace(",", ""))
    hi = int(m.group(2).replace(",", "")) if m.group(2) else lo
    return lo, hi


def _parse_ptr(html):
    """Transaction rows out of an e-filed PTR page."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    txns = []
    for row in rows:
        cells = [re.sub(r"<[^>]+>", " ", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        if len(cells) < 8:
            continue
        # #, transaction_date, owner, ticker, asset_name, asset_type, type, amount[, comment]
        _, tdate, owner, ticker, asset, atype, ttype, amount = cells[:8]
        if "stock" not in atype.lower() or ticker in ("--", ""):
            continue
        side = "BUY" if "purchase" in ttype.lower() else "SELL" if "sale" in ttype.lower() else None
        if not side:
            continue
        try:
            d = datetime.strptime(tdate, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        lo, hi = parse_amount(amount)
        txns.append({"date": d, "owner": owner, "symbol": ticker.upper(),
                     "asset": asset, "side": side, "lo": lo, "hi": hi})
    return txns


def fetch():
    """Pull any unseen PTR filings; returns number of new filings cached."""
    os.makedirs(X.cache_path(MKT, "senate"), exist_ok=True)
    s = _session()
    filings = _list_ptrs(s)
    new = 0
    for first, last, filed, uuid in filings:
        path = os.path.join(X.cache_path(MKT, "senate"), f"{uuid}.json")
        if os.path.exists(path):
            continue
        try:
            r = s.get(f"{ROOT}/search/view/ptr/{uuid}/", timeout=30)
            txns = _parse_ptr(r.text)
        except Exception as e:  # noqa: BLE001
            print(f"  [senate] {uuid} failed: {e}", flush=True)
            continue
        with open(path, "w") as f:
            json.dump({"first": first, "last": last, "filed": filed,
                       "chamber": "Senate", "txns": txns}, f)
        new += 1
        time.sleep(THROTTLE)
    print(f"  [senate] {len(filings)} PTRs listed, {new} new fetched", flush=True)
    return new


def filings():
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(X.cache_path(MKT, "senate"), "*.json"))):
        with open(p) as f:
            out.append(json.load(f))
    return out


if __name__ == "__main__":
    fetch()
    fs = filings()
    n = sum(len(f["txns"]) for f in fs)
    print(f"{len(fs)} filings, {n} stock transactions")
    for f in fs[:3]:
        if f["txns"]:
            print(f["first"], f["last"], f["filed"], "->", f["txns"][0])
