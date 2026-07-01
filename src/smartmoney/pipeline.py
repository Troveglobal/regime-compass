"""
FII / DII smart-money pipeline.

ingest NSE bulk/block CSVs  ->  canonicalise + classify entities  ->  net out
intraday round-trips (noise)  ->  apply materiality  ->  aggregate accumulation
by Today / Week / Month / Sector, with FII-vs-DII splits, KPIs and a flow
timeline  ->  persist to SQLite + emit feed.json.

Pure standard library. Run:  python3 pipeline.py
"""

import csv
import glob
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

try:  # works both as a package (inside Regime Compass) and standalone
    from . import classify as C
    from . import prices as P
except ImportError:
    import classify as C
    import prices as P

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "data", "raw")
DB_PATH = os.path.join(HERE, "store.db")
OUT_PATH = os.path.join(HERE, "out", "feed.json")

CR = 1e7  # 1 crore = 10,000,000

# --- tunables -------------------------------------------------------------
MIN_NET_CR = 5.0
SIGNIFICANT_CR = 25.0
WEEK_DAYS = 7
MONTH_DAYS = 30
TIMELINE_DAYS = 95

# Foreign vs domestic buckets, for the FII/DII tilt.
FOREIGN = {C.FII, C.ODI, C.SOVEREIGN}
DOMESTIC = {C.DII, C.INSURANCE, C.AIF}


def bucket(etype):
    if etype in FOREIGN:
        return "FII"
    if etype in DOMESTIC:
        return "DII"
    return "Other"


# --- parsing helpers ------------------------------------------------------
def _inum(s):
    return int(float(s.replace(",", ""))) if s and s.strip() else 0


def _fnum(s):
    return float(s.replace(",", "")) if s and s.strip() else 0.0


def _col(row, *aliases):
    """Find a column value tolerant to header variants across NSE sources."""
    norm = {k.lower().replace(" ", "").replace(".", ""): v for k, v in row.items()}
    for a in aliases:
        key = a.lower().replace(" ", "").replace(".", "")
        if key in norm:
            return norm[key]
    return ""


def load_raw():
    """Read every CSV under data/raw (recursive). deal_type from filename. Dedup."""
    seen = set()
    deals = []
    for path in sorted(glob.glob(os.path.join(RAW_DIR, "**", "*.csv"), recursive=True)):
        fname = os.path.basename(path).lower()
        deal_type = "bulk" if "bulk" in fname else "dln" if "dln" in fname else "block"
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {(k or "").strip(): (v.strip() if v else "") for k, v in row.items()}
                symbol = _col(row, "Symbol")
                date = _col(row, "Date")
                if not symbol or not date:
                    continue
                side = _col(row, "Buy / Sell", "Buy/Sell").upper()
                qty = _inum(_col(row, "Quantity Traded", "Quantity"))
                px = _fnum(_col(row, "Trade Price / Wght. Avg. Price", "Trade Price"))
                client = _col(row, "Client Name")
                key = (date, symbol, client, side, qty, round(px, 2), deal_type)
                if key in seen:
                    continue
                seen.add(key)
                canon = C.canonical_entity(client)
                etype = C.entity_type(canon)
                sign = 1 if side == "BUY" else -1
                deals.append({
                    "date": datetime.strptime(date, "%d-%b-%Y").date(),
                    "symbol": symbol,
                    "security": _col(row, "Security Name"),
                    "raw_name": client,
                    "entity": canon,
                    "etype": etype,
                    "bucket": bucket(etype),
                    "smart": C.is_smart(etype),
                    "sector": C.sector_for(symbol, _col(row, "Security Name")),
                    "deal_type": deal_type,
                    "side": side,
                    "qty": qty,
                    "px": px,
                    "signed_val": sign * qty * px,
                })
    return deals


