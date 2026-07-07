"""
India FII/DII flow layers — the market-wide context around the deal tracker:

  cash   — daily FII/DII provisional net cash flows (whole-market, one number
           each; published every trading evening). The API serves only the
           latest day, so history accumulates in data/flows/cash.json.
  derivs — participant-wise F&O open interest (FII/DII/Client/Pro), daily CSV
           from NSE archives, backfillable. FII index-futures and index-option
           positioning is the classic "FII positioning" read.

Emits out/feed_inflows.json, served at /api/smartmoney/inflows. Pure stdlib,
same anti-bot plumbing as the NSE deal fetcher.
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
DATA = os.path.join(HERE, "data", "flows")
OUT = os.path.join(HERE, "out", "feed_inflows.json")

CASH_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
OI_URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d}.csv"
HOMEPAGE = "https://www.nseindia.com/"
TIMELINE_DAYS = 95

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "*/*", "Referer": "https://www.nseindia.com/"}


def _opener():
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=_CTX),
                                       urllib.request.HTTPCookieProcessor())


def _get(url, warm=False, retries=3):
    op = _opener()
    last = None
    for i in range(retries):
        try:
            if warm or i:
                try:
                    op.open(urllib.request.Request(HOMEPAGE, headers=_H), timeout=15).read()
                except Exception:
                    pass
                time.sleep(1.0 * i)
            return op.open(urllib.request.Request(url, headers=_H), timeout=25).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"{url}: {last!r}")


def _fnum(s):
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


# --- daily cash flows (accumulating store) ---------------------------------
def fetch_cash():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "cash.json")
    store = {}
    if os.path.exists(path):
        with open(path) as f:
            store = json.load(f)
    try:
        rows = json.loads(_get(CASH_URL, warm=True))
    except Exception as e:  # noqa: BLE001
        print(f"  [flows] cash fetch failed: {e}", flush=True)
        return store
    for r in rows:
        d = datetime.strptime(r["date"], "%d-%b-%Y").date().isoformat()
        cat = "fii" if "FII" in r["category"].upper() else "dii"
        day = store.setdefault(d, {})
        day[cat] = {"buy": _fnum(r["buyValue"]), "sell": _fnum(r["sellValue"]),
                    "net": _fnum(r["netValue"])}
    with open(path, "w") as f:
        json.dump(store, f)
    return store


# --- participant-wise F&O open interest -------------------------------------
def _fetch_oi_day(d: date):
    """Cache the participant OI matrix for one date; None on transient failure."""
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, f"oi_{d.isoformat()}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    try:
        text = _get(OI_URL.format(d=d.strftime("%d%m%Y")))
    except Exception as e:
        if "404" in str(e) and d < date.today():
            day = {}  # holiday — file never exists
            with open(path, "w") as f:
                json.dump(day, f)
            return day
        return None
    lines = [l for l in text.splitlines() if l.strip()]
    day = {}
    for row in csv.reader(io.StringIO("\n".join(lines[1:]))):  # skip title line
        if not row or row[0].strip() in ("", "Client Type"):
            header = [c.strip() for c in row] if row and row[0].strip() == "Client Type" else None
            if header:
                day["_cols"] = header[1:]
            continue
        who = row[0].strip()
        if who in ("Client", "DII", "FII", "Pro", "TOTAL"):
            day[who] = [_fnum(c) for c in row[1:]]
    with open(path, "w") as f:
        json.dump(day, f)
    return day


def fetch_oi(backfill_days=0):
    fetched = 0
    end = date.today()
    for i in range(backfill_days, -1, -1):
        d = end - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if os.path.exists(os.path.join(DATA, f"oi_{d.isoformat()}.json")):
            continue
        if _fetch_oi_day(d) is not None:
            fetched += 1
        time.sleep(0.8)
    return fetched


def _oi_series():
    import glob
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "oi_*.json"))):
        with open(p) as f:
            day = json.load(f)
        if not day or "FII" not in day or "_cols" not in day:
            continue
        cols = {c: i for i, c in enumerate(day["_cols"])}
        def v(who, col):
            row = day.get(who)
            return row[cols[col]] if row and col in cols and cols[col] < len(row) else 0

        d = os.path.basename(p)[3:13]
        fut_l, fut_s = v("FII", "Future Index Long"), v("FII", "Future Index Short")
        out.append({
            "date": d,
            "fii": {
                "fut_idx_long": fut_l, "fut_idx_short": fut_s,
                "fut_idx_net": fut_l - fut_s,
                "long_pct": round(fut_l / (fut_l + fut_s) * 100, 1) if fut_l + fut_s else None,
                "opt_idx_call_long": v("FII", "Option Index Call Long"),
                "opt_idx_put_long": v("FII", "Option Index Put Long"),
                "opt_idx_call_short": v("FII", "Option Index Call Short"),
                "opt_idx_put_short": v("FII", "Option Index Put Short"),
                "fut_stk_net": v("FII", "Future Stock Long") - v("FII", "Future Stock Short       ")
                               or v("FII", "Future Stock Long") - v("FII", "Future Stock Short"),
            },
            "client_fut_idx_net": v("Client", "Future Index Long") - v("Client", "Future Index Short"),
            "pro_fut_idx_net": v("Pro", "Future Index Long") - v("Pro", "Future Index Short"),
            "dii_fut_idx_net": v("DII", "Future Index Long") - v("DII", "Future Index Short"),
        })
    return out


def build():
    with open(os.path.join(DATA, "cash.json")) as f:
        cash = json.load(f)
    cutoff = (date.today() - timedelta(days=TIMELINE_DAYS)).isoformat()
    cash_tl = [{"date": d, "fii": v.get("fii", {}).get("net"), "dii": v.get("dii", {}).get("net")}
               for d, v in sorted(cash.items()) if d >= cutoff]
    oi = _oi_series()
    oi = [x for x in oi if x["date"] >= cutoff]

    latest_oi = oi[-1] if oi else None
    prev_oi = oi[-2] if len(oi) > 1 else None
    latest_cash = cash_tl[-1] if cash_tl else None

    feed = {
        "meta": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "cash_latest": latest_cash["date"] if latest_cash else None,
            "oi_latest": latest_oi["date"] if latest_oi else None,
            "source": "NSE daily FII/DII provisional flows (cash, ₹ cr) + participant-wise "
                      "F&O open interest (contracts). Provisional figures, T+0 evening.",
        },
        "cash": {"timeline": cash_tl, "latest": latest_cash},
        "derivs": {
            "timeline": [{"date": x["date"], "net": x["fii"]["fut_idx_net"],
                          "long_pct": x["fii"]["long_pct"]} for x in oi],
            "latest": latest_oi,
            "prev": prev_oi,
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(feed, f, indent=1)
    return feed


def refresh(backfill_days=5):
    fetch_cash()
    fetch_oi(backfill_days=backfill_days)
    feed = build()
    print(f"  [flows] cash latest {feed['meta']['cash_latest']}, "
          f"OI latest {feed['meta']['oi_latest']}", flush=True)
    return True


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 95
    fetch_cash()
    print("backfilled OI days:", fetch_oi(backfill_days=days))
    feed = build()
    print("cash days:", len(feed["cash"]["timeline"]), "| oi days:", len(feed["derivs"]["timeline"]))
    if feed["derivs"]["latest"]:
        f = feed["derivs"]["latest"]["fii"]
        print("FII idx futures:", f["fut_idx_long"], "L /", f["fut_idx_short"], "S →",
              f["long_pct"], "% long")
