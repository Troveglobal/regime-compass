"""
NSDL FPI sector-wise data — fortnightly (15th + month-end since 2012).

Where FPI money is moving at the sector level: assets under custody and net
investment per sector per fortnight, equity sleeve, INR crore. This is the
only sector-level FII disclosure that exists — 15-day cadence, no per-stock
detail (that's the quarterly shareholding layer).

Report filenames are hand-typed and irregular (typos live in the archive), so
the selection page's dropdown is always scraped for the real URLs — never
construct filenames. Emits out/feed_fpisectors.json.
"""

import json
import os
import re
from datetime import datetime

try:
    from .flows import _get  # same NSE-ish fetch plumbing (plain UA works here)
except ImportError:
    from flows import _get

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "nsdl")
OUT = os.path.join(HERE, "out", "feed_fpisectors.json")

ROOT = "https://www.fpi.nsdl.co.in"
INDEX = ROOT + "/web/Reports/FPI_Fortnightly_Selection.aspx"
SINCE = "2024-01-01"  # current sector classification + column era

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _num(s):
    s = (s or "").replace(",", "").replace("&nbsp;", "").strip()
    if not s or s in ("-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _report_date(path):
    """FIIInvestSector_June302026.html -> date; None if unparseable."""
    m = re.search(r"_([A-Za-z]+)(\d{1,2})(\d{4})\.html?$", path)
    if not m:
        return None
    mon = MONTHS.get(m.group(1).lower()[:3])
    if not mon:
        return None
    try:
        return datetime(int(m.group(3)), mon, int(m.group(2))).date()
    except ValueError:
        return None


def _list_reports():
    html = _get(INDEX)
    out = []
    for val in re.findall(r'<option value="~/([^"]+)"', html):
        d = _report_date(val)
        if d and d.isoformat() >= SINCE:
            out.append((d.isoformat(), ROOT + "/" + val))
    return sorted(out)


def _parse_report(html):
    """-> {sector: {auc_start, net1, net2, auc_end}} equity sleeve, INR cr."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    out = {}
    for r in rows:
        cells = [re.sub(r"<[^>]+>|&nbsp;", " ", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(cells) < 10 or not cells[0].strip().isdigit():
            continue
        n = (len(cells) - 2) // 8  # sub-block width (INR+USD per 4 blocks)
        if n < 1:
            continue
        import html as _h
        sector = _h.unescape(re.sub(r"\s+", " ", cells[1]))
        vals = cells[2:]
        out[sector] = {
            "auc_start": _num(vals[0]),
            "net1": _num(vals[2 * n]),
            "net2": _num(vals[4 * n]),
            "auc_end": _num(vals[6 * n]),
        }
    return out


def fetch():
    os.makedirs(DATA, exist_ok=True)
    new = 0
    for iso, url in _list_reports():
        path = os.path.join(DATA, f"sector_{iso}.json")
        if os.path.exists(path):
            continue
        try:
            data = _parse_report(_get(url, retries=2))
        except Exception as e:  # noqa: BLE001
            print(f"  [nsdl] {iso} failed: {e}", flush=True)
            continue
        if data:
            with open(path, "w") as f:
                json.dump(data, f)
            new += 1
    print(f"  [nsdl] {new} new fortnightly reports", flush=True)
    return new


def build():
    import glob
    import html as _h
    series = []
    for p in sorted(glob.glob(os.path.join(DATA, "sector_*.json"))):
        with open(p) as f:
            snap = json.load(f)
        # heal caches written before entity unescaping
        series.append({"date": os.path.basename(p)[7:17],
                       "sectors": {_h.unescape(k): v for k, v in snap.items()}})
    if not series:
        feed = {"meta": {}, "latest": None, "timeline": []}
    else:
        latest = series[-1]
        sectors = []
        for name, v in latest["sectors"].items():
            if name.lower() in ("sovereign", "others") or v["auc_end"] <= 0:
                continue
            net = v["net1"] + v["net2"]
            sectors.append({
                "sector": name, "auc": round(v["auc_end"]),
                "net": round(net),
                "net_pct_auc": round(net / v["auc_start"] * 100, 2) if v["auc_start"] else None,
            })
        sectors.sort(key=lambda s: -s["net"])
        # per-sector net flow across all cached fortnights (for trend sparklines)
        names = [s["sector"] for s in sectors]
        timeline = []
        for snap in series:
            row = {"date": snap["date"]}
            for nm in names:
                v = snap["sectors"].get(nm)
                row[nm] = round(v["net1"] + v["net2"]) if v else None
            timeline.append(row)
        feed = {
            "meta": {
                "generated": datetime.now().isoformat(timespec="seconds"),
                "period_end": latest["date"],
                "n_reports": len(series),
                "source": "NSDL fortnightly FPI sector-wise data (equity sleeve, INR cr). "
                          "Published every 15th and month-end, ~3-5 day lag.",
            },
            "latest": {"date": latest["date"], "sectors": sectors},
            "timeline": timeline,
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(feed, f, indent=1)
    return feed


def refresh():
    fetch()
    feed = build()
    print(f"  [nsdl] feed: {feed['meta'].get('n_reports', 0)} fortnights, "
          f"latest {feed['meta'].get('period_end')}", flush=True)
    return True


if __name__ == "__main__":
    refresh()
    feed = json.load(open(OUT))
    if feed.get("latest"):
        for s in feed["latest"]["sectors"][:6]:
            print(s["sector"], "| net", s["net"], "cr | AUC", s["auc"], "cr")
