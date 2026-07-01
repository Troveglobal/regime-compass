"""
India sector heatmap — daily 1D / 1W / 1M performance of the Nifty sectoral
indices, straight from NSE's index snapshot (the same feed SmartFlow already
pulls). Writes out/sectors.json, served at /api/sectors and refreshed daily.

Pure standard library; reuses the SmartFlow price fetcher for index closes.
"""

import json
import logging
import os
from datetime import date, timedelta

try:
    from ..smartmoney import prices as P
except ImportError:  # standalone
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "..", "smartmoney"))
    import prices as P  # type: ignore

log = logging.getLogger("regime_compass")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out", "sectors.json")

# NSE index name -> short display label. Order = display order.
SECTORS = [
    ("Nifty Bank", "Bank"),
    ("Nifty Financial Services", "Financials"),
    ("Nifty Private Bank", "Pvt Bank"),
    ("Nifty PSU Bank", "PSU Bank"),
    ("Nifty IT", "IT"),
    ("Nifty Auto", "Auto"),
    ("Nifty Pharma", "Pharma"),
    ("Nifty Healthcare Index", "Healthcare"),
    ("Nifty FMCG", "FMCG"),
    ("Nifty Consumer Durables", "Consumer Durables"),
    ("Nifty Metal", "Metal"),
    ("Nifty Energy", "Energy"),
    ("Nifty Oil & Gas", "Oil & Gas"),
    ("Nifty Realty", "Realty"),
    ("Nifty Media", "Media"),
    ("Nifty Infrastructure", "Infra"),
    ("Nifty Commodities", "Commodities"),
]
BENCHMARK = "Nifty 50"


def _ret(a, b):
    return round((a - b) / b * 100, 2) if (a and b) else None


def build() -> dict:
    today = date.today()
    ld, latest = P.closes_on_or_before("idx", today)
    if not latest:
        raise RuntimeError("no NSE index data available")
    _, prev = P.closes_on_or_before("idx", ld - timedelta(days=1))
    _, wk = P.closes_on_or_before("idx", ld - timedelta(days=7))
    _, mo = P.closes_on_or_before("idx", ld - timedelta(days=30))
    prev, wk, mo = prev or {}, wk or {}, mo or {}

    rows = []
    for name, label in SECTORS:
        c = latest.get(name)
        if not c:
            continue
        rows.append({
            "name": name, "label": label, "close": round(c, 1),
            "d1": _ret(c, prev.get(name)),
            "w1": _ret(c, wk.get(name)),
            "m1": _ret(c, mo.get(name)),
        })

    bench = None
    b = latest.get(BENCHMARK)
    if b:
        bench = {"name": BENCHMARK, "label": "Nifty 50", "close": round(b, 1),
                 "d1": _ret(b, prev.get(BENCHMARK)), "w1": _ret(b, wk.get(BENCHMARK)),
                 "m1": _ret(b, mo.get(BENCHMARK))}

    return {"as_of": str(ld), "benchmark": bench, "sectors": rows}


def refresh() -> bool:
    try:
        data = build()
    except Exception as e:  # noqa: BLE001
        log.warning("[sectors] build failed: %s", e)
        return False
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2)
    log.info("[sectors] wrote %s sectors as_of %s", len(data["sectors"]), data["as_of"])
    return True


if __name__ == "__main__":
    refresh()
    print(json.dumps(json.load(open(OUT)), indent=2)[:600])
