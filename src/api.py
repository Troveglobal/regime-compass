"""FastAPI server for Regime Compass."""
from __future__ import annotations

import collections
import logging
import os
import pickle
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import alerts as alerts_mod
from . import badge as badge_mod
from . import cards as cards_mod
from . import changes as changes_mod
from . import composite as composite_mod
from . import crossmarket as cross_mod
from . import news as news_mod
from . import digest as digest_mod
from . import geo
from . import email_sender
from . import seasonality as season_mod
from . import valuation as val_mod
from . import yieldcurve as yield_mod
from . import ma_backtest
from . import ma_regime
from . import credit
from . import subscriptions
from .config import (
    API_CORS_ORIGINS,
    COUNTRIES,
    DATA_DIR,
    DB_PATH,
    DEFAULT_INDEX,
    INDICES,
    STATE_LABELS,
    model_path,
    raw_path,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
_SIMPLE_CACHE: dict = {}  # in-process cache for expensive derived feeds (e.g. portfolio backtest)
SMARTMONEY_FEED = Path(__file__).resolve().parent / "smartmoney" / "out" / "feed.json"
SMARTMONEY_OUT = Path(__file__).resolve().parent / "smartmoney" / "out"
SMARTMONEY_MARKETS = ("tw", "id", "us")  # global deal-level trackers (India is /api/smartmoney)
SECTORS_FEED = Path(__file__).resolve().parent / "markets" / "out" / "sectors.json"
ANALYTICS_DIR = DATA_DIR / "analytics"

log = logging.getLogger("regime_compass")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s — %(message)s")

# ============================================================
# First-run bootstrap and scheduler
# ============================================================
_scheduler: BackgroundScheduler | None = None
_bootstrap_done = False
_bootstrap_lock = threading.Lock()


def _data_is_present() -> bool:
    """Return True if we have at least one fully populated index."""
    if not DB_PATH.exists():
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT COUNT(*) FROM probabilities").fetchone()
        conn.close()
        return row and row[0] > 0
    except sqlite3.Error:
        return False


def _run_full_pipeline() -> None:
    """Fetch + features + train + inference for all indices. Used on first run."""
    from . import features as features_mod
    from . import fetch as fetch_mod
    from . import inference as inference_mod
    from . import model as model_mod
    log.info("Full pipeline: fetching raw data ...")
    fetch_mod.fetch_all()
    log.info("Building feature matrices ...")
    features_mod.build_all()
    log.info("Training HMMs ...")
    model_mod.train_all()
    log.info("Computing filtered probability history ...")
    inference_mod.compute_history_all()
    log.info("Pipeline complete.")


def _run_smartmoney() -> None:
    """Refresh the Smart Money India feeds (NSE bulk/block deals + FII/DII flow layers)."""
    try:
        from .smartmoney import refresh as sm_refresh
        sm_refresh.refresh()
        log.info("[smartmoney] feed refreshed.")
    except Exception as e:
        log.exception("[smartmoney] refresh failed: %s", e)
    try:
        from .smartmoney import flows as sm_flows
        sm_flows.refresh()
        log.info("[smartmoney] flows feed refreshed.")
    except Exception as e:
        log.exception("[smartmoney] flows refresh failed: %s", e)
    try:
        from .smartmoney import nsdl as sm_nsdl
        sm_nsdl.refresh()  # cheap no-op unless a new fortnightly report is out
    except Exception as e:
        log.exception("[smartmoney] nsdl refresh failed: %s", e)


def _run_stakes() -> None:
    """Weekly FII stake-change refresh (BSE quarterly shareholding patterns)."""
    try:
        from .smartmoney import stakes as sm_stakes
        sm_stakes.refresh()
        log.info("[stakes] feed refreshed.")
    except Exception as e:
        log.exception("[stakes] refresh failed: %s", e)


def _run_smartmoney_market(mkt: str) -> None:
    """Refresh one global Smart Money market feed (deal-level disclosures only)."""
    try:
        from .smartmoney.markets import refresh as smg_refresh
        smg_refresh.refresh(mkt)
    except Exception as e:
        log.exception("[smartmoney:%s] refresh failed: %s", mkt, e)


def _run_congress() -> None:
    """Refresh the US Congress trading feed (Senate eFD + House Clerk PTRs)."""
    try:
        from .smartmoney.congress import pipeline as cg
        cg.refresh()
        log.info("[congress] feed refreshed.")
    except Exception as e:
        log.exception("[congress] refresh failed: %s", e)


def _run_sectors() -> None:
    """Refresh the India sector heatmap (Nifty sectoral index returns)."""
    try:
        from .markets import sectors as sec
        sec.refresh()
    except Exception as e:
        log.exception("[sectors] refresh failed: %s", e)


def _run_macro() -> None:
    """Refresh the US Macro Pane feed (FRED surprise meter + global tracker)."""
    try:
        from . import macro as macro_mod
        macro_mod.refresh()
    except Exception as e:
        log.exception("[macro] refresh failed: %s", e)


def _run_news() -> None:
    """Refresh the aggregated news feed (headlines + links only)."""
    try:
        result = news_mod.refresh()
        log.info("[news] %s", result)
    except Exception as e:
        log.exception("[news] refresh failed: %s", e)


def _run_analytics() -> None:
    """Refresh the precomputed analytics feeds (Regime Movers, sparkline
    ribbons, Systemic Risk). Each module is wrapped separately: a failure in
    any of them logs an error but never blocks the daily regime update."""
    from . import assets as assets_mod
    from . import countries as countries_mod
    from . import movers as movers_mod
    from . import sparklines as sparks_mod
    from . import systemic as systemic_mod
    for name, mod in (("movers", movers_mod), ("sparklines", sparks_mod), ("systemic", systemic_mod),
                      ("countries", countries_mod), ("assets", assets_mod)):
        try:
            mod.refresh()
        except Exception as e:
            log.exception("[analytics:%s] refresh failed: %s", name, e)


def _run_daily() -> None:
    from .alerts import detect_and_send
    from .inference import update_today_all
    try:
        update_today_all()
        log.info("Daily inference update done.")
    except Exception as e:
        log.exception("Daily inference failed: %s", e)
    _run_analytics()  # derived feeds; internally fault-isolated
    try:
        result = detect_and_send()
        log.info("Daily alerts: %s", result)
    except Exception as e:
        log.exception("Alert dispatch failed: %s", e)


def _run_weekly() -> None:
    try:
        _run_full_pipeline()
    except Exception as e:
        log.exception("Weekly retrain failed: %s", e)
    _run_analytics()  # regime history changed under the feeds — rebuild them
    try:
        result = digest_mod.send_weekly_digest()
        log.info("Weekly digest: %s", result)
    except Exception as e:
        log.exception("Weekly digest failed: %s", e)


def _bootstrap_async() -> None:
    """Run bootstrap in a background thread so the web server can start serving immediately."""
    global _bootstrap_done
    with _bootstrap_lock:
        if _bootstrap_done:
            return
        if not _data_is_present():
            log.info("[bootstrap] No regime data found — running first-run pipeline.")
            try:
                _run_full_pipeline()
            except Exception as e:
                log.exception("[bootstrap] First-run failed: %s", e)
        else:
            log.info("[bootstrap] Data present — skipping first-run pipeline.")
        if not SMARTMONEY_FEED.exists():
            log.info("[bootstrap] No Smart Money feed — generating.")
            _run_smartmoney()
        if not SECTORS_FEED.exists():
            log.info("[bootstrap] No sector heatmap — generating.")
            _run_sectors()
        if not news_mod.has_items():
            log.info("[bootstrap] No news items — fetching feeds.")
            _run_news()
        if not ANALYTICS_DIR.joinpath("movers.json").exists():
            log.info("[bootstrap] No analytics feeds — generating.")
            _run_analytics()
        if not ANALYTICS_DIR.joinpath("macro.json").exists():
            log.info("[bootstrap] No macro feed — generating.")
            _run_macro()
        _bootstrap_done = True
        log.info("[bootstrap] Ready.")


def _start_scheduler() -> None:
    """In-process scheduler. Runs alongside the FastAPI process."""
    global _scheduler
    if _scheduler is not None:
        return
    if os.getenv("DISABLE_SCHEDULER") == "1":
        log.info("[scheduler] disabled via DISABLE_SCHEDULER env var.")
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    # Daily 11:00 UTC = after US market close. Mon–Fri.
    _scheduler.add_job(_run_daily, CronTrigger(day_of_week="mon-fri", hour=11, minute=0), id="daily_update")
    # Smart Money India: 15:00 UTC = ~20:30 IST, after NSE publishes the day's bulk/block deals,
    # with a 17:00 UTC retry — NSE archives only serve the latest session, so a single
    # failed fetch would otherwise lose that day entirely.
    _scheduler.add_job(_run_smartmoney, CronTrigger(day_of_week="mon-fri", hour=15, minute=0), id="smartmoney_daily")
    _scheduler.add_job(_run_smartmoney, CronTrigger(day_of_week="mon-fri", hour=17, minute=0), id="smartmoney_retry")
    # Smart Money global — after each exchange's end-of-day disclosures:
    # Taiwan blocks 09:30 UTC (~17:30 TPE), Indonesia negotiated 11:30 UTC (~18:30 WIB),
    # US Form 4 02:30 UTC Tue-Sat (after EDGAR's 22:00 ET filing cutoff).
    _scheduler.add_job(lambda: _run_smartmoney_market("tw"),
                       CronTrigger(day_of_week="mon-fri", hour=9, minute=30), id="smartmoney_tw")
    _scheduler.add_job(lambda: _run_smartmoney_market("id"),
                       CronTrigger(day_of_week="mon-fri", hour=11, minute=30), id="smartmoney_id")
    _scheduler.add_job(lambda: _run_smartmoney_market("us"),
                       CronTrigger(day_of_week="tue-sat", hour=2, minute=30), id="smartmoney_us")
    # US Congress STOCK Act filings: 03:30 UTC after the US day's filings settle
    _scheduler.add_job(_run_congress,
                       CronTrigger(day_of_week="tue-sat", hour=3, minute=30), id="smartmoney_congress")
    # India FII stake changes: weekly sweep (daily would be wasteful outside filing season;
    # only missing (stock, quarter) cells are fetched, so this is cheap when nothing is new)
    _scheduler.add_job(_run_stakes, CronTrigger(day_of_week="sun", hour=4, minute=0), id="smartmoney_stakes")
    # India sector heatmap: 12:00 UTC = ~17:30 IST, after NSE index close is published
    _scheduler.add_job(_run_sectors, CronTrigger(day_of_week="mon-fri", hour=12, minute=0), id="sectors_daily")
    # Weekly: Sunday 03:30 UTC.
    _scheduler.add_job(_run_weekly, CronTrigger(day_of_week="sun", hour=3, minute=30), id="weekly_refit")
    # News aggregation: hourly at :20 (aggregation-only; headlines + links)
    _scheduler.add_job(_run_news, CronTrigger(minute=20), id="news_hourly")
    # US Macro Pane: 15:10 UTC mon-fri — after the 8:30 and 10:00 ET US data
    # releases have landed on FRED. One FRED pass per day (cached per series).
    _scheduler.add_job(_run_macro, CronTrigger(day_of_week="mon-fri", hour=15, minute=10), id="macro_daily")
    _scheduler.start()
    log.info("[scheduler] started (daily + weekly).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kick off bootstrap in a background thread — web server starts serving immediately
    threading.Thread(target=_bootstrap_async, daemon=True).start()
    _start_scheduler()
    yield
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Regime Compass", version="0.4.0", docs_url=None, redoc_url=None, lifespan=lifespan)


_STATIC_EXTS = {".css", ".js", ".svg", ".png", ".jpg", ".ico", ".woff2", ".woff"}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # The SmartFlow section is a same-origin embed inside /smartmoney, so it
        # must permit framing by self; the rest of the site stays DENY.
        embeddable = request.url.path.startswith("/smartflow")
        frame_ancestors = "'self'" if embeddable else "'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN" if embeddable else "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://www.googletagmanager.com https://www.google-analytics.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob: https://www.google-analytics.com https://www.googletagmanager.com; "
            "connect-src 'self' https://www.google-analytics.com https://analytics.google.com https://www.googletagmanager.com; "
            f"frame-ancestors {frame_ancestors}; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if "server" in response.headers:
            del response.headers["server"]

        path = request.url.path
        ext = os.path.splitext(path)[1].lower()
        if ext in _STATIC_EXTS:
            response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=3600"
        elif path.startswith("/api/"):
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
        else:
            response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=120"

        return response


# --------------- Rate limiter (in-memory, per-IP) ---------------
_rate_buckets: dict[str, collections.deque] = {}
_rate_lock = threading.Lock()

RATE_LIMITS = {
    "/api/subscribe": (5, 300),
    "/api/run-alerts": (3, 600),
}
GLOBAL_API_RATE = (120, 60)


def _check_rate_limit(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets.setdefault(key, collections.deque())
        while bucket and bucket[0] < now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_requests:
            retry_after = int(bucket[0] + window_seconds - now) + 1
            return False, retry_after
        bucket.append(now)
        return True, 0


def _rate_limit_response(retry_after: int) -> JSONResponse:
    return JSONResponse(
        {"error": "Rate limit exceeded. Try again later."},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path

        if path.startswith("/api/"):
            specific = RATE_LIMITS.get(path)
            if specific:
                ok, retry = _check_rate_limit(f"{path}:{client_ip}", *specific)
                if not ok:
                    return _rate_limit_response(retry)
            else:
                ok, retry = _check_rate_limit(f"api:{client_ip}", *GLOBAL_API_RATE)
                if not ok:
                    return _rate_limit_response(retry)

        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    allow_origins=API_CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


def _validate(index_key: str) -> str:
    if index_key not in INDICES:
        raise HTTPException(400, f"Unknown index '{index_key}'. Available: {list(INDICES)}")
    return index_key


def _q(sql: str, params: tuple = ()) -> list[dict]:
    if not DB_PATH.exists():
        raise HTTPException(503, "Database not initialised. Run `python -m src.inference` first.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return rows


def _load_bundle_summary(key: str) -> dict:
    path = model_path(key)
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        b = pickle.load(f)
    return {
        "feature_cols": b.feature_cols,
        "state_means": b.state_means,
        "transmat": b.transmat,
        "trained_at": b.trained_at,
        "n_train_rows": b.n_train_rows,
        "log_likelihood": b.log_likelihood,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "version": "v3.1", "bootstrap_done": _bootstrap_done, "data_present": _data_is_present()}


@app.get("/api/concordance")
def concordance_endpoint() -> dict:
    """Regime concordance across all markets — the global risk dial."""
    return cross_mod.concordance()


@app.get("/api/correlations")
def correlations_endpoint(window: int = Query(90)) -> dict:
    return cross_mod.correlations(window)


@app.get("/api/vol")
def vol_endpoint() -> dict:
    return cross_mod.vol_monitor()


@app.get("/api/news")
def news_endpoint(index: str | None = Query(None), limit: int = Query(30, ge=1, le=100)) -> dict:
    if index and index != "global" and index not in INDICES and index not in COUNTRIES:
        raise HTTPException(400, f"Unknown index '{index}'")
    return {"items": news_mod.latest(index, limit)}


@app.get("/api/composite/today")
def composite_today_endpoint() -> dict:
    return composite_mod.composite_today()


@app.get("/api/composite/history")
def composite_history_endpoint(days: int = Query(180, ge=10, le=2000)) -> dict:
    return composite_mod.composite_history(days)


@app.post("/api/subscribe")
async def subscribe_endpoint(request: Request):
    """Accept JSON or form-encoded signups."""
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)
    email = (data.get("email") or "").strip()
    if not email or not subscriptions.is_valid_email(email):
        raise HTTPException(400, "Please provide a valid email address.")

    def _flag(name, default):
        v = data.get(name, default)
        if isinstance(v, str):
            return v in ("1", "true", "on", "yes")
        return bool(v)

    prefs = {
        "alert_any_regime_change": _flag("alert_any_regime_change", True),
        "alert_bear_start": _flag("alert_bear_start", True),
        "alert_bull_start": _flag("alert_bull_start", False),
        "alert_composite_extreme": _flag("alert_composite_extreme", True),
        "indices": data.get("indices", "all"),
    }
    result = subscriptions.create_subscriber(email, prefs)

    # Send verification email (or log if API key missing)
    if not result["verified"]:
        base = str(request.base_url).rstrip("/")
        subject, html = email_sender.build_verification_email(base, result["verify_token"])
        send_res = email_sender.send_email(email, subject, html)
        return {
            "ok": True,
            "existing": result["existing"],
            "verified": False,
            "email_sent": send_res["sent"],
            "message": (
                "Almost there! Check your inbox for the verification link." if send_res["sent"]
                else "Subscription created. Verification email logged (email service not configured yet)."
            ),
        }
    return {"ok": True, "existing": result["existing"], "verified": True,
            "message": "You're already verified. Preferences updated."}


@app.get("/verify")
def verify_subscriber_endpoint(token: str):
    email = subscriptions.verify_subscriber(token)
    if not email:
        return HTMLResponse(_simple_html_page(
            "Invalid link", "This verification link is invalid or has already been used.", "Try subscribing again."
        ), status_code=404)
    return HTMLResponse(_simple_html_page(
        "Verified ✓",
        f"<strong>{email}</strong> is now confirmed. You'll receive Regime Compass alerts when matching events occur.",
        "Back to dashboard"
    ))


@app.get("/unsubscribe")
def unsubscribe_endpoint(token: str):
    email = subscriptions.unsubscribe(token)
    if not email:
        return HTMLResponse(_simple_html_page(
            "Not found", "This unsubscribe link is invalid or already used.", "Back to home"
        ), status_code=404)
    return HTMLResponse(_simple_html_page(
        "Unsubscribed",
        f"<strong>{email}</strong> has been removed. Sorry to see you go.",
        "Back to home"
    ))


_ALERTS_SECRET = os.getenv("ALERTS_API_SECRET", "")
_ADMIN_SECRET = os.getenv("ADMIN_API_SECRET", "")


def _require_admin(request: Request) -> None:
    if not _ADMIN_SECRET:
        raise HTTPException(403, "Admin API disabled — ADMIN_API_SECRET not configured.")
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {_ADMIN_SECRET}":
        raise HTTPException(403, "Invalid or missing authorization.")


@app.post("/api/run-alerts")
def run_alerts_endpoint(request: Request):
    if not _ALERTS_SECRET:
        raise HTTPException(403, "Alert trigger disabled — ALERTS_API_SECRET not configured.")
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {_ALERTS_SECRET}":
        raise HTTPException(403, "Invalid or missing authorization.")
    return alerts_mod.detect_and_send()


@app.get("/api/admin/subscribers")
def admin_subscribers(request: Request):
    _require_admin(request)
    subs = subscriptions.list_subscribers(only_verified=False)
    stats = subscriptions.summary_stats()
    return {"stats": stats, "subscribers": subs}


@app.get("/api/admin/subscribers/export")
def admin_subscribers_export(request: Request):
    _require_admin(request)
    subs = subscriptions.list_subscribers(only_verified=False)
    lines = ["email,verified,created_at,verified_at,alert_any_regime_change,alert_bear_start,alert_bull_start,alert_composite_extreme,indices_subscribed"]
    for s in subs:
        lines.append(
            f"{s['email']},{s['verified']},{s.get('created_at','')},{s.get('verified_at','')}"
            f",{s.get('alert_any_regime_change','')},{s.get('alert_bear_start','')}"
            f",{s.get('alert_bull_start','')},{s.get('alert_composite_extreme','')}"
            f",{s.get('indices_subscribed','')}"
        )
    csv_text = "\n".join(lines) + "\n"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=subscribers.csv"},
    )


@app.get("/api/admin/stats")
def admin_stats(request: Request):
    _require_admin(request)
    stats = subscriptions.summary_stats()
    fresh = freshness()
    return {"subscribers": stats, "data_freshness": fresh}


@app.get("/api/seasonality")
def seasonality_endpoint(index: str = Query(DEFAULT_INDEX)) -> dict:
    key = _validate(index)
    return season_mod.monthly_returns(key)


@app.get("/api/seasonality/summary")
def seasonality_summary_endpoint() -> list[dict]:
    return season_mod.all_seasonality()


@app.get("/api/valuation")
def valuation_endpoint(index: str = Query(DEFAULT_INDEX)) -> dict:
    key = _validate(index)
    return val_mod.valuation_data(key)


@app.get("/api/valuation/summary")
def valuation_summary_endpoint() -> list[dict]:
    return val_mod.valuation_summary()


@app.get("/api/yields/us")
def us_yields_endpoint() -> dict:
    return yield_mod.us_yield_curve()


@app.get("/api/yields/india")
def india_yields_endpoint() -> dict:
    return yield_mod.india_yield()


@app.get("/api/changes")
def changes_endpoint(days: int = Query(30, ge=1, le=365)) -> list[dict]:
    return changes_mod.recent_changes(days)


@app.get("/api/calendar")
def calendar_endpoint(index: str | None = Query(None)) -> dict:
    if index and index not in INDICES:
        raise HTTPException(400, f"Unknown index '{index}'")
    return changes_mod.calendar_data(index)


@app.get("/api/card/{index_key}")
def card_endpoint(index_key: str):
    if index_key not in INDICES:
        raise HTTPException(400, f"Unknown index '{index_key}'")
    try:
        png = cards_mod.generate_card(index_key)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/badge/{index_key}")
def badge_endpoint(index_key: str):
    if index_key not in INDICES:
        raise HTTPException(400, f"Unknown index '{index_key}'")
    svg = badge_mod.generate_badge(index_key)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/share/{index_key}")
def share_page(index_key: str):
    if index_key not in INDICES:
        raise HTTPException(404, "Unknown index")
    cfg = INDICES[index_key]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT date, bear, neutral, bull, hard_state, price_close "
        "FROM probabilities WHERE index_key = ? ORDER BY date DESC LIMIT 1",
        (index_key,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "No data")
    date, bear, neutral, bull, hard_state, price = row
    confidence = max(bear, neutral, bull) * 100
    title = f"{cfg['name']} — {hard_state.upper()} regime"
    desc = f"{cfg['name']} is in a {hard_state} regime ({confidence:.0f}% HMM confidence) as of {date}. View all 11 markets on Regime Compass."
    base = os.getenv("PUBLIC_URL", "https://www.regimecompass.com")
    card_url = f"{base}/api/card/{index_key}"
    page_url = f"{base}/share/{index_key}"
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{title} — Regime Compass</title>
<meta name="description" content="{desc}"/>
<meta property="og:type" content="website"/>
<meta property="og:site_name" content="Regime Compass"/>
<meta property="og:title" content="{title}"/>
<meta property="og:description" content="{desc}"/>
<meta property="og:image" content="{card_url}"/>
<meta property="og:image:width" content="1200"/>
<meta property="og:image:height" content="630"/>
<meta property="og:url" content="{page_url}"/>
<meta name="twitter:card" content="summary_large_image"/>
<meta name="twitter:title" content="{title}"/>
<meta name="twitter:description" content="{desc}"/>
<meta name="twitter:image" content="{card_url}"/>
<link rel="canonical" href="{page_url}"/>
<link rel="stylesheet" href="/styles.css"/>
<link rel="icon" type="image/svg+xml" href="/favicon.svg"/>
<meta http-equiv="refresh" content="3;url=/hmm?index={index_key}"/>
</head><body>
<div class="wrap" style="text-align:center;padding-top:60px;">
<img src="/api/card/{index_key}" alt="{title}" style="max-width:100%;border-radius:12px;margin-bottom:24px;"/>
<p style="color:var(--muted);font-size:14px;">Redirecting to dashboard...</p>
</div>
</body></html>"""
    return HTMLResponse(html)


def _simple_html_page(title: str, body: str, cta_label: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<title>{title} — Regime Compass</title>
<link rel="stylesheet" href="/styles.css"/>
<link rel="icon" type="image/svg+xml" href="/favicon.svg"/>
</head><body>
<nav class="top"><div class="inner">
<a class="brand" href="/"><svg class="logo-svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" class="ring" fill="none" stroke="currentColor" stroke-width="0.6" opacity="0.5"/><path d="M12 2.5 L9.6 11 L14.4 11 Z" fill="#27ae60"/><path d="M12 21.5 L9.6 13 L14.4 13 Z" fill="#c0392b"/><rect x="19" y="11.4" width="2.7" height="1.2" fill="#d4a017"/><rect x="2.3" y="11.4" width="2.7" height="1.2" fill="#d4a017"/><circle cx="12" cy="12" r="1.4" class="needle" fill="currentColor"/></svg> Regime Compass</a>
</div></nav>
<div class="wrap narrow" style="text-align:center;padding-top:60px;">
<div class="hero"><h1 style="font-size:48px;">{title}</h1></div>
<p style="font-size:16px;color:#c9d1d9;margin:0 auto 30px;max-width:480px;">{body}</p>
<a href="/" style="background:var(--accent);color:var(--bg);padding:10px 22px;border-radius:8px;font-weight:600;text-decoration:none;display:inline-block;">{cta_label} →</a>
</div></body></html>"""


@app.get("/api/freshness")
def freshness() -> dict:
    """Report when data for each index was last refreshed (file mtime of raw.parquet)."""
    import time
    from .config import raw_path as _rp
    now = time.time()
    out = {"indices": [], "max_age_hours": 0.0, "any_stale": False}
    worst_age_hours = 0.0
    for key, cfg in INDICES.items():
        path = _rp(key)
        if not path.exists():
            out["indices"].append({"index_key": key, "fresh": False, "age_hours": None})
            continue
        age_sec = now - path.stat().st_mtime
        age_hours = age_sec / 3600
        if age_hours > worst_age_hours:
            worst_age_hours = age_hours
        out["indices"].append({
            "index_key": key,
            "index_name": cfg["name"],
            "age_hours": round(age_hours, 1),
            # Fresh if updated within last 48 hours (accounts for weekends)
            "fresh": age_hours < 48,
        })
    out["max_age_hours"] = round(worst_age_hours, 1)
    out["any_stale"] = worst_age_hours >= 72  # 3 trading days
    return out


@app.get("/api/indices")
def indices() -> list[dict]:
    return [
        {
            "key": key,
            "name": cfg["name"],
            "country": cfg["country"],
            "currency": cfg["currency"],
            "has_vix": cfg["tickers"].get("vix") is not None,
            "has_fx": cfg["tickers"].get("fx") is not None,
        }
        for key, cfg in INDICES.items()
    ]


@app.get("/api/hmm/snapshot")
def hmm_snapshot() -> dict:
    """Today's HMM regime for every index. Used by the home overview grid."""
    out = {"indices": [], "state_labels": STATE_LABELS}
    for key in INDICES:
        try:
            t = today(index=key)
            out["indices"].append({
                "index_key": key,
                "index_name": INDICES[key]["name"],
                "country": INDICES[key]["country"],
                "currency": INDICES[key]["currency"],
                "date": t.get("date"),
                "bear": t.get("bear"),
                "neutral": t.get("neutral"),
                "bull": t.get("bull"),
                "hard_state": t.get("hard_state"),
                "days_in_regime": t.get("days_in_regime"),
                "price_close": t.get("price_close"),
            })
        except HTTPException:
            pass
    return out


@app.get("/api/today")
def today(index: str = Query(DEFAULT_INDEX)) -> dict:
    key = _validate(index)
    rows = _q("SELECT * FROM probabilities WHERE index_key = ? ORDER BY date DESC LIMIT 1", (key,))
    if not rows:
        raise HTTPException(404, f"no data for {key}")
    latest = rows[0]
    state = latest["hard_state"]
    streak = _q(
        "SELECT date, hard_state FROM probabilities WHERE index_key = ? AND date <= ? "
        "ORDER BY date DESC LIMIT 365",
        (key, latest["date"]),
    )
    days_in_regime = 0
    for r in streak:
        if r["hard_state"] == state:
            days_in_regime += 1
        else:
            break
    meta_rows = _q("SELECT key, value FROM metadata WHERE index_key = ?", (key,))
    meta = {r["key"]: r["value"] for r in meta_rows}
    cfg = INDICES[key]
    return {
        **latest,
        "days_in_regime": days_in_regime,
        "index_name": cfg["name"],
        "index_country": cfg["country"],
        "index_currency": cfg["currency"],
        "last_full_rebuild": meta.get("last_full_rebuild"),
        "last_daily_update": meta.get("last_daily_update"),
        "state_labels": STATE_LABELS,
    }


@app.get("/api/history")
def history(index: str = Query(DEFAULT_INDEX), days: int = Query(90, ge=1, le=4500)) -> list[dict]:
    key = _validate(index)
    rows = _q(
        "SELECT date, bear, neutral, bull, hard_state, price_close FROM probabilities "
        "WHERE index_key = ? ORDER BY date DESC LIMIT ?",
        (key, days),
    )
    rows.reverse()
    return rows


@app.get("/api/regime_runs")
def regime_runs(index: str = Query(DEFAULT_INDEX), min_days: int = Query(10, ge=1)) -> list[dict]:
    key = _validate(index)
    rows = _q(
        "SELECT date, hard_state, price_close FROM probabilities WHERE index_key = ? ORDER BY date",
        (key,),
    )
    runs = []
    if not rows:
        return runs
    cur_state = rows[0]["hard_state"]
    cur_start_date = rows[0]["date"]
    cur_start_close = rows[0]["price_close"]
    last_date = rows[0]["date"]
    last_close = rows[0]["price_close"]
    import datetime as _dt

    def days_between(s: str, e: str) -> int:
        return (_dt.date.fromisoformat(e) - _dt.date.fromisoformat(s)).days + 1

    for r in rows[1:]:
        if r["hard_state"] != cur_state:
            d = days_between(cur_start_date, last_date)
            if d >= min_days:
                runs.append({
                    "state": cur_state,
                    "start": cur_start_date,
                    "end": last_date,
                    "days": d,
                    "return_pct": (last_close / cur_start_close - 1.0) * 100,
                })
            cur_state = r["hard_state"]
            cur_start_date = r["date"]
            cur_start_close = r["price_close"]
        last_date = r["date"]
        last_close = r["price_close"]
    d = days_between(cur_start_date, last_date)
    if d >= min_days:
        runs.append({
            "state": cur_state,
            "start": cur_start_date,
            "end": last_date,
            "days": d,
            "return_pct": (last_close / cur_start_close - 1.0) * 100,
        })
    return runs


# Moving-average regime endpoints (simple price-vs-DMA classifier)
@app.get("/api/ma/snapshot")
def ma_snapshot_endpoint() -> dict:
    return ma_regime.snapshot()


@app.get("/api/ma/today")
def ma_today_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200)) -> dict:
    key = _validate(index)
    return ma_regime.today(key, period)


@app.get("/api/ma/history")
def ma_history_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200),
                         days: int = Query(365, ge=1, le=4500)) -> list[dict]:
    key = _validate(index)
    return ma_regime.history(key, period, days)


