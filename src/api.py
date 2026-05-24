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
from . import digest as digest_mod
from . import email_sender
from . import seasonality as season_mod
from . import valuation as val_mod
from . import yieldcurve as yield_mod
from . import ma_backtest
from . import ma_regime
from . import subscriptions
from .config import (
    API_CORS_ORIGINS,
    DB_PATH,
    DEFAULT_INDEX,
    INDICES,
    STATE_LABELS,
    model_path,
    raw_path,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

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


def _run_daily() -> None:
    from .alerts import detect_and_send
    from .inference import update_today_all
    try:
        update_today_all()
        log.info("Daily inference update done.")
    except Exception as e:
        log.exception("Daily inference failed: %s", e)
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
    # Daily 11:00 UTC = 16:30 IST = 19:00 SGT. Mon–Fri after NSE close.
    _scheduler.add_job(_run_daily, CronTrigger(day_of_week="mon-fri", hour=11, minute=0), id="daily_update")
    # Weekly: Sunday 03:30 UTC.
    _scheduler.add_job(_run_weekly, CronTrigger(day_of_week="sun", hour=3, minute=30), id="weekly_refit")
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
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
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
            "frame-ancestors 'none'; "
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
    desc = f"{cfg['name']} is in a {hard_state} regime ({confidence:.0f}% HMM confidence) as of {date}. View all 6 markets on Regime Compass."
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
    @app.get("/about")
    def about_page():
        return FileResponse(FRONTEND_DIR / "about.html")

    @app.get("/methodology")
    def methodology_page():
        return FileResponse(FRONTEND_DIR / "methodology.html")

    @app.get("/hmm")
    def hmm_page():
        return FileResponse(FRONTEND_DIR / "hmm.html")

    @app.get("/ma")
    def ma_page():
        return FileResponse(FRONTEND_DIR / "ma.html")

    @app.get("/ma/backtest")
    def ma_backtest_page():
        return FileResponse(FRONTEND_DIR / "ma_backtest.html")

    @app.get("/ema")
    def ema_page():
        return FileResponse(FRONTEND_DIR / "ema.html")

    @app.get("/ema/backtest")
    def ema_backtest_page():
        return FileResponse(FRONTEND_DIR / "ema_backtest.html")

    @app.get("/composite")
    def composite_page():
        return FileResponse(FRONTEND_DIR / "composite.html")

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

    @app.get("/valuation")
    def valuation_page():
        return FileResponse(FRONTEND_DIR / "valuation.html")

    @app.get("/yields")
    def yields_page():
        return FileResponse(FRONTEND_DIR / "yields.html")

    @app.get("/robots.txt")
    def robots():
        return FileResponse(FRONTEND_DIR / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        return FileResponse(FRONTEND_DIR / "sitemap.xml", media_type="application/xml")

    # Custom 404 — replaces FastAPI's default JSON Not Found
    @app.exception_handler(404)
    async def not_found_handler(request, exc):
        return FileResponse(FRONTEND_DIR / "404.html", status_code=404)

    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