# --- noise cleaning -------------------------------------------------------
def net_positions(deals):
    """Net every (entity, symbol, day) -> one signed value (kills round-trips)."""
    agg = defaultdict(lambda: {"signed_val": 0.0, "qty_net": 0, "buys": 0, "sells": 0})
    meta = {}
    for d in deals:
        key = (d["entity"], d["symbol"], d["date"])
        a = agg[key]
        a["signed_val"] += d["signed_val"]
        a["qty_net"] += d["qty"] if d["side"] == "BUY" else -d["qty"]
        a["buys" if d["side"] == "BUY" else "sells"] += 1
        meta[key] = d
    out = []
    for (entity, symbol, date), a in agg.items():
        m = meta[(entity, symbol, date)]
        round_trip = a["buys"] > 0 and a["sells"] > 0 and abs(a["signed_val"]) < MIN_NET_CR * CR
        out.append({
            "entity": entity, "symbol": symbol, "date": date, "etype": m["etype"],
            "bucket": m["bucket"], "smart": m["smart"], "sector": m["sector"],
            "security": m["security"], "net_val": a["signed_val"],
            "qty_net": a["qty_net"], "round_trip": round_trip,
        })
    return out


def _smart_clean(nets, start, end):
    for n in nets:
        if n["smart"] and not n["round_trip"] and start <= n["date"] <= end:
            yield n


# --- aggregation ----------------------------------------------------------
def _aggregate(nets, start, end):
    """
    Net each smart entity per symbol over the window, then split into the BUY
    side (accumulation) and the SELL side (distribution / exits). All values on
    each side are returned as positive magnitudes.
    """
    per = defaultdict(lambda: {"actors": defaultdict(float), "sector": "", "security": ""})
    for n in _smart_clean(nets, start, end):
        p = per[n["symbol"]]
        p["actors"][(n["entity"], n["etype"], n["bucket"])] += n["net_val"]
        p["sector"] = n["sector"]
        p["security"] = n["security"]

    def rows_for(side):
        # gross smart flow on this side per symbol — so a name rotated FII->DII
        # surfaces in BOTH accumulation and distribution.
        sign = 1 if side == "buy" else -1
        rows = []
        for sym, p in per.items():
            actors = {k: sign * v for k, v in p["actors"].items() if sign * v > 0}
            mag = sum(actors.values())
            if mag <= MIN_NET_CR * CR:
                continue
            fii = sum(v for (e, t, b), v in actors.items() if b == "FII")
            dii = sum(v for (e, t, b), v in actors.items() if b == "DII")
            rows.append({
                "symbol": sym, "security": p["security"], "sector": p["sector"],
                "net_cr": round(mag / CR, 1),
                "fii_cr": round(fii / CR, 1), "dii_cr": round(dii / CR, 1),
                "n_buyers": len(actors),
                "buyers": [
                    {"entity": e, "etype": t, "bucket": b, "net_cr": round(v / CR, 1)}
                    for (e, t, b), v in sorted(actors.items(), key=lambda kv: -kv[1])
                ],
                "significant": mag >= SIGNIFICANT_CR * CR,
            })
        rows.sort(key=lambda r: -r["net_cr"])
        return rows

    return rows_for("buy"), rows_for("sell")


def _sector_flavour(rows):
    sec = defaultdict(lambda: {"net_cr": 0.0, "fii_cr": 0.0, "dii_cr": 0.0, "names": 0})
    for r in rows:
        s = sec[r["sector"]]
        s["net_cr"] += r["net_cr"]
        s["fii_cr"] += r["fii_cr"]
        s["dii_cr"] += r["dii_cr"]
        s["names"] += 1
    out = [{"sector": s, "net_cr": round(v["net_cr"], 1), "fii_cr": round(v["fii_cr"], 1),
            "dii_cr": round(v["dii_cr"], 1), "names": v["names"]} for s, v in sec.items()]
    out.sort(key=lambda x: -x["net_cr"])
    return out