@app.get("/api/ma/runs")
def ma_runs_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200),
                     min_days: int = Query(5, ge=1)) -> list[dict]:
    key = _validate(index)
    return ma_regime.regime_runs(key, period, min_days)


@app.get("/api/ma/stats")
def ma_stats_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200)) -> dict:
    key = _validate(index)
    return ma_regime.stats(key, period)


@app.get("/api/ma/backtest")
def ma_backtest_endpoint(
    index: str = Query(DEFAULT_INDEX),
    period: int = Query(200),
    confirm_days: int = Query(2, ge=1, le=10),
) -> dict:
    key = _validate(index)
    return ma_backtest.backtest(key, period, confirm_days, kind="sma")


# Exponential moving-average regime endpoints (same logic, EMA instead of SMA)
@app.get("/api/ema/snapshot")
def ema_snapshot_endpoint() -> dict:
    return ma_regime.snapshot(kind="ema")


@app.get("/api/ema/today")
def ema_today_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200)) -> dict:
    key = _validate(index)
    return ma_regime.today(key, period, kind="ema")


@app.get("/api/ema/history")
def ema_history_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200),
                          days: int = Query(365, ge=1, le=4500)) -> list[dict]:
    key = _validate(index)
    return ma_regime.history(key, period, days, kind="ema")


