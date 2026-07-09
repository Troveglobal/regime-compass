"""Smart Money portfolio — backtest + live tracker.

IDEA
  When smart money (default: FIIs) buys a name with conviction (net buy above a
  size bar on a day), we buy it too — at the NEXT trading day's close, since
  bulk/block deals are only public after market close (no look-ahead). We then
  run two exit disciplines:

    A. FOLLOW-THE-FLOW  — hold until smart money net-sells the name (or a max
       holding cap; FIIs often exit invisibly in the open market, so the cap
       matters). Category 1 the user asked for.
    B. TARGET-RETURN    — take profit at +TARGET%, cut at -STOP%, or time out
       after MAX_HOLD trading days. Category 2. We SWEEP the target to let the
       data say what the best exit is, rather than guessing 15-20%.

DATA
  signals : store.db `deals` (smart=1) — entries and (for A) exits.
  prices  : data/prices/eq_<ISO>.json  — {symbol: close} per trading day.

Everything is gross of costs and single-market — a mechanism study, not a live edge.
"""
import json
import os
import sqlite3
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "store.db")
PRICES = os.path.join(HERE, "data", "prices")
CR = 1e7  # 1 crore in rupees


# ---------- data loading ----------

def load_panel():
    """{iso_date: {symbol: close}} and the sorted list of trading dates present,
    back-adjusted for stock splits/bonuses (NSE bhavcopy closes are raw/unadjusted)."""
    panel = {}
    for f in os.listdir(PRICES):
        if f.startswith("eq_") and f.endswith(".json"):
            iso = f[3:-5]
            try:
                with open(os.path.join(PRICES, f)) as fh:
                    panel[iso] = json.load(fh)
            except Exception:
                pass
    dates = sorted(panel)
    return _split_adjust(panel, dates), dates


# Clean split/bonus ratios (forward split makes price fall by 1/N; bonus/reverse the inverse).
_CLEAN_RATIOS = [1/2, 1/3, 1/4, 1/5, 1/6, 1/8, 1/10, 2, 3, 4, 5, 6, 8, 10]


def _split_adjust(panel, dates):
    """Detect single-day price discontinuities that match a clean split/bonus ratio
    and persist (not a bad tick), then BACK-ADJUST all earlier prices onto the
    post-event scale so returns across the event are correct.

    Only triggers near clean ratios (1:2 … 1:10 and inverses) with a next-day
    persistence check, so a real one-day crash is not mistaken for a split."""
    from collections import defaultdict
    series = defaultdict(list)
    for i, iso in enumerate(dates):
        for sym, px in panel[iso].items():
            if px:
                series[sym].append((i, iso, px))

    events = defaultdict(list)  # sym -> [(event_iso, observed_ratio)]
    for sym, pts in series.items():
        for k in range(1, len(pts)):
            r = pts[k][2] / pts[k - 1][2]
            if not any(abs(r - c) / c < 0.06 for c in _CLEAN_RATIOS):
                continue
            # persistence: the day AFTER should stay near the new level (ratio ~1)
            if k + 1 < len(pts):
                r2 = pts[k + 1][2] / pts[k][2]
                if r2 < 0.7 or r2 > 1.45:
                    continue
            events[sym].append((pts[k][1], r))
    if not events:
        return panel

    adj = {iso: dict(panel[iso]) for iso in dates}
    for sym, evs in events.items():
        for iso in dates:
            if sym not in adj[iso]:
                continue
            f = 1.0
            for ev_iso, r in evs:
                if iso < ev_iso:      # dates before the split: scale onto post-split terms
                    f *= r
            if f != 1.0:
                adj[iso][sym] = round(adj[iso][sym] * f, 4)
    return adj


def load_idx_panel():
    """{iso: nifty50_close} from cached idx_<ISO>.json files."""
    out = {}
    for f in os.listdir(PRICES):
        if f.startswith("idx_") and f.endswith(".json"):
            iso = f[4:-5]
            try:
                with open(os.path.join(PRICES, f)) as fh:
                    v = json.load(fh).get("Nifty 50")
                if v:
                    out[iso] = v
            except Exception:
                pass
    return out


def _bench_ret(idx, idates, entry_iso, exit_iso):
    """Nifty 50 % return from the trading day on/before entry to on/before exit."""
    import bisect
    def near(iso):
        i = bisect.bisect_right(idates, iso) - 1
        return idx[idates[i]] if 0 <= i < len(idates) else None
    a, b = near(entry_iso), near(exit_iso)
    return (b / a - 1.0) * 100.0 if a and b else None


