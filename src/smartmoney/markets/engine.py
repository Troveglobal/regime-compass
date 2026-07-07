"""
Feed builder shared by the global smart-money markets.

Two flavours, depending on what the exchange discloses:

  blocks — anonymous large negotiated/block deals (Taiwan, Indonesia).
           Record: {date(date), symbol, security, px, qty, value, close, deals}
           value/close optional-zero tolerant; deals = trade count that day.

  deals  — named-entity transactions (US insiders; India has its own legacy
           pipeline). Record: {date(date), symbol, security, entity, etype,
           side("BUY"/"SELL"), qty, px, value(+ve magnitude)}

Both emit the same top-level shape (meta / stats / timeline / history /
today / week / month) so the frontend renderer is shared.
"""

from collections import defaultdict
from datetime import datetime, timedelta

WEEK_DAYS = 7
MONTH_DAYS = 30
TIMELINE_DAYS = 95


def _u(cfg, v):
    return round(v / cfg["unit_div"], 1)


def _meta(cfg, records, flavor):
    dates = [r["date"] for r in records]
    return {
        "market": cfg["market"], "market_name": cfg["market_name"], "flavor": flavor,
        "currency": cfg["currency"], "unit": cfg["unit"],
        "generated": datetime.now().isoformat(timespec="seconds"),
        "latest_date": str(max(dates)) if dates else None,
        "earliest_date": str(min(dates)) if dates else None,
        "min_value": _u(cfg, cfg["min_value"]),
        "significant": _u(cfg, cfg["significant"]),
        "source": cfg["source"],
    }


def _anchor(records, latest, cfg):
    """Most recent day with material activity, so 'today' is never empty."""
    active = sorted({r["date"] for r in records if r["value"] >= cfg["min_value"]})
    return active[-1] if active else latest


# --- blocks flavour ---------------------------------------------------------
def _blocks_window(records, cfg, start, end, today=False):
    per = defaultdict(lambda: {"value": 0.0, "qty": 0, "deals": 0, "security": "",
                               "sector": "", "pxs": [], "prems": [], "close": 0.0,
                               "dates": set()})
    all_dates = set()
    for r in records:
        if not (start <= r["date"] <= end):
            continue
        all_dates.add(r["date"])
        p = per[r["symbol"]]
        p["value"] += r["value"]
        p["qty"] += r["qty"]
        p["deals"] += r.get("deals", 1)
        p["dates"].add(r["date"])
        p["security"] = r["security"] or p["security"]
        p["sector"] = r.get("sector") or p["sector"]
        p["close"] = r.get("close") or p["close"]
        if r.get("px") and r.get("qty"):
            p["pxs"].append((r["qty"], r["px"]))
        if r.get("px") and r.get("close"):
            p["prems"].append((r["value"], (r["px"] - r["close"]) / r["close"] * 100))
    stocks = []
    for sym, p in per.items():
        if p["value"] < cfg["min_value"]:
            continue
        q = sum(w for w, _ in p["pxs"])
        avg = sum(w * x for w, x in p["pxs"]) / q if q else None
        # value-weighted premium of each print vs its own day's close
        pw = sum(w for w, _ in p["prems"])
        prem = round(sum(w * x for w, x in p["prems"]) / pw, 1) if pw else None
        if prem is not None and abs(prem) > 40:
            prem = None  # blocks print near market; beyond this it's a data mismatch
        stocks.append({
            "symbol": sym, "security": p["security"], "sector": p["sector"],
            "value": _u(cfg, p["value"]), "deals": p["deals"],
            "days_active": len(p["dates"]),
            "avg_px": round(avg, 2) if avg else None,
            "prem_pct": prem, "significant": p["value"] >= cfg["significant"],
        })
    stocks.sort(key=lambda s: -s["value"])
    w = {
        "stocks": stocks,
        "kpi": {
            "total_value": round(sum(s["value"] for s in stocks), 1),
            "n_deals": sum(s["deals"] for s in stocks),
            "n_stocks": len(stocks),
            "top_stock": stocks[0]["symbol"] if stocks else None,
            "sessions": len(all_dates),
        },
    }
    if today:
        w["date"] = str(end)
    else:
        w["from"], w["to"] = str(start), str(end)
    return w