@app.get("/api/ema/runs")
def ema_runs_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200),
                      min_days: int = Query(5, ge=1)) -> list[dict]:
    key = _validate(index)
    return ma_regime.regime_runs(key, period, min_days, kind="ema")


@app.get("/api/ema/stats")
def ema_stats_endpoint(index: str = Query(DEFAULT_INDEX), period: int = Query(200)) -> dict:
    key = _validate(index)
    return ma_regime.stats(key, period, kind="ema")


@app.get("/api/ema/backtest")
def ema_backtest_endpoint(
    index: str = Query(DEFAULT_INDEX),
    period: int = Query(200),
    confirm_days: int = Query(2, ge=1, le=10),
) -> dict:
    key = _validate(index)
    return ma_backtest.backtest(key, period, confirm_days, kind="ema")


# Credit stress (US High-Yield OAS) — gauge, history, conditioned equity overlay
@app.get("/api/credit/gauge")
def credit_gauge_endpoint(
    kind: str = Query("sma", regex="^(sma|ema)$"),
    period: int = Query(200),
) -> dict:
    if period not in (50, 100, 200):
        period = 200
    return credit.gauge(kind=kind, period=period)


@app.get("/api/credit/history")
def credit_history_endpoint(days: int = Query(500, ge=30, le=800)) -> dict:
    return credit.history(days)


