"""Email subscription storage. Uses the existing SQLite db (regime.db).

Double opt-in: signups create an unverified record; a verification link must be
clicked to activate. Every subscriber has an unsubscribe_token used in the
one-click unsubscribe link at the bottom of every email.

Per-subscriber preferences control which kinds of alerts get sent.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .config import USER_DB_PATH as DB_PATH


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    verify_token TEXT NOT NULL,
    unsubscribe_token TEXT NOT NULL,
    alert_any_regime_change INTEGER NOT NULL DEFAULT 1,
    alert_bear_start INTEGER NOT NULL DEFAULT 1,
    alert_bull_start INTEGER NOT NULL DEFAULT 0,
    alert_composite_extreme INTEGER NOT NULL DEFAULT 1,
    indices_subscribed TEXT NOT NULL DEFAULT 'all',
    created_at TEXT NOT NULL,
    verified_at TEXT,
    last_email_sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_subscribers_verify_token ON subscribers(verify_token);
CREATE INDEX IF NOT EXISTS idx_subscribers_unsub_token ON subscribers(unsubscribe_token);

CREATE TABLE IF NOT EXISTS sent_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,
    alert_key TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    UNIQUE(subscriber_id, alert_key)
);
"""


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def create_subscriber(email: str, preferences: dict) -> dict:
    """Create or update a subscriber. Returns dict with verify_token and existing-flag."""
    init_db()
    email = normalize_email(email)
    if not is_valid_email(email):
        raise ValueError("Invalid email address")

    now = datetime.now(timezone.utc).isoformat()
    verify_token = secrets.token_urlsafe(32)
    unsub_token = secrets.token_urlsafe(32)

    indices = preferences.get("indices", "all")
    if isinstance(indices, list):
        indices = ",".join(indices)

    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("SELECT id, verified FROM subscribers WHERE email = ?", (email,))
        existing = cur.fetchone()
        if existing:
            sub_id, verified = existing
            # Update preferences, refresh verify_token if not yet verified
            conn.execute(
                "UPDATE subscribers SET "
                "alert_any_regime_change=?, alert_bear_start=?, alert_bull_start=?, "
                "alert_composite_extreme=?, indices_subscribed=?, "
                "verify_token=COALESCE(NULLIF(?, ''), verify_token) "
                "WHERE id=?",
                (
                    int(preferences.get("alert_any_regime_change", 1)),
                    int(preferences.get("alert_bear_start", 1)),
                    int(preferences.get("alert_bull_start", 0)),
                    int(preferences.get("alert_composite_extreme", 1)),
                    indices,
                    verify_token if not verified else "",
                    sub_id,
                ),
            )
            conn.commit()
            cur = conn.execute("SELECT verify_token, verified FROM subscribers WHERE id=?", (sub_id,))
            vt, vrf = cur.fetchone()
            return {"existing": True, "verified": bool(vrf), "verify_token": vt}

        conn.execute(
            "INSERT INTO subscribers (email, verify_token, unsubscribe_token, "
            "alert_any_regime_change, alert_bear_start, alert_bull_start, "
            "alert_composite_extreme, indices_subscribed, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                email, verify_token, unsub_token,
                int(preferences.get("alert_any_regime_change", 1)),
                int(preferences.get("alert_bear_start", 1)),
                int(preferences.get("alert_bull_start", 0)),
                int(preferences.get("alert_composite_extreme", 1)),
                indices, now,
            ),
        )
        conn.commit()
        return {"existing": False, "verified": False, "verify_token": verify_token}
    finally:
        conn.close()


def verify_subscriber(token: str) -> Optional[str]:
    """Mark a subscriber as verified. Returns the email if successful, None otherwise."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT id, email, verified FROM subscribers WHERE verify_token = ?",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        sub_id, email, verified = row
        if not verified:
            conn.execute(
                "UPDATE subscribers SET verified=1, verified_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), sub_id),
            )
            conn.commit()
        return email
    finally:
        conn.close()


def unsubscribe(token: str) -> Optional[str]:
    """Delete a subscriber. Returns the email if successful."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT id, email FROM subscribers WHERE unsubscribe_token = ?",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        sub_id, email = row
        conn.execute("DELETE FROM sent_alerts WHERE subscriber_id=?", (sub_id,))
        conn.execute("DELETE FROM subscribers WHERE id=?", (sub_id,))
        conn.commit()
        return email
    finally:
        conn.close()


def list_subscribers(only_verified: bool = True) -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where = "WHERE verified=1" if only_verified else ""
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM subscribers {where} ORDER BY id").fetchall()]
    conn.close()
    return rows


def mark_alert_sent(subscriber_id: int, alert_type: str, alert_key: str) -> bool:
    """Returns True if marked (first time), False if already sent."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        try:
            conn.execute(
                "INSERT INTO sent_alerts (subscriber_id, alert_type, alert_key, sent_at) "
                "VALUES (?, ?, ?, ?)",
                (subscriber_id, alert_type, alert_key, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    finally:
        conn.close()


def summary_stats() -> dict:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        verified = conn.execute("SELECT COUNT(*) FROM subscribers WHERE verified=1").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        alerts_sent = conn.execute("SELECT COUNT(*) FROM sent_alerts").fetchone()[0]
        return {"verified": verified, "total": total, "alerts_sent_total": alerts_sent}
    finally:
        conn.close()
