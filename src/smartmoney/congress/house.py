"""
House periodic transaction reports from the Clerk's bulk disclosure files.

No anti-bot: one ZIP per year (index XML of every filing, updated daily),
then one PDF per PTR. E-filed PTRs (DocID starting "2") extract cleanly with
pdfplumber; scanned paper filings (DocID "8"/"9") are skipped.

Cache: data/congress/house/<docid>.json per filing.
"""

import io
import json
import os
import re
import time
import zipfile
from datetime import datetime

try:
    from ..markets import common as X
    from .senate import parse_amount
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "markets"))
    import common as X
    from senate import parse_amount

MKT = "congress"
YEARS = [2025, 2026]
ZIP_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{y}FD.zip"
PDF_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{y}/{doc}.pdf"
THROTTLE = 0.8

# "... - Common Stock (AMZN) [ST] ... P|S|E 06/12/2026 06/13/2026 $1,001 - $15,000"
TXN_RE = re.compile(
    r"\(([A-Z][A-Z.\-]{0,7})\)\s*(?:\[([A-Z]{2})\])?\s*"
    r"(P|S|E)(?:\s*\((?:partial|full)\))?\s+"
    r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+"
    r"(\$[\d,]+(?:\s*-\s*\$[\d,]+)?)")


def _get_bytes(url, retries=3):
    import urllib.request
    last = None
    for i in range(retries):
        try:
            if i:
                time.sleep(1.5 * i)
            req = urllib.request.Request(url, headers={"User-Agent": X.UA})
            return urllib.request.urlopen(req, timeout=60).read()
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{url}: {last!r}")


def _index(year):
    """[(last, first, state_district, filed, docid)] for the year's PTR filings."""
    xml = zipfile.ZipFile(io.BytesIO(_get_bytes(ZIP_URL.format(y=year)))) \
        .read(f"{year}FD.xml").decode("utf-8", "replace")
    out = []
    for m in re.finditer(r"<Member>(.*?)</Member>", xml, re.S):
        block = m.group(1)
        get = lambda tag: (re.search(f"<{tag}>(.*?)</{tag}>", block) or [None, ""])[1].strip()
        if get("FilingType") != "P":
            continue
        out.append((get("Last"), get("First"), get("StateDst"), get("FilingDate"), get("DocID")))
    return out


def clean_asset(window):
    """Clean tail of the text window before a ticker parenthesis: drop prior-row
    fragments (dates, $ ranges, class tags, nulls, filing-status codes)."""
    window = window.replace("\x00", " ")
    parts = re.split(r"\$[\d,]+(?:\s*-\s*\$[\d,]+)?|\d{2}/\d{2}/\d{4}|\[[A-Z]{2}\]|\([A-Z.\-]{1,8}\)", window)
    asset = parts[-1]
    if ":" in asset[:40]:  # "? F S : New <asset>" filing-status column fragments
        asset = asset.rsplit(":", 1)[-1]
    asset = re.sub(r"^\s*(?:New\s+)?", "", asset)
    return re.sub(r"^(?:[A-Z?]{1,2}\s+)*(?:\((?:partial|full)\)\s*)?", "", asset).strip(" -:?")


def _parse_pdf(pdf_bytes):
    import pdfplumber
    txns = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    text = re.sub(r"\s+", " ", text)
    for m in TXN_RE.finditer(text):
        ticker, aclass, code, tdate, _ndate, amount = m.groups()
        if aclass != "ST":
            continue  # stocks only, tag required (skips options/bonds and stray "(X)" in trust names)
        asset = clean_asset(text[max(0, m.start() - 90):m.start()])
        try:
            d = datetime.strptime(tdate, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        lo, hi = parse_amount(amount)
        txns.append({"date": d, "owner": "", "symbol": ticker.upper(), "asset": asset,
                     "side": "BUY" if code == "P" else "SELL", "lo": lo, "hi": hi})
    return txns


def fetch():
    os.makedirs(X.cache_path(MKT, "house"), exist_ok=True)
    new = 0
    for year in YEARS:
        try:
            idx = _index(year)
        except Exception as e:  # noqa: BLE001
            print(f"  [house] {year} index failed: {e}", flush=True)
            continue
        for last, first, dst, filed, doc in idx:
            if not doc.startswith("2"):
                continue  # scanned paper filing
            path = os.path.join(X.cache_path(MKT, "house"), f"{doc}.json")
            if os.path.exists(path):
                continue
            try:
                txns = _parse_pdf(_get_bytes(PDF_URL.format(y=year, doc=doc)))
            except Exception as e:  # noqa: BLE001
                print(f"  [house] {doc} failed: {e}", flush=True)
                continue
            with open(path, "w") as f:
                json.dump({"first": first, "last": last, "filed": filed,
                           "state_dst": dst, "chamber": "House", "txns": txns}, f)
            new += 1
            time.sleep(THROTTLE)
        print(f"  [house] {year}: {len(idx)} PTR filings indexed", flush=True)
    print(f"  [house] {new} new filings fetched", flush=True)
    return new


def filings():
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(X.cache_path(MKT, "house"), "*.json"))):
        with open(p) as f:
            out.append(json.load(f))
    return out


if __name__ == "__main__":
    fetch()
    fs = filings()
    n = sum(len(f["txns"]) for f in fs)
    print(f"{len(fs)} filings, {n} stock transactions")
    for f in fs:
        if f["txns"]:
            print(f["first"], f["last"], f["state_dst"], "->", f["txns"][0])
            break