@app.get("/api/credit/backtest")
def credit_backtest_endpoint(
    kind: str = Query("sma", regex="^(sma|ema)$"),
    period: int = Query(200),
    series: str = Query("baa", regex="^(baa|hy)$"),
) -> dict:
    if period not in (50, 100, 200):
        period = 200
    return credit.overlay_backtest(kind=kind, period=period, series=series)


# Analytics feeds (Regime Movers, sparkline ribbons, Systemic Risk) —
# precomputed by the daily pipeline (src/movers.py, src/sparklines.py,
# src/systemic.py), served straight from data/analytics/.
def _analytics_feed(name: str) -> Response:
    path = ANALYTICS_DIR / f"{name}.json"
    if not path.exists():
        _run_analytics()
    if path.exists():
        return FileResponse(path, media_type="application/json")
    return JSONResponse({"error": f"{name} feed unavailable"}, status_code=503)


@app.get("/api/movers")
def movers_feed() -> Response:
    return _analytics_feed("movers")


@app.get("/api/sparklines")
def sparklines_feed() -> Response:
    return _analytics_feed("sparklines")


@app.get("/api/systemic")
def systemic_feed() -> Response:
    return _analytics_feed("systemic")


@app.get("/api/countries")
def countries_feed() -> Response:
    return _analytics_feed("countries")