def _top_entities(nets, start, end, side="buy", n=12):
    # sum only this entity's positions on the active side (per symbol), so it
    # reconciles with the stock list — an entity buying A and selling B counts
    # its A-buy under accumulation and its B-sell under distribution.
    sign = 1 if side == "buy" else -1
    per = defaultdict(lambda: {"mag": 0.0, "etype": "", "bucket": "", "symbols": set()})
    for x in _smart_clean(nets, start, end):
        side_mag = sign * x["net_val"]
        if side_mag <= 0:
            continue
        p = per[x["entity"]]
        p["mag"] += side_mag
        p["etype"] = x["etype"]
        p["bucket"] = x["bucket"]
        p["symbols"].add(x["symbol"])
    out = [{"entity": e, "etype": v["etype"], "bucket": v["bucket"],
            "net_cr": round(v["mag"] / CR, 1), "n_names": len(v["symbols"])}
           for e, v in per.items() if v["mag"] > MIN_NET_CR * CR]
    out.sort(key=lambda x: -x["net_cr"])
    return out[:n]


def _kpis(rows, entities):
    net = round(sum(r["net_cr"] for r in rows), 1)
    fii = round(sum(r["fii_cr"] for r in rows), 1)
    dii = round(sum(r["dii_cr"] for r in rows), 1)
    flav = _sector_flavour(rows)
    return {
        "net_cr": net, "fii_cr": fii, "dii_cr": dii,
        "n_stocks": len(rows), "n_entities": len(entities),
        "top_sector": flav[0]["sector"] if flav else None,
        "top_stock": rows[0]["symbol"] if rows else None,
    }


def _timeline(nets, latest):
    """Daily net smart flow (₹cr), split FII vs DII, over the trailing window."""
    start = latest - timedelta(days=TIMELINE_DAYS)
    days = defaultdict(lambda: {"fii": 0.0, "dii": 0.0})
    for n in nets:
        if not n["smart"] or n["round_trip"] or not (start <= n["date"] <= latest):
            continue
        if n["bucket"] == "FII":
            days[n["date"]]["fii"] += n["net_val"]
        elif n["bucket"] == "DII":
            days[n["date"]]["dii"] += n["net_val"]
    out = [{"date": str(d), "fii_cr": round(v["fii"] / CR, 1), "dii_cr": round(v["dii"] / CR, 1),
            "net_cr": round((v["fii"] + v["dii"]) / CR, 1)} for d, v in sorted(days.items())]
    return out


def _vwap(deals, start, end, side):
    """Quantity-weighted price of smart deals on a side within the window, per symbol."""
    agg = defaultdict(lambda: [0, 0.0])  # qty, value
    for d in deals:
        if d["smart"] and d["side"] == side and start <= d["date"] <= end:
            agg[d["symbol"]][0] += d["qty"]
            agg[d["symbol"]][1] += d["qty"] * d["px"]
    return {s: v / q for s, (q, v) in agg.items() if q}


def _attach_returns(rows, vwap, closes):
    """Return on each name since the smart money traded it (vwap entry -> latest close)."""
    for r in rows:
        entry = vwap.get(r["symbol"])
        now = closes.get(r["symbol"])
        if entry and now:
            r["entry"] = round(entry, 1)
            r["px_now"] = round(now, 1)
            r["ret_pct"] = round((now - entry) / entry * 100, 1)
        else:
            r["ret_pct"] = None


def _performance(rows, flavour, idx, start_pack, label):
    """Basket return of the accumulated names vs Nifty, plus sectoral index moves."""
    rets = [(r["net_cr"], r["ret_pct"]) for r in rows if r.get("ret_pct") is not None]
    wsum = sum(w for w, _ in rets)
    basket = round(sum(w * r for w, r in rets) / wsum, 1) if wsum else None

    nifty = None
    sd, sc = start_pack
    if sc and idx["latest"].get(P.BENCHMARK) and sc.get(P.BENCHMARK):
        nifty = round((idx["latest"][P.BENCHMARK] - sc[P.BENCHMARK]) / sc[P.BENCHMARK] * 100, 1)

    sectors = []
    for f in flavour:
        name = P.SECTOR_INDEX.get(f["sector"])
        if not name or not sc:
            continue
        a, b = idx["latest"].get(name), sc.get(name)
        if a and b:
            sectors.append({"sector": f["sector"], "index": name,
                            "index_ret": round((a - b) / b * 100, 1), "flow_cr": f["net_cr"]})
    return {"basket_ret": basket, "nifty_ret": nifty, "n_priced": len(rets),
            "since": str(sd) if sd else None, "sectors": sectors}