def build_blocks_feed(records, cfg):
    if not records:
        return {"meta": _meta(cfg, [], "blocks"), "stats": {}, "timeline": [],
                "history": [], "today": None, "week": None, "month": None}
    latest = max(r["date"] for r in records)
    anchor = _anchor(records, latest, cfg)

    tl = defaultdict(lambda: {"value": 0.0, "deals": 0})
    for r in records:
        if r["date"] >= latest - timedelta(days=TIMELINE_DAYS):
            tl[r["date"]]["value"] += r["value"]
            tl[r["date"]]["deals"] += r.get("deals", 1)
    months = defaultdict(float)
    for r in records:
        months[f"{r['date'].year}-{r['date'].month:02d}"] += r["value"]

    return {
        "meta": _meta(cfg, records, "blocks"),
        "stats": {
            "raw_deals": sum(r.get("deals", 1) for r in records),
            "distinct_symbols": len({r["symbol"] for r in records}),
            "days": len({r["date"] for r in records}),
        },
        "timeline": [{"date": str(d), "value": _u(cfg, v["value"]), "deals": v["deals"]}
                     for d, v in sorted(tl.items())],
        "history": [{"month": m, "value": _u(cfg, v)} for m, v in sorted(months.items())],
        "today": _blocks_window(records, cfg, anchor, anchor, today=True),
        "week": _blocks_window(records, cfg, latest - timedelta(days=WEEK_DAYS - 1), latest),
        "month": _blocks_window(records, cfg, latest - timedelta(days=MONTH_DAYS - 1), latest),
    }


# --- deals flavour (named entities, two sides) -------------------------------
def _deals_side(records, cfg, start, end, side):
    per = defaultdict(lambda: {"actors": defaultdict(float), "security": ""})
    for r in records:
        if r["side"] != side or not (start <= r["date"] <= end):
            continue
        p = per[r["symbol"]]
        p["actors"][(r["entity"], r["etype"])] += r["value"]
        p["security"] = r["security"] or p["security"]
    stocks = []
    for sym, p in per.items():
        total = sum(p["actors"].values())
        if total < cfg["min_value"]:
            continue
        stocks.append({
            "symbol": sym, "security": p["security"], "value": _u(cfg, total),
            "n_actors": len(p["actors"]),
            "actors": [{"entity": e, "etype": t, "value": _u(cfg, v)}
                       for (e, t), v in sorted(p["actors"].items(), key=lambda kv: -kv[1])],
            "significant": total >= cfg["significant"],
        })
    stocks.sort(key=lambda s: -s["value"])

    ents = defaultdict(lambda: {"value": 0.0, "etype": "", "symbols": set()})
    for r in records:
        if r["side"] != side or not (start <= r["date"] <= end):
            continue
        x = ents[r["entity"]]
        x["value"] += r["value"]
        x["etype"] = r["etype"]
        x["symbols"].add(r["symbol"])
    top = [{"entity": e, "etype": v["etype"], "value": _u(cfg, v["value"]),
            "n_names": len(v["symbols"])}
           for e, v in ents.items() if v["value"] >= cfg["min_value"]]
    top.sort(key=lambda x: -x["value"])

    return {
        "stocks": stocks, "top_entities": top[:12],
        "kpi": {
            "total_value": round(sum(s["value"] for s in stocks), 1),
            "n_stocks": len(stocks), "n_entities": len(ents),
            "top_stock": stocks[0]["symbol"] if stocks else None,
        },
    }


def _deals_window(records, cfg, start, end, today=False):
    w = {"buy": _deals_side(records, cfg, start, end, "BUY"),
         "sell": _deals_side(records, cfg, start, end, "SELL")}
    if today:
        w["date"] = str(end)
    else:
        w["from"], w["to"] = str(start), str(end)
    return w


def build_deals_feed(records, cfg):
    if not records:
        return {"meta": _meta(cfg, [], "deals"), "stats": {}, "timeline": [],
                "history": [], "today": None, "week": None, "month": None}
    latest = max(r["date"] for r in records)
    anchor = _anchor(records, latest, cfg)

    tl = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    for r in records:
        if r["date"] >= latest - timedelta(days=TIMELINE_DAYS):
            tl[r["date"]]["buy" if r["side"] == "BUY" else "sell"] += r["value"]
    months = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
    for r in records:
        months[f"{r['date'].year}-{r['date'].month:02d}"]["buy" if r["side"] == "BUY" else "sell"] += r["value"]

    return {
        "meta": _meta(cfg, records, "deals"),
        "stats": {
            "raw_deals": len(records),
            "distinct_symbols": len({r["symbol"] for r in records}),
            "distinct_entities": len({r["entity"] for r in records}),
            "days": len({r["date"] for r in records}),
        },
        "timeline": [{"date": str(d), "buy": _u(cfg, v["buy"]), "sell": _u(cfg, v["sell"]),
                      "net": _u(cfg, v["buy"] - v["sell"])} for d, v in sorted(tl.items())],
        "history": [{"month": m, "buy": _u(cfg, v["buy"]), "sell": _u(cfg, v["sell"]),
                     "net": _u(cfg, v["buy"] - v["sell"])} for m, v in sorted(months.items())],
        "today": _deals_window(records, cfg, anchor, anchor, today=True),
        "week": _deals_window(records, cfg, latest - timedelta(days=WEEK_DAYS - 1), latest),
        "month": _deals_window(records, cfg, latest - timedelta(days=MONTH_DAYS - 1), latest),
    }