@app.get("/api/assets")
def assets_feed() -> Response:
    return _analytics_feed("assets")


@app.get("/api/macro")
def macro_feed() -> Response:
    path = ANALYTICS_DIR / "macro.json"
    if not path.exists():
        _run_macro()
    if path.exists():
        return FileResponse(path, media_type="application/json")
    return JSONResponse({"error": "macro feed unavailable"}, status_code=503)


# HMM validation (walk-forward accuracy audit) — precomputed by scripts/hmm_backtest.py,
# served straight from data/validation/. Regenerate by rerunning the script.
VALIDATION_DIR = DATA_DIR / "validation"


@app.get("/api/validation")
def validation_summary() -> Response:
    path = VALIDATION_DIR / "summary.json"
    if not path.exists():
        return JSONResponse({"error": "validation data not generated"}, status_code=503)
    return FileResponse(path, media_type="application/json")


@app.get("/api/validation/{index}")
def validation_detail(index: str) -> Response:
    key = _validate(index)
    path = VALIDATION_DIR / f"{key}.json"
    if not path.exists():
        return JSONResponse({"error": "validation data not generated"}, status_code=503)
    return FileResponse(path, media_type="application/json")


@app.get("/api/model_info")
def model_info(index: str = Query(DEFAULT_INDEX)) -> dict:
    key = _validate(index)
    cfg = INDICES[key]
    summary = _load_bundle_summary(key)
    return {
        "index_key": key,
        "index_name": cfg["name"],
        "tickers": cfg["tickers"],
        **summary,
    }


