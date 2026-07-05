"""GEO (Generative Engine Optimization) — server-rendered data summaries.

All live numbers on the site are injected client-side, so AI crawlers that
don't execute JavaScript (GPTBot, ClaudeBot, PerplexityBot, ...) would see
prose but no data. This module assembles a cached "today snapshot" from
local sources only (SQLite, parquet caches, feed JSON — never the network)
and renders it into the served HTML via `<!--GEO:...-->` placeholders.

Any failure falls back to serving the raw file — GEO can never break a page.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse

from . import composite as composite_mod
from .config import DATA_DIR, DB_PATH, INDICES

log = logging.getLogger("regime_compass")

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
SMARTMONEY_FEED = Path(__file__).resolve().parent / "smartmoney" / "out" / "feed.json"
VAL_DIR = DATA_DIR / "valuation"

_TTL_SECONDS = 1800  # composite_today() re-reads ~20 parquets; cap at 2 builds/hour
_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "snap": None}


# ------------------------------------------------------------------
# Snapshot assembly (local reads only — no network, ever)
# ------------------------------------------------------------------

def _esc(s) -> str:
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _fmt_cr(cr: float) -> str:
    v = round(abs(cr))
    if v >= 10000:
        return f"₹{v / 1000:.1f}k cr"
    return f"₹{v:,.0f} cr"


def _latest_hmm_date() -> str | None:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT MAX(date) FROM probabilities").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _smartmoney_summary() -> dict | None:
    if not SMARTMONEY_FEED.exists():
        return None
    feed = json.loads(SMARTMONEY_FEED.read_text())
    w = feed.get("month") or {}
    buy_k = (w.get("buy") or {}).get("kpi") or {}
    sell_k = (w.get("sell") or {}).get("kpi") or {}
    net = (buy_k.get("net_cr") or 0) - (sell_k.get("net_cr") or 0)
    fii_net = (buy_k.get("fii_cr") or 0) - (sell_k.get("fii_cr") or 0)
    dii_net = (buy_k.get("dii_cr") or 0) - (sell_k.get("dii_cr") or 0)
    flav = (w.get("buy") or {}).get("flavour") or []
    top_sector = flav[0]["sector"] if flav else None
    stocks = ((w.get("buy") or {}).get("stocks") or [])[:3]
    return {
        "latest_date": (feed.get("meta") or {}).get("latest_date"),
        "net_cr": net,
        "direction": "accumulating" if net >= 0 else "distributing",
        "lead": "FII-led" if abs(fii_net) > abs(dii_net) else "DII-led",
        "top_sector": top_sector,
        "top_buys": [{"symbol": s["symbol"], "net_cr": s["net_cr"]} for s in stocks],
        "buy_net_cr": buy_k.get("net_cr") or 0,
        "sell_net_cr": sell_k.get("net_cr") or 0,
    }


def _valuation_summary() -> dict:
    """Cheap disk-cache reads only: CAPE + MVRV parquets and the PE snapshot JSON."""
    out: dict = {}
    try:
        import pandas as pd

        cape_path = VAL_DIR / "shiller_cape.parquet"
        if cape_path.exists():
            df = pd.read_parquet(cape_path)
            cur = float(df["cape"].iloc[-1])
            out["spx_cape"] = round(cur, 2)
            out["spx_cape_pct"] = round(float((df["cape"] <= cur).mean() * 100), 1)
        for asset in ("btc", "eth"):
            p = VAL_DIR / f"mvrv_{asset}.parquet"
            if p.exists():
                df = pd.read_parquet(p)
                cur = float(df["mvrv"].iloc[-1])
                out[f"{asset}_mvrv"] = round(cur, 2)
    except Exception as e:  # noqa: BLE001
        log.debug("[geo] valuation parquet read failed: %s", e)
    try:
        pe_path = VAL_DIR / "pe_snapshots.json"
        if pe_path.exists():
            out["pe"] = json.loads(pe_path.read_text())
    except Exception as e:  # noqa: BLE001
        log.debug("[geo] pe snapshot read failed: %s", e)
    return out


def _build_snapshot() -> dict:
    comp = composite_mod.composite_today()
    markets = []
    for b in comp.get("breakdown", []):
        gauge = round((b["score"] + 100) / 2)
        markets.append({
            "key": b["index_key"],
            "name": b["index_name"],
            "sma": b["sma_regime"],
            "ema": b["ema_regime"],
            "hmm": b.get("hmm_state"),
            "sma_gap_pct": b.get("sma_gap_pct"),
            "gauge": gauge,
            "stance": "risk-on" if gauge >= 70 else "risk-off" if gauge <= 30 else "mixed",
        })
    return {
        "as_of": _latest_hmm_date(),
        "gauge": comp.get("gauge"),
        "gauge_label": comp.get("regime_label"),
        "markets": markets,
        "smartmoney": _smartmoney_summary(),
        "valuation": _valuation_summary(),
    }


def geo_snapshot() -> dict | None:
    with _lock:
        if _cache["snap"] is not None and time.time() - _cache["ts"] < _TTL_SECONDS:
            return _cache["snap"]
        try:
            snap = _build_snapshot()
        except Exception as e:  # noqa: BLE001
            log.warning("[geo] snapshot build failed: %s", e)
            return _cache["snap"]  # stale-if-error
        _cache["snap"] = snap
        _cache["ts"] = time.time()
        return snap


# ------------------------------------------------------------------
# Rendered blocks
# ------------------------------------------------------------------

def _market_phrase(m: dict) -> str:
    """One market, one consensus word. Agreement across models is stated as such."""
    votes = [m["sma"], m["ema"], m["hmm"] or m["sma"]]
    if len(set(votes)) == 1:
        return f"{m['name']} {votes[0]}"
    return f"{m['name']} {m['stance']}"


def _brief_sentence(snap: dict) -> str:
    parts = [_market_phrase(m) for m in snap["markets"]]
    parts.append(f"global risk gauge {snap['gauge']:.0f}/100 ({snap['gauge_label']})")
    return f"As of {snap['as_of']}: " + " · ".join(parts) + "."


def _strip_html(title: str, body_html: str) -> str:
    return (
        '<section class="geo-brief" aria-label="Today at a glance">'
        f"<h2>{title}</h2><p>{body_html}</p></section>"
    )


def _jsonld(snap: dict, name: str, desc: str) -> str:
    node = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name,
        "description": desc,
        "dateModified": snap["as_of"],
        "creator": {"@type": "Organization", "name": "iQuant Labs", "url": "https://www.regimecompass.com"},
        "license": "https://www.regimecompass.com/terms",
        "isAccessibleForFree": True,
    }
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'


def blocks_for(page: str) -> dict[str, str]:
    """Placeholder → rendered HTML for one page. Raises on missing data (caller falls back)."""
    snap = geo_snapshot()
    if not snap or not snap.get("markets"):
        raise RuntimeError("no snapshot")
    as_of = _esc(snap["as_of"])
    out: dict[str, str] = {}

    if page == "index":
        out["BRIEF"] = _strip_html("Today at a glance", _esc(_brief_sentence(snap)))
        out["JSONLD"] = _jsonld(
            snap,
            "Regime Compass daily market regime classifications",
            f"Daily bull/neutral/bear regime states from SMA, EMA and HMM models across 11 global markets. {_brief_sentence(snap)}",
        )

    elif page == "composite":
        parts = [f"{m['name']} {m['gauge']}/100 ({m['stance']})" for m in snap["markets"]]
        body = f"As of {as_of}, global risk gauge {snap['gauge']:.0f}/100 ({_esc(snap['gauge_label'])}). Per market: " + " · ".join(_esc(p) for p in parts) + "."
        out["BRIEF"] = _strip_html("Today's risk scores", body)
        out["JSONLD"] = _jsonld(snap, "Regime Compass composite risk scores", body)

    elif page == "hmm":
        parts = [f"{m['name']} {m['hmm']}" for m in snap["markets"] if m.get("hmm")]
        body = f"HMM regime as of {as_of}: " + " · ".join(_esc(p) for p in parts) + "."
        out["BRIEF"] = _strip_html("Today's HMM regimes", body)
        out["JSONLD"] = _jsonld(snap, "Regime Compass HMM regime probabilities", body)

    elif page in ("ma", "ema"):
        kind = page
        parts = []
        for m in snap["markets"]:
            gap = m.get("sma_gap_pct")
            gap_txt = f" ({gap:+.1f}% vs 200d)" if isinstance(gap, (int, float)) and kind == "ma" else ""
            parts.append(f"{m['name']} {m[('sma' if kind == 'ma' else 'ema')]}{gap_txt}")
        label = "SMA" if kind == "ma" else "EMA"
        body = f"200-day {label} regime as of {as_of}: " + " · ".join(_esc(p) for p in parts) + "."
        out["BRIEF"] = _strip_html(f"Today's {label} regimes", body)
        out["JSONLD"] = _jsonld(snap, f"Regime Compass {label} regime states", body)

    elif page == "smartmoney":
        sm = snap.get("smartmoney")
        if not sm:
            raise RuntimeError("no smartmoney feed")
        sign = "+" if sm["net_cr"] >= 0 else "−"
        body = (
            f"As of {_esc(sm['latest_date'])}, smart money is net {sm['direction']} "
            f"{sign}{_fmt_cr(sm['net_cr'])} over the last month ({sm['lead']}"
            + (f", heaviest in {_esc(sm['top_sector'])}" if sm["top_sector"] else "")
            + ")."
        )
        if sm["top_buys"]:
            tops = " · ".join(f"{_esc(s['symbol'])} +{_fmt_cr(s['net_cr'])}" for s in sm["top_buys"])
            body += f" Top accumulation: {tops}."
        out["BRIEF"] = _strip_html("Smart money this month", body)
        out["JSONLD"] = _jsonld(snap, "Smart Money India institutional flows", body)

    elif page == "valuation":
        val = snap.get("valuation") or {}
        parts = []
        if "spx_cape" in val:
            parts.append(f"S&amp;P 500 Shiller CAPE {val['spx_cape']} ({val['spx_cape_pct']}th percentile since 1881)")
        for key, label in (("btc", "Bitcoin"), ("eth", "Ethereum")):
            if f"{key}_mvrv" in val:
                parts.append(f"{label} MVRV {val[f'{key}_mvrv']}")
        for key, cfg in INDICES.items():
            pe = (val.get("pe") or {}).get(key)
            if pe:
                parts.append(f"{_esc(cfg['name'])} trailing PE {pe['pe']}")
        if not parts:
            raise RuntimeError("no valuation caches")
        body = f"As of {as_of}: " + " · ".join(parts) + "."
        out["BRIEF"] = _strip_html("Valuation snapshot", body)
        out["JSONLD"] = _jsonld(snap, "Regime Compass valuation metrics", body)

    elif page == "today":
        out.update(_today_blocks(snap))

    return out


def _today_blocks(snap: dict) -> dict[str, str]:
    """Sections for the fully server-rendered /today daily brief."""
    as_of = _esc(snap["as_of"])
    rows = "".join(
        "<tr>"
        f"<td>{_esc(m['name'])}</td>"
        f"<td>{_esc(m['sma'])}</td><td>{_esc(m['ema'])}</td><td>{_esc(m['hmm'] or '—')}</td>"
        f'<td class="right">{m["gauge"]}/100</td><td>{_esc(m["stance"])}</td>'
        "</tr>"
        for m in snap["markets"]
    )
    table = (
        '<div class="table-scroll"><table class="t">'
        "<thead><tr><th>Market</th><th>SMA 200</th><th>EMA 200</th><th>HMM</th>"
        '<th class="right">Risk gauge</th><th>Stance</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )

    sm_html = ""
    sm = snap.get("smartmoney")
    if sm:
        sign = "+" if sm["net_cr"] >= 0 else "−"
        sm_html = (
            f"<p>Institutional smart money on the NSE is net <strong>{sm['direction']}</strong> "
            f"{sign}{_fmt_cr(sm['net_cr'])} over the trailing month ({sm['lead']}"
            + (f", heaviest in {_esc(sm['top_sector'])}" if sm["top_sector"] else "")
            + f"). Gross buying {_fmt_cr(sm['buy_net_cr'])} vs exits {_fmt_cr(sm['sell_net_cr'])}."
            + "</p>"
        )
        if sm["top_buys"]:
            sm_html += "<ul>" + "".join(
                f"<li>{_esc(s['symbol'])}: +{_fmt_cr(s['net_cr'])} net accumulation</li>" for s in sm["top_buys"]
            ) + "</ul>"

    val_html = ""
    val = snap.get("valuation") or {}
    val_parts = []
    if "spx_cape" in val:
        val_parts.append(f"<li>S&amp;P 500 Shiller CAPE: <strong>{val['spx_cape']}</strong> — {val['spx_cape_pct']}th percentile of all readings since 1881</li>")
    for key, label in (("btc", "Bitcoin"), ("eth", "Ethereum")):
        if f"{key}_mvrv" in val:
            val_parts.append(f"<li>{label} MVRV ratio: <strong>{val[f'{key}_mvrv']}</strong></li>")
    for key, cfg in INDICES.items():
        pe = (val.get("pe") or {}).get(key)
        if pe:
            val_parts.append(f"<li>{_esc(cfg['name'])} trailing PE: <strong>{pe['pe']}</strong> (via {_esc(pe['proxy'])})</li>")
    if val_parts:
        val_html = "<ul>" + "".join(val_parts) + "</ul>"

    headline = (
        f"Global risk gauge <strong>{snap['gauge']:.0f}/100</strong> ({_esc(snap['gauge_label'])}). "
        + _esc(" · ".join(_market_phrase(m) for m in snap["markets"])) + "."
    )
    return {
        "ASOF": as_of,
        "HEADLINE": headline,
        "TABLE": table,
        "SMARTMONEY": sm_html or "<p>Smart-money feed unavailable.</p>",
        "VALUATION": val_html or "<p>Valuation caches unavailable.</p>",
        "JSONLD": _jsonld(
            snap,
            f"Regime Compass daily brief — {snap['as_of']}",
            f"Market regime daily brief for {snap['as_of']}: risk gauge {snap['gauge']:.0f}/100 across 11 global markets, NSE smart-money flows, valuation percentiles.",
        ),
    }


# ------------------------------------------------------------------
# Page rendering
# ------------------------------------------------------------------

def render_page(filename: str, page: str):
    """Serve an HTML file with GEO placeholders filled. Falls back to the raw file."""
    path = FRONTEND_DIR / filename
    try:
        text = path.read_text(encoding="utf-8")
        for key, val in blocks_for(page).items():
            text = text.replace(f"<!--GEO:{key}-->", val)
        return HTMLResponse(text)
    except Exception as e:  # noqa: BLE001
        log.warning("[geo] render failed for %s (%s) — serving raw file", filename, e)
        return FileResponse(path)