def _rotation(nets, start, end):
    """
    Names where one side (FII vs DII) is net-selling while the other net-buys —
    a hand-off. Surfaces FII->DII rotation (and the reverse) that net-per-symbol
    flow hides. Threshold both legs at the 'significant' level.
    """
    per = defaultdict(lambda: {"FII": 0.0, "DII": 0.0, "sector": "", "security": "",
                               "fii_actor": ("", 0.0), "dii_actor": ("", 0.0)})
    actors = defaultdict(lambda: defaultdict(float))  # (sym,bucket) -> entity -> netval
    for n in _smart_clean(nets, start, end):
        b = n["bucket"]
        if b not in ("FII", "DII"):
            continue
        p = per[n["symbol"]]
        p[b] += n["net_val"]
        p["sector"] = n["sector"]
        p["security"] = n["security"]
        actors[(n["symbol"], b)][n["entity"]] += n["net_val"]
    out = []
    for sym, p in per.items():
        fii, dii = p["FII"], p["DII"]
        if fii * dii >= 0:
            continue  # same direction (or one side zero) -> not a rotation
        handoff = min(abs(fii), abs(dii))
        if handoff < SIGNIFICANT_CR * CR:
            continue
        direction = "FII→DII" if fii < 0 else "DII→FII"
        sellers = actors[(sym, "FII" if fii < 0 else "DII")]
        buyers = actors[(sym, "DII" if fii < 0 else "FII")]
        seller = min(sellers.items(), key=lambda kv: kv[1])[0] if sellers else ""
        buyer = max(buyers.items(), key=lambda kv: kv[1])[0] if buyers else ""
        out.append({
            "symbol": sym, "security": p["security"], "sector": p["sector"],
            "direction": direction,
            "out_cr": round(min(fii, dii) / CR, 1),   # selling leg (negative)
            "in_cr": round(max(fii, dii) / CR, 1),    # buying leg (positive)
            "handoff_cr": round(handoff / CR, 1),
            "seller": seller, "buyer": buyer,
        })
    out.sort(key=lambda r: -r["handoff_cr"])
    return out


def _side(rows, ents):
    return {
        "stocks": rows,
        "flavour": _sector_flavour(rows),
        "top_entities": ents,
        "kpi": _kpis(rows, set(e["entity"] for e in ents)),
    }


def _history(nets):
    """Monthly net smart flow (₹cr) split FII vs DII over the full tracking period —
    the long-run buying/selling consensus."""
    months = defaultdict(lambda: {"fii": 0.0, "dii": 0.0})
    for n in nets:
        if not n["smart"] or n["round_trip"]:
            continue
        ym = f"{n['date'].year}-{n['date'].month:02d}"
        if n["bucket"] == "FII":
            months[ym]["fii"] += n["net_val"]
        elif n["bucket"] == "DII":
            months[ym]["dii"] += n["net_val"]
    return [{"month": ym, "fii_cr": round(v["fii"] / CR, 1), "dii_cr": round(v["dii"] / CR, 1),
             "net_cr": round((v["fii"] + v["dii"]) / CR, 1)} for ym, v in sorted(months.items())]


def _window(nets, deals, eq, idx, latest, days, label, label_today=False):
    start = latest if label_today else latest - timedelta(days=days - 1)
    buy_rows, sell_rows = _aggregate(nets, start, latest)
    _attach_returns(buy_rows, _vwap(deals, start, latest, "BUY"), eq)
    _attach_returns(sell_rows, _vwap(deals, start, latest, "SELL"), eq)
    buy = _side(buy_rows, _top_entities(nets, start, latest, "buy"))
    buy["performance"] = _performance(buy_rows, buy["flavour"], idx, idx["starts"].get(label, (None, None)), label)
    w = {"buy": buy, "sell": _side(sell_rows, _top_entities(nets, start, latest, "sell")),
         "rotation": _rotation(nets, start, latest)}
    if label_today:
        w["date"] = str(latest)
    else:
        w["from"], w["to"] = str(start), str(latest)
    return w


