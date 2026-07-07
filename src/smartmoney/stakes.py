"""
Quarterly FII stake changes — the stock-level ground truth, ~21-day lag.

Every listed company files its shareholding pattern within 21 days of quarter
end. BSE serves these as clean JSON per (scripcode, quarter); we track the
foreign-institution holding % for the Nifty 500 universe and surface where
FII ownership actually rose or fell — the definitive answer to "which stocks
did FIIs buy", one quarter at a time.

Cache: data/stakes/<scripcode>_<qtr>.json. Emits out/feed_stakes.json.
"""

import csv
import io
import json
import os
import time
from datetime import date, datetime

try:
    from .flows import _get
except ImportError:
    from flows import _get

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "stakes")
OUT = os.path.join(HERE, "out", "feed_stakes.json")

N500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
BSE_MASTER = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
              "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
SHP_URL = ("https://api.bseindia.com/BseIndiaAPI/api/Corp_shpSec_SHPPubShold_ng/w"
           "?SCRIPCODE={code}&QtrCode={qtr}")
BSE_H = {"Referer": "https://www.bseindia.com/"}
THROTTLE = 0.35
QUARTERS_BACK = 5  # latest + 4 prior

# BSE global quarter ids: Jun-2021 = 110 (one per calendar quarter)
def qtr_code(y, q):
    return 110 + (y - 2021) * 4 + (q - 2)


def _labels():
    """[(code, 'Mar 2026'), ...] newest first, for the window we track."""
    today = date.today()
    q = (today.month - 1) // 3 + 1  # current calendar quarter
    y = today.year
    # latest quarter whose END has passed (filings may be partial for ~21 days)
    q -= 1
    if q == 0:
        y, q = y - 1, 4
    out = []
    for _ in range(QUARTERS_BACK):
        out.append((qtr_code(y, q), ["Mar", "Jun", "Sep", "Dec"][q - 1] + f" {y}"))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def _universe():
    """Nifty 500 joined to BSE scripcodes via ISIN; cached weekly."""
    path = os.path.join(DATA, "universe.json")
    os.makedirs(DATA, exist_ok=True)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        with open(path) as f:
            return json.load(f)
    n500 = list(csv.DictReader(io.StringIO(_get(N500_URL))))
    master = _bse_get(BSE_MASTER)
    by_isin = {m["ISIN_NUMBER"]: m["SCRIP_CD"] for m in master if m.get("ISIN_NUMBER")}
    uni = []
    for r in n500:
        code = by_isin.get(r["ISIN Code"])
        if code:
            uni.append({"symbol": r["Symbol"], "name": r["Company Name"],
                        "industry": r["Industry"], "code": code})
    with open(path, "w") as f:
        json.dump(uni, f)
    print(f"  [stakes] universe: {len(uni)}/{len(n500)} Nifty-500 mapped to BSE", flush=True)
    return uni


def _fii_pct(payload):
    """Foreign-institution holding % from a BSE shareholding response."""
    rows = payload.get("Table1") or []
    by_id = {}
    for r in rows:
        fid = str(r.get("Fld_Id", "")).split(".")[0]
        pct = r.get("Fld_TotalPercentageOf_A_B_C2")
        if pct is not None:
            by_id[fid] = float(pct)
    if "10141" in by_id:          # current format: Institutions (Foreign) subtotal
        return by_id["10141"]
    if "10045" in by_id:          # legacy format: FPI row (B1e)
        return by_id["10045"] + by_id.get("10046", 0.0)
    return None


def _bse_get(url, retries=3):
    import ssl
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    h = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
         "Accept": "application/json", "Referer": "https://www.bseindia.com/"}
    last = None
    for i in range(retries):
        try:
            if i:
                time.sleep(1.5 * i)
            req = urllib.request.Request(url, headers=h)
            raw = urllib.request.urlopen(req, timeout=25, context=ctx).read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{url}: {last!r}")


def fetch():
    uni = _universe()
    quarters = _labels()
    todo = [(u, q) for u in uni for q, _ in quarters
            if not os.path.exists(os.path.join(DATA, f"{u['code']}_{q}.json"))]
    print(f"  [stakes] {len(todo)} (stock, quarter) cells to fetch", flush=True)
    done = 0
    for u, q in todo:
        path = os.path.join(DATA, f"{u['code']}_{q}.json")
        try:
            payload = _bse_get(SHP_URL.format(code=u["code"], qtr=q))
            out = {"pct": _fii_pct(payload)} if payload.get("Table1") else {"pct": None}
            with open(path, "w") as f:
                json.dump(out, f)
            done += 1
            if done % 200 == 0:
                print(f"  [stakes] {done}/{len(todo)}", flush=True)
        except Exception:
            pass  # transient — retry next run
        time.sleep(THROTTLE)
    print(f"  [stakes] fetched {done} cells", flush=True)
    return done


def build():
    uni = _universe()
    quarters = _labels()  # newest first
    stocks = []
    for u in uni:
        series = []
        for q, label in quarters:
            path = os.path.join(DATA, f"{u['code']}_{q}.json")
            pct = None
            if os.path.exists(path):
                with open(path) as f:
                    pct = json.load(f).get("pct")
            series.append({"q": label, "pct": pct})
        vals = [s["pct"] for s in series if s["pct"] is not None]
        if len(vals) < 2:
            continue
        newest = next((s["pct"] for s in series if s["pct"] is not None), None)
        older = [s["pct"] for s in series if s["pct"] is not None][1]
        first = vals[-1]
        stocks.append({
            "symbol": u["symbol"], "name": u["name"], "industry": u["industry"],
            "series": series[::-1],  # oldest -> newest for sparklines
            "pct": newest, "delta_q": round(newest - older, 2),
            "delta_full": round(newest - first, 2),
        })
    stocks.sort(key=lambda s: -abs(s["delta_q"]))
    feed = {
        "meta": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "quarters": [l for _, l in quarters][::-1],
            "latest_quarter": quarters[0][1],
            "n_stocks": len(stocks),
            "universe": "Nifty 500",
            "source": "BSE shareholding patterns (quarterly filings, up to 21-day lag). "
                      "Foreign institutional holding % per stock.",
        },
        "stocks": stocks,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(feed, f, indent=1)
    return feed


def refresh():
    try:
        fetch()
    except Exception as e:  # noqa: BLE001
        print(f"  [stakes] fetch failed: {e}", flush=True)
    feed = build()
    print(f"  [stakes] feed: {feed['meta']['n_stocks']} stocks, "
          f"latest {feed['meta']['latest_quarter']}", flush=True)
    return True


if __name__ == "__main__":
    refresh()
    feed = json.load(open(OUT))
    ups = [s for s in feed["stocks"] if s["delta_q"] > 0][:5]
    for s in ups:
        print(s["symbol"], s["industry"][:20], "| FII", s["pct"], "% | QoQ", s["delta_q"])