if FRONTEND_DIR.exists():
    # GEO: data pages are served with a server-rendered "today at a glance"
    # block injected so non-JS AI crawlers see live numbers (src/geo.py;
    # falls back to the raw file on any error).
    @app.get("/")
    def index_page():
        return geo.render_page("index.html", "index")

    @app.get("/today")
    def today_page():
        return geo.render_page("today.html", "today")

    @app.get("/about")
    def about_page():
        return FileResponse(FRONTEND_DIR / "about.html")

    @app.get("/methodology")
    def methodology_page():
        return FileResponse(FRONTEND_DIR / "methodology.html")

    @app.get("/validation")
    def validation_page():
        return FileResponse(FRONTEND_DIR / "validation.html")

    @app.get("/hmm")
    def hmm_page():
        return geo.render_page("hmm.html", "hmm")

    @app.get("/ma")
    def ma_page():
        return geo.render_page("ma.html", "ma")

    @app.get("/ma/backtest")
    def ma_backtest_page():
        return FileResponse(FRONTEND_DIR / "ma_backtest.html")

    @app.get("/ema")
    def ema_page():
        return geo.render_page("ema.html", "ema")

    @app.get("/ema/backtest")
    def ema_backtest_page():
        return FileResponse(FRONTEND_DIR / "ema_backtest.html")

    @app.get("/credit")
    def credit_page():
        return FileResponse(FRONTEND_DIR / "credit.html")

    @app.get("/composite")
    def composite_page():
        return geo.render_page("composite.html", "composite")

    @app.get("/subscribe")
    def subscribe_page():
        return FileResponse(FRONTEND_DIR / "subscribe.html")

    @app.get("/disclaimer")
    def disclaimer_page():
        return FileResponse(FRONTEND_DIR / "disclaimer.html")

    @app.get("/privacy")
    def privacy_page():
        return FileResponse(FRONTEND_DIR / "privacy.html")

    @app.get("/terms")
    def terms_page():
        return FileResponse(FRONTEND_DIR / "terms.html")

    @app.get("/changes")
    def changes_page():
        return FileResponse(FRONTEND_DIR / "changes.html")

    @app.get("/calendar")
    def calendar_page():
        return FileResponse(FRONTEND_DIR / "calendar.html")

    @app.get("/embed")
    def embed_page():
        return FileResponse(FRONTEND_DIR / "embed.html")

    @app.get("/seasonality")
    def seasonality_page():
        return FileResponse(FRONTEND_DIR / "seasonality.html")

    @app.get("/smartmoney")
    def smartmoney_page():
        return geo.render_page("smartmoney.html", "smartmoney")

    @app.get("/smartflow")
    @app.get("/smartflow/")
    def smartflow_redirect():
        # The old Next.js SmartFlow embed is retired; it lives natively at /smartmoney now.
        return RedirectResponse("/smartmoney", status_code=301)

    @app.get("/api/smartmoney")
    def smartmoney_feed():
        # Live feed for the Smart Money India dashboard; refreshed daily by the scheduler.
        if not SMARTMONEY_FEED.exists():
            _run_smartmoney()
        if SMARTMONEY_FEED.exists():
            return FileResponse(SMARTMONEY_FEED, media_type="application/json")
        return JSONResponse({"error": "feed unavailable"}, status_code=503)

    @app.get("/api/smartmoney/summary")
    def smartmoney_summary():
        """Tiny cross-market digest for the home page — one line per market,
        computed from the feed files on disk and cached by mtime."""
        import json as _json
        out = {}
        try:
            if SMARTMONEY_FEED.exists():
                f = _json.loads(SMARTMONEY_FEED.read_text())
                b, s = f["month"]["buy"]["kpi"], f["month"]["sell"]["kpi"]
                flav = f["month"]["buy"]["flavour"]
                out["in"] = {"net_cr": round(b["net_cr"] - s["net_cr"], 1),
                             "top_sector": flav[0]["sector"] if flav else None,
                             "latest": f["meta"]["latest_date"]}
            for mkt in ("tw", "id"):
                p = SMARTMONEY_OUT / f"feed_{mkt}.json"
                if p.exists():
                    f = _json.loads(p.read_text())
                    k = f["month"]["kpi"]
                    top = f["month"]["stocks"][0] if f["month"]["stocks"] else None
                    out[mkt] = {"total": k["total_value"], "unit": f["meta"]["unit"],
                                "top": (top or {}).get("security") or (top or {}).get("symbol"),
                                "latest": f["meta"]["latest_date"]}
            p = SMARTMONEY_OUT / "feed_us.json"
            if p.exists():
                f = _json.loads(p.read_text())
                out["us"] = {"buy": f["month"]["buy"]["kpi"]["total_value"],
                             "sell": f["month"]["sell"]["kpi"]["total_value"],
                             "latest": f["meta"]["latest_date"]}
            p = SMARTMONEY_OUT / "feed_congress.json"
            if p.exists():
                f = _json.loads(p.read_text())
                tb = f["overview"]["top_bought"]
                out["congress"] = {"top": tb[0]["symbol"] if tb else None,
                                   "n_pol": f["meta"]["n_politicians"]}
        except Exception as e:  # noqa: BLE001 — a partial summary beats a 500
            log.warning("[smartmoney] summary build hiccup: %s", e)
        return JSONResponse(out, headers={"Cache-Control": "public, max-age=600"})

    @app.get("/api/smartmoney/inflows")
    def smartmoney_inflows_feed():
        # India market-wide FII/DII flow layers (cash provisional + F&O positioning)
        feed = SMARTMONEY_OUT / "feed_inflows.json"
        if not feed.exists():
            _run_smartmoney()
        if feed.exists():
            return FileResponse(feed, media_type="application/json")
        return JSONResponse({"error": "feed unavailable"}, status_code=503)

    @app.get("/api/smartmoney/fpisectors")
    def smartmoney_fpisectors_feed():
        # NSDL fortnightly FPI sector-wise equity flows
        feed = SMARTMONEY_OUT / "feed_fpisectors.json"
        if feed.exists():
            return FileResponse(feed, media_type="application/json")
        return JSONResponse({"error": "feed unavailable"}, status_code=503)

    @app.get("/api/smartmoney/stakes")
    def smartmoney_stakes_feed():
        # Quarterly FII stake changes across the Nifty 500
        feed = SMARTMONEY_OUT / "feed_stakes.json"
        if feed.exists():
            return FileResponse(feed, media_type="application/json")
        return JSONResponse({"error": "feed unavailable"}, status_code=503)

    @app.get("/api/smartmoney/portfolio")
    def smartmoney_portfolio(mode: str = Query("refined", regex="^(refined|raw)$")):
        # Backtested Smart Money portfolio. mode=refined applies momentum + regime overlays.
        # Cached in-process; the underlying data refreshes daily via the scheduler.
        try:
            from .smartmoney import portfolio as sm_portfolio
            key = "sm_portfolio_v2_" + mode
            cached = _SIMPLE_CACHE.get(key)
            if cached is None:
                cached = sm_portfolio.backtest(mode=mode)
                _SIMPLE_CACHE[key] = cached
            return cached
        except Exception as e:
            log.exception("[smartmoney] portfolio failed: %s", e)
            return JSONResponse({"error": "portfolio unavailable"}, status_code=503)

    @app.get("/smartmoney/{mkt}")
    def smartmoney_market_page(mkt: str):
        if mkt == "congress":
            # Congress lives as a view inside the US page
            return RedirectResponse("/smartmoney/us#congress", status_code=301)
        if mkt not in SMARTMONEY_MARKETS:
            return geo.render_page("404.html", "404")
        return geo.render_page("smartmoney-global.html", "smartmoney")

    @app.get("/api/smartmoney/{mkt}")
    def smartmoney_market_feed(mkt: str):
        if mkt not in SMARTMONEY_MARKETS and mkt != "congress":
            return JSONResponse({"error": "unknown market"}, status_code=404)
        feed = SMARTMONEY_OUT / f"feed_{mkt}.json"
        if not feed.exists():
            _run_smartmoney_market(mkt)
        if feed.exists():
            return FileResponse(feed, media_type="application/json")
        return JSONResponse({"error": "feed unavailable"}, status_code=503)

    @app.get("/sectors")
    def sectors_page():
        return FileResponse(FRONTEND_DIR / "sectors.html")

    @app.get("/api/sectors")
    def sectors_feed():
        if not SECTORS_FEED.exists():
            _run_sectors()
        if SECTORS_FEED.exists():
            return FileResponse(SECTORS_FEED, media_type="application/json")
        return JSONResponse({"error": "feed unavailable"}, status_code=503)

    @app.get("/valuation")
    def valuation_page():
        return geo.render_page("valuation.html", "valuation")

    @app.get("/correlations")
    def correlations_page():
        return FileResponse(FRONTEND_DIR / "correlations.html")

    @app.get("/volatility")
    def volatility_page():
        return FileResponse(FRONTEND_DIR / "volatility.html")

    @app.get("/movers")
    def movers_page():
        return FileResponse(FRONTEND_DIR / "movers.html")

    @app.get("/systemic")
    def systemic_page():
        return FileResponse(FRONTEND_DIR / "systemic.html")

    @app.get("/news")
    def news_page():
        return FileResponse(FRONTEND_DIR / "news.html")

    @app.get("/yields")
    def yields_page():
        return FileResponse(FRONTEND_DIR / "yields.html")

    @app.get("/macro")
    def macro_page():
        return FileResponse(FRONTEND_DIR / "macro.html")

    @app.get("/countries")
    def countries_page():
        return FileResponse(FRONTEND_DIR / "countries.html")

    @app.get("/country/{slug}")
    def country_page(slug: str):
        if slug not in COUNTRIES:
            return FileResponse(FRONTEND_DIR / "404.html", status_code=404)
        return FileResponse(FRONTEND_DIR / "country.html")

    @app.get("/assets")
    def assets_page():
        return FileResponse(FRONTEND_DIR / "assets.html")

    @app.get("/asset/{slug}")
    def asset_page(slug: str):
        from .config import ASSETS
        if slug not in ASSETS:
            return FileResponse(FRONTEND_DIR / "404.html", status_code=404)
        return FileResponse(FRONTEND_DIR / "asset.html")

    @app.get("/robots.txt")
    def robots():
        return FileResponse(FRONTEND_DIR / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        # Generated so daily-data pages carry a fresh <lastmod> (AI/search freshness signal).
        base = "https://www.regimecompass.com"
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        evergreen = "2026-07-05"
        daily = [("/", "1.0"), ("/today", "1.0"), ("/composite", "0.9"), ("/hmm", "0.9"),
                 ("/ma", "0.9"), ("/ema", "0.9"), ("/smartmoney", "0.9"), ("/valuation", "0.9"),
                 ("/movers", "0.9"), ("/systemic", "0.8"),
                 ("/correlations", "0.8"), ("/volatility", "0.8"), ("/news", "0.8"),
                 ("/changes", "0.8"), ("/yields", "0.8"), ("/sectors", "0.8"), ("/macro", "0.8"),
                 ("/countries", "0.8"), ("/assets", "0.8")] \
                + [(f"/country/{s}", "0.7") for s in COUNTRIES] \
                + [(f"/asset/{s}", "0.7") for s in ("bitcoin", "ethereum", "gold", "silver")]
        weekly = [("/ma/backtest", "0.7"), ("/ema/backtest", "0.7"), ("/calendar", "0.7"),
                  ("/seasonality", "0.7"), ("/validation", "0.7")]
        stable = [("/subscribe", "0.6"), ("/about", "0.5"), ("/methodology", "0.6"), ("/embed", "0.4"),
                  ("/disclaimer", "0.3"), ("/terms", "0.3"), ("/privacy", "0.3")]
        entries = []
        for path, prio in daily:
            entries.append((path, today_str, "daily", prio))
        for path, prio in weekly:
            entries.append((path, today_str, "weekly", prio))
        for path, prio in stable:
            entries.append((path, evergreen, "monthly", prio))
        body = "".join(
            f"<url><loc>{base}{p}</loc><lastmod>{lm}</lastmod>"
            f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>"
            for p, lm, cf, pr in entries
        )
        xml = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + body + "</urlset>")
        return Response(content=xml, media_type="application/xml")

    # Custom 404 — replaces FastAPI's default JSON Not Found
    @app.exception_handler(404)
    async def not_found_handler(request, exc):
        return FileResponse(FRONTEND_DIR / "404.html", status_code=404)

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
