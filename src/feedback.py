"""Feedback + early-access waitlist storage. Uses the existing SQLite db (regime.db).

Two kinds of submission share one table:
  kind='feedback' — the one-question form ("what's missing?"); message required, email optional.
  kind='waitlist' — "sign up for what's coming next"; email required, message optional.

No verification flow: these are one-way submissions, not a mailing list. When
accounts launch, waitlist emails get a single announcement with an opt-out.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH
from .subscriptions import is_valid_email

MAX_MESSAGE_LEN = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('feedback', 'waitlist')),
    message TEXT,
    email TEXT,
    page TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_kind ON feedback(kind);
"""


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def add(kind: str, message: str | None, email: str | None, page: str | None) -> dict:
    """Validate and store one submission. Raises ValueError on bad input."""
    message = (message or "").strip()[:MAX_MESSAGE_LEN]
    email = (email or "").strip().lower()
    page = (page or "").strip()[:200]

    if kind not in ("feedback", "waitlist"):
        raise ValueError("Unknown submission kind.")
    if kind == "feedback" and not message:
        raise ValueError("Please write a line about what's missing.")
    if kind == "waitlist" and not email:
        raise ValueError("Please provide an email for early access.")
    if email and not is_valid_email(email):
        raise ValueError("That email address doesn't look valid.")

    init_db()
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    # a waitlist double-submit of the same email is a no-op, not a duplicate row
    if kind == "waitlist":
        row = conn.execute(
            "SELECT id FROM feedback WHERE kind='waitlist' AND email=?", (email,)
        ).fetchone()
        if row:
            conn.close()
            return {"ok": True, "existing": True}
    conn.execute(
        "INSERT INTO feedback (kind, message, email, page, created_at) VALUES (?,?,?,?,?)",
        (kind, message or None, email or None, page or None, now),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "existing": False}


def list_all() -> list[dict]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM feedback ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]
