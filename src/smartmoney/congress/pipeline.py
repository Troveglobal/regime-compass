"""
Congress trades pipeline: senate + house filings -> per-politician portfolios
and an overview (what Congress is buying/selling, who trades most).

STOCK Act amounts are ranges, never exact — aggregates use range midpoints and
the UI shows the ranges. Disclosure lag is up to 45 days: this is a positioning
tracker, not a daily-flow tracker.
"""

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta

try:
    from ..markets import common as X
    from . import house, members, senate
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "markets"))
    import common as X
    import house
    import members
    import senate

MKT = "congress"
OUT = os.path.join(X.OUT, "feed_congress.json")
RECENT_DAYS = 90
TRADE_FLOOR = "2025-01-01"  # drop stale trades surfacing via amended filings


def _mid(t):
    return (t["lo"] + t["hi"]) / 2


def _trades():
    """Flatten all filings into per-politician trade lists, joined to metadata."""
    ms = members.load()
    idx = members.matcher(ms)
    people = {}
    unmatched = set()
    for f in senate.filings() + house.filings():
        state = (f.get("state_dst") or "")[:2] or None
        bid = members.match(idx, ms, f["last"], f["first"], state=state, chamber=f["chamber"])
        key = bid or f"{f['chamber']}:{f['last']},{f['first']}"
        if not bid:
            unmatched.add(key)
        if key not in people:
            m = ms.get(bid, {})
            people[key] = {
                "id": key, "name": m.get("name") or f"{f['first']} {f['last']}".title(),
                "party": m.get("party", ""), "chamber": f["chamber"],
                "state": m.get("state") or state or "", "district": m.get("district"),
                "committees": m.get("committees", []), "trades": [],
            }
        try:
            filed = datetime.strptime(f["filed"], "%m/%d/%Y").date().isoformat()
        except ValueError:
            filed = None
        for t in f["txns"]:
            if t["date"] < TRADE_FLOOR:
                continue
            if f["chamber"] == "House":  # heal caches parsed before the asset cleanup
                t = {**t, "asset": house.clean_asset(t["asset"])}
            people[key]["trades"].append({**t, "filed": filed})
    return people, unmatched


def build():
    people, unmatched = _trades()
    today = date.today()
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()

    politicians = []
    sym_buy = defaultdict(lambda: {"mid": 0.0, "who": set(), "asset": ""})
    sym_sell = defaultdict(lambda: {"mid": 0.0, "who": set(), "asset": ""})
    recent_filings = []

    for p in people.values():
        trades = sorted(p["trades"], key=lambda t: t["date"], reverse=True)
        if not trades:
            continue
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]

        def top(ts, n=8):
            agg = defaultdict(lambda: {"mid": 0.0, "n": 0, "asset": ""})
            for t in ts:
                a = agg[t["symbol"]]
                a["mid"] += _mid(t)
                a["n"] += 1
                a["asset"] = t["asset"] or a["asset"]
            out = [{"symbol": s, "mid": round(v["mid"]), "n": v["n"], "asset": v["asset"][:60]}
                   for s, v in agg.items()]
            out.sort(key=lambda x: -x["mid"])
            return out[:n]

        for t in trades:
            if t["date"] >= cutoff:
                tgt = sym_buy if t["side"] == "BUY" else sym_sell
                tgt[t["symbol"]]["mid"] += _mid(t)
                tgt[t["symbol"]]["who"].add(p["name"])
                tgt[t["symbol"]]["asset"] = t["asset"] or tgt[t["symbol"]]["asset"]
            if t.get("filed"):
                recent_filings.append({
                    "name": p["name"], "party": p["party"], "chamber": p["chamber"],
                    "state": p["state"], "symbol": t["symbol"], "asset": t["asset"][:60],
                    "side": t["side"], "lo": t["lo"], "hi": t["hi"],
                    "date": t["date"], "filed": t["filed"],
                })

        recent_mid = sum(_mid(t) for t in trades if t["date"] >= cutoff)
        politicians.append({
            "id": p["id"], "name": p["name"], "party": p["party"],
            "chamber": p["chamber"], "state": p["state"], "district": p["district"],
            "committees": p["committees"],
            "n_trades": len(trades),
            "buy_mid": round(sum(_mid(t) for t in buys)),
            "sell_mid": round(sum(_mid(t) for t in sells)),
            "recent_mid": round(recent_mid),
            "last_traded": trades[0]["date"],
            "top_buys": top(buys), "top_sells": top(sells),
            "trades": trades[:60],
        })

    politicians.sort(key=lambda p: (-p["recent_mid"], -p["n_trades"]))
    recent_filings.sort(key=lambda r: (r["filed"], r["date"]), reverse=True)

    def sym_list(agg):
        out = [{"symbol": s, "asset": v["asset"][:60], "mid": round(v["mid"]),
                "n_politicians": len(v["who"]), "politicians": sorted(v["who"])[:6]}
               for s, v in agg.items()]
        out.sort(key=lambda x: -x["mid"])
        return out[:15]

    all_dates = [t["date"] for p in people.values() for t in p["trades"]]
    feed = {
        "meta": {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "coverage_from": min(all_dates) if all_dates else None,
            "latest_trade": max(all_dates) if all_dates else None,
            "n_politicians": len(politicians),
            "n_trades": sum(p["n_trades"] for p in politicians),
            "unmatched": sorted(unmatched),
            "recent_days": RECENT_DAYS,
            "source": "Senate eFD + House Clerk periodic transaction reports (STOCK Act); "
                      "amounts are disclosed ranges; filings may lag trades by up to 45 days",
        },
        "overview": {
            "top_bought": sym_list(sym_buy),
            "top_sold": sym_list(sym_sell),
            "recent": recent_filings[:30],
        },
        "politicians": politicians,
    }
    return feed


def refresh():
    try:
        senate.fetch()
    except Exception as e:  # noqa: BLE001
        print(f"  [congress] senate fetch failed: {e}", flush=True)
    try:
        house.fetch()
    except Exception as e:  # noqa: BLE001
        print(f"  [congress] house fetch failed: {e}", flush=True)
    feed = build()
    with open(OUT, "w") as f:
        json.dump(feed, f, indent=1)
    m = feed["meta"]
    print(f"congress feed: {m['n_politicians']} politicians, {m['n_trades']} trades, "
          f"latest {m['latest_trade']}", flush=True)
    return True


if __name__ == "__main__":
    import sys
    if "--build-only" in sys.argv:
        feed = build()
        with open(OUT, "w") as f:
            json.dump(feed, f, indent=1)
        print(json.dumps(feed["meta"], indent=1))
    else:
        refresh()