def _next_close(panel, dates, symbol, after_iso):
    """(iso, close) for the first trading day strictly AFTER after_iso that has a
    price for symbol. None if none."""
    import bisect
    i = bisect.bisect_right(dates, after_iso)
    while i < len(dates):
        px = panel[dates[i]].get(symbol)
        if px:
            return dates[i], px
        i += 1
    return None


def _walk(panel, dates, symbol, from_iso):
    """Yield (iso, close) for symbol on/after from_iso, in order (skips days with no px)."""
    import bisect
    i = bisect.bisect_left(dates, from_iso)
    while i < len(dates):
        px = panel[dates[i]].get(symbol)
        if px:
            yield dates[i], px
        i += 1


def _stock_ma(panel, dates, sym, iso, n=50):
    import bisect
    i = bisect.bisect_right(dates, iso)
    vals, j = [], i - 1
    while j >= 0 and len(vals) < n:
        px = panel[dates[j]].get(sym)
        if px:
            vals.append(px)
        j -= 1
    return sum(vals) / len(vals) if len(vals) >= n * 0.6 else None


def _nifty_ma(idx, idates, iso, n=100):
    import bisect
    i = bisect.bisect_right(idates, iso)
    vals = [idx[idates[j]] for j in range(max(0, i - n), i)]
    return sum(vals) / len(vals) if len(vals) >= n * 0.6 else None


def entry_signals(bar_cr=25.0, bucket="FII"):
    """Days a symbol saw net smart-money buying >= bar_cr. Returns list of
    (date_iso, symbol, net_cr), earliest first, one row per (date,symbol)."""
    con = sqlite3.connect(DB)
    where = "smart=1" + (f" AND bucket='{bucket}'" if bucket else "")
    rows = con.execute(
        f"SELECT date, symbol, SUM(signed_val)/? AS net_cr FROM deals "
        f"WHERE {where} GROUP BY date, symbol HAVING net_cr >= ? ORDER BY date",
        (CR, bar_cr),
    ).fetchall()
    con.close()
    return [(d, s, round(v, 1)) for d, s, v in rows]