# --- persistence ----------------------------------------------------------
def write_db(deals, nets):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        DROP TABLE IF EXISTS deals;
        DROP TABLE IF EXISTS net_positions;
        CREATE TABLE deals(date TEXT, symbol TEXT, security TEXT, raw_name TEXT,
            entity TEXT, etype TEXT, bucket TEXT, smart INT, sector TEXT,
            deal_type TEXT, side TEXT, qty INT, px REAL, signed_val REAL);
        CREATE TABLE net_positions(date TEXT, entity TEXT, symbol TEXT, etype TEXT,
            bucket TEXT, smart INT, sector TEXT, net_val REAL, qty_net INT, round_trip INT);
        CREATE INDEX idx_deals_sym ON deals(symbol);
        CREATE INDEX idx_net_date ON net_positions(date);
    """)
    cur.executemany("INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(str(d["date"]), d["symbol"], d["security"], d["raw_name"], d["entity"], d["etype"],
          d["bucket"], int(d["smart"]), d["sector"], d["deal_type"], d["side"], d["qty"],
          d["px"], d["signed_val"]) for d in deals])
    cur.executemany("INSERT INTO net_positions VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(str(n["date"]), n["entity"], n["symbol"], n["etype"], n["bucket"], int(n["smart"]),
          n["sector"], n["net_val"], n["qty_net"], int(n["round_trip"])) for n in nets])
    con.commit()
    con.close()


def build_feed(deals, nets, eq, idx):
    latest = max(d["date"] for d in deals)
    earliest = min(d["date"] for d in deals)
    # "Today" = the most recent session that actually had material smart buying,
    # so the headline view is never empty on a quiet day.
    active = sorted({n["date"] for n in nets
                     if n["smart"] and not n["round_trip"] and n["net_val"] > MIN_NET_CR * CR})
    today_anchor = active[-1] if active else latest
    return {
        "meta": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "latest_date": str(latest), "earliest_date": str(earliest),
            "deal_types": sorted(set(d["deal_type"] for d in deals)),
            "min_net_cr": MIN_NET_CR, "significant_cr": SIGNIFICANT_CR,
        },
        "stats": {
            "raw_deals": len(deals),
            "smart_deals": sum(1 for d in deals if d["smart"]),
            "net_positions": len(nets),
            "round_trips_removed": sum(1 for n in nets if n["round_trip"]),
            "distinct_entities": len(set(d["entity"] for d in deals)),
            "distinct_symbols": len(set(d["symbol"] for d in deals)),
        },
        "timeline": _timeline(nets, latest),
        "history": _history(nets),
        "today": _window(nets, deals, eq, idx, today_anchor, 1, "today", label_today=True),
        "week": _window(nets, deals, eq, idx, latest, WEEK_DAYS, "week"),
        "month": _window(nets, deals, eq, idx, latest, MONTH_DAYS, "month"),
    }


def main():
    deals = load_raw()
    if not deals:
        print("no deals found in data/raw")
        return
    nets = net_positions(deals)
    write_db(deals, nets)
    latest = max(d["date"] for d in deals)
    active = sorted({n["date"] for n in nets
                     if n["smart"] and not n["round_trip"] and n["net_val"] > MIN_NET_CR * CR})
    anchor = active[-1] if active else latest
    eq, idx = P.load_prices(latest, {
        "today": anchor,
        "week": latest - timedelta(days=WEEK_DAYS - 1),
        "month": latest - timedelta(days=MONTH_DAYS - 1),
    })
    feed = build_feed(deals, nets, eq, idx)
    with open(OUT_PATH, "w") as f:
        json.dump(feed, f, indent=2)
    s = feed["stats"]
    print(f"deals={s['raw_deals']} smart={s['smart_deals']} "
          f"round_trips_removed={s['round_trips_removed']} "
          f"latest={feed['meta']['latest_date']}")
    m = feed["month"]
    print(f"month buy={len(m['buy']['stocks'])} sell={len(m['sell']['stocks'])} "
          f"timeline_days={len(feed['timeline'])}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