def _net_sell_days(symbol, bucket="FII"):
    """Sorted iso dates where smart money net-SOLD `symbol` (net flow < 0)."""
    con = sqlite3.connect(DB)
    where = "smart=1" + (f" AND bucket='{bucket}'" if bucket else "")
    rows = con.execute(
        f"SELECT date, SUM(signed_val) net FROM deals WHERE {where} AND symbol=? "
        f"GROUP BY date HAVING net < 0 ORDER BY date", (symbol,),
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


# ---------- position simulation ----------

def _simulate_target(panel, dates, symbol, signal_iso, target, stop, max_hold):
    """Enter next close after signal; exit at +target / -stop / timeout. Returns
    dict or None if no entry price available."""
    ent = _next_close(panel, dates, symbol, signal_iso)
    if not ent:
        return None
    ent_iso, ent_px = ent
    held = 0
    for iso, px in _walk(panel, dates, symbol, ent_iso):
        if iso == ent_iso:
            continue
        held += 1
        r = px / ent_px - 1.0
        if r >= target:
            return _trade(symbol, ent_iso, ent_px, iso, px, held, "target", False)
        if r <= -stop:
            return _trade(symbol, ent_iso, ent_px, iso, px, held, "stop", False)
        if held >= max_hold:
            return _trade(symbol, ent_iso, ent_px, iso, px, held, "timeout", False)
    # ran out of data -> still open, mark to last available close
    last = None
    for iso, px in _walk(panel, dates, symbol, ent_iso):
        last = (iso, px, )
        held_last = iso
    if last and last[0] != ent_iso:
        # recompute held as trading days from entry
        hd = sum(1 for _ in _walk(panel, dates, symbol, ent_iso)) - 1
        return _trade(symbol, ent_iso, ent_px, last[0], last[1], hd, "open", True)
    return _trade(symbol, ent_iso, ent_px, ent_iso, ent_px, 0, "open", True)


def _simulate_flow(panel, dates, symbol, signal_iso, max_hold, stop, bucket):
    """Enter next close after signal; exit when smart money net-sells the name
    (next close after that sell), or stop, or max_hold. Category A."""
    ent = _next_close(panel, dates, symbol, signal_iso)
    if not ent:
        return None
    ent_iso, ent_px = ent
    sells = [d for d in _net_sell_days(symbol, bucket) if d > signal_iso]
    sell_exit = _next_close(panel, dates, symbol, sells[0]) if sells else None
    held = 0
    for iso, px in _walk(panel, dates, symbol, ent_iso):
        if iso == ent_iso:
            continue
        held += 1
        r = px / ent_px - 1.0
        if stop is not None and r <= -stop:
            return _trade(symbol, ent_iso, ent_px, iso, px, held, "stop", False)
        if sell_exit and iso >= sell_exit[0]:
            return _trade(symbol, ent_iso, ent_px, iso, px, held, "flow_exit", False)
        if held >= max_hold:
            return _trade(symbol, ent_iso, ent_px, iso, px, held, "timeout", False)
    hd = sum(1 for _ in _walk(panel, dates, symbol, ent_iso)) - 1
    last = None
    for iso, px in _walk(panel, dates, symbol, ent_iso):
        last = (iso, px)
    return _trade(symbol, ent_iso, ent_px, last[0], last[1], hd, "open", True)


def _trade(sym, e_iso, e_px, x_iso, x_px, held, reason, is_open):
    return {"symbol": sym, "entry_date": e_iso, "entry_px": e_px,
            "exit_date": x_iso, "exit_px": x_px, "held_days": held,
            "ret_pct": (x_px / e_px - 1.0) * 100.0, "reason": reason, "open": is_open}


# ---------- portfolio-level aggregation ----------

def _dedup_first_entry(signals):
    """Keep only the first signal per symbol (one position per name at a time)."""
    seen, out = set(), []
    for d, s, v in signals:
        if s in seen:
            continue
        seen.add(s)
        out.append((d, s, v))
    return out


def _summ(trades):
    """Summary stats over a list of trades."""
    ts = [t for t in trades if t]
    if not ts:
        return {"n": 0}
    rets = [t["ret_pct"] for t in ts]
    wins = [r for r in rets if r > 0]
    closed = [t for t in ts if not t["open"]]
    import statistics as st
    return {
        "n": len(ts),
        "n_open": sum(1 for t in ts if t["open"]),
        "avg_ret_pct": round(st.mean(rets), 2),
        "median_ret_pct": round(st.median(rets), 2),
        "win_rate_pct": round(100.0 * len(wins) / len(ts), 1),
        "avg_hold_days": round(st.mean([t["held_days"] for t in ts]), 1),
        "avg_win_pct": round(st.mean(wins), 2) if wins else 0.0,
        "avg_loss_pct": round(st.mean([r for r in rets if r <= 0]), 2) if any(r <= 0 for r in rets) else 0.0,
        "best_pct": round(max(rets), 1),
        "worst_pct": round(min(rets), 1),
        "total_equal_weight_pct": round(st.mean(rets), 2),  # equal-weight avg trade = portfolio return per rotation
    }


def _annotate_alpha(trades, idx, idates):
    """Attach benchmark & alpha to each trade; return (avg_bench, avg_alpha)."""
    import statistics as st
    benches, alphas = [], []
    for t in [x for x in trades if x]:
        b = _bench_ret(idx, idates, t["entry_date"], t["exit_date"])
        t["bench_pct"] = round(b, 2) if b is not None else None
        if b is not None:
            t["alpha_pct"] = round(t["ret_pct"] - b, 2)
            benches.append(b); alphas.append(t["ret_pct"] - b)
    return (round(st.mean(benches), 2) if benches else 0.0,
            round(st.mean(alphas), 2) if alphas else 0.0)


def _qualify(panel, dates, idx, idates, sigs, momentum, nifty_regime):
    """Keep only signals whose entry (next close) passes the overlay filters:
    momentum = stock already above its own 50-day average; nifty_regime = Nifty
    above its 100-day average. 'Don't catch a falling knife in a falling market.'"""
    out = []
    for d, s, v in sigs:
        ent = _next_close(panel, dates, s, d)
        if not ent:
            continue
        e_iso, e_px = ent
        if momentum:
            ma = _stock_ma(panel, dates, s, e_iso, 50)
            if ma is None or e_px < ma:
                continue
        if nifty_regime:
            nm = _nifty_ma(idx, idates, e_iso, 100)
            import bisect
            ni = idx.get(e_iso) or (idx[idates[bisect.bisect_right(idates, e_iso) - 1]] if idates else None)
            if nm is None or ni is None or ni < nm:
                continue
        out.append((d, s, v))
    return out


def _equity_curve(trades, panel, dates, idx, idates):
    """Equal-weight, daily-rebalanced portfolio equity curve (base 100) vs Nifty
    over the same span. Each held position contributes its 1-day return; the
    portfolio's daily return is the average across positions held that day
    (cash / 0% on days with no positions)."""
    import bisect
    pos = [(t["symbol"], t["entry_date"], t["exit_date"]) for t in trades if t]
    if not pos:
        return None
    start = min(e for _, e, _ in pos)
    end = max(x for _, _, x in pos)
    i0 = bisect.bisect_left(dates, start)
    i1 = bisect.bisect_right(dates, end)
    span = dates[i0:i1]
    if len(span) < 2:
        return None
    eq, out = [100.0], [span[0]]
    for k in range(1, len(span)):
        prev, cur = span[k - 1], span[k]
        rr = []
        for sym, e, x in pos:
            if e <= prev and x >= cur:
                a, b = panel[prev].get(sym), panel[cur].get(sym)
                if a and b:
                    rr.append(b / a - 1.0)
        r = sum(rr) / len(rr) if rr else 0.0
        eq.append(eq[-1] * (1.0 + r))
        out.append(cur)

    def ni(iso):
        j = bisect.bisect_right(idates, iso) - 1
        return idx[idates[j]] if 0 <= j < len(idates) else None
    n0 = ni(out[0])
    nifty = [(round(ni(d) / n0 * 100.0, 2) if ni(d) and n0 else None) for d in out]
    return {"dates": out, "portfolio": [round(x, 2) for x in eq], "nifty": nifty}


def backtest(bar_cr=25.0, bucket="FII", target=0.20, stop=0.10, max_hold=90, mode="refined"):
    """Run both strategies once, plus the target sweep. mode='refined' applies the
    momentum + Nifty-regime entry overlays (the optimised config); 'raw' does not."""
    panel, dates = load_panel()
    if len(dates) < 30:
        return {"error": f"only {len(dates)} price days cached — backfill still running"}
    idx, idates = load_idx_panel(), sorted(load_idx_panel())
    sigs = _dedup_first_entry(entry_signals(bar_cr, bucket))
    refined = mode == "refined"
    if refined:
        sigs = _qualify(panel, dates, idx, idates, sigs, momentum=True, nifty_regime=True)

    # Category 1 = buy & hold until smart money exits — NO stop (per spec)
    flow = [_simulate_flow(panel, dates, s, d, max_hold=252, stop=None, bucket=bucket) for d, s, _ in sigs]
    targ = [_simulate_target(panel, dates, s, d, target, stop, max_hold) for d, s, _ in sigs]
    priced = sum(1 for t in targ if t is not None)

    flow_sum, targ_sum = _summ(flow), _summ(targ)
    flow_sum["avg_bench_pct"], flow_sum["avg_alpha_pct"] = _annotate_alpha(flow, idx, idates)
    targ_sum["avg_bench_pct"], targ_sum["avg_alpha_pct"] = _annotate_alpha(targ, idx, idates)

    # target sweep — "correlate the best return"
    sweep = []
    for tg in (0.10, 0.15, 0.20, 0.25, 0.30):
        tr = [_simulate_target(panel, dates, s, d, tg, stop, max_hold) for d, s, _ in sigs]
        sm = _summ(tr)
        sm["target_pct"] = int(tg * 100)
        sm["hit_rate_pct"] = round(100.0 * sum(1 for t in tr if t and t["reason"] == "target") / max(1, sm["n"]), 1)
        _, sm["avg_alpha_pct"] = _annotate_alpha(tr, idx, idates)
        sweep.append(sm)

    # Nifty over the whole window (buy-and-hold benchmark)
    nifty = _bench_ret(idx, idates, dates[0], dates[-1]) if idates else None

    # live holdings = still-open follow-the-flow positions (bought, not yet exited)
    holdings = sorted([t for t in flow if t and t["open"]],
                      key=lambda t: t["ret_pct"], reverse=True)

    return {
        "params": {"bar_cr": bar_cr, "bucket": bucket, "target_pct": int(target * 100),
                   "stop_pct": int(stop * 100), "max_hold_days": max_hold, "mode": mode,
                   "overlays": ["stock above 50-DMA", "Nifty above 100-DMA"] if refined else []},
        "universe": {"signals": len(sigs), "priced": priced,
                     "price_days": len(dates), "date_range": [dates[0], dates[-1]]},
        "benchmark_nifty_pct": round(nifty, 2) if nifty is not None else None,
        "follow_the_flow": flow_sum,
        "target_return": targ_sum,
        "target_sweep": sweep,
        "live_holdings": holdings,
        "equity_curve": _equity_curve(flow, panel, dates, idx, idates),
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(backtest())
