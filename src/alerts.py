"""Alert detection + dispatch.

Runs after each daily update. Inspects what changed and emails matching subscribers.

Alert types:
  - REGIME_CHANGE: any HMM regime flip on any subscribed index
  - BEAR_START: HMM regime entered bear
  - BULL_START: HMM regime entered bull
  - COMPOSITE_EXTREME: risk-on/off composite crossed 30 or 70

De-duplicated via the sent_alerts table — same alert_key won't be sent twice.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterable, Optional

import pandas as pd

from . import composite as composite_mod
from . import email_sender
from . import subscriptions
from .config import DB_PATH, INDICES

BASE_URL = "https://regime.iquantlabs.com"


def _format_index_alert(index_name: str, new_state: str, days: int, prev_state: Optional[str]) -> str:
    color = {"bear": "#e35454", "neutral": "#d4a017", "bull": "#34c673"}.get(new_state, "#8b95a3")
    arrow = f" (was {prev_state})" if prev_state else ""
    return (
        f'<div style="margin:14px 0;padding:14px 16px;background:#161b22;border:1px solid #232830;border-radius:8px;">'
        f'<div style="font-weight:600;color:#fff;font-size:15px;">{index_name}</div>'
        f'<div style="margin-top:6px;">'
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;background:{color};color:white;font-weight:700;text-transform:uppercase;letter-spacing:1px;font-size:11px;">{new_state}</span>'
        f'<span style="color:#8b95a3;font-size:13px;margin-left:10px;">{days}d in regime{arrow}</span>'
        f"</div></div>"
    )


def _detect_regime_changes() -> list[dict]:
    """Find recent HMM regime changes (within the last 2 days).

    Returns a list of {"index_key", "index_name", "new_state", "prev_state", "date", "days_in_regime"}.
    """
    conn = sqlite3.connect(DB_PATH)
    changes = []
    for key, cfg in INDICES.items():
        rows = conn.execute(
            "SELECT date, hard_state FROM probabilities WHERE index_key=? "
            "ORDER BY date DESC LIMIT 30",
            (key,),
        ).fetchall()
        if len(rows) < 2:
            continue
        latest_date, latest_state = rows[0]
        prev_state = rows[1][1] if len(rows) > 1 else None
        # Count days in current regime
        days = 1
        for d, s in rows[1:]:
            if s == latest_state:
                days += 1
            else:
                break
        # If days_in_regime <= 2, the regime is fresh
        if days <= 2 and prev_state != latest_state:
            changes.append({
                "index_key": key,
                "index_name": cfg["name"],
                "date": latest_date,
                "new_state": latest_state,
                "prev_state": prev_state,
                "days_in_regime": days,
            })
    conn.close()
    return changes


def _detect_composite_extreme() -> Optional[dict]:
    """If today's composite gauge is <30 or >70, flag it for alerts."""
    today = composite_mod.composite_today()
    gauge = today["gauge"]
    if gauge >= 70:
        return {"gauge": gauge, "label": today["regime_label"], "side": "risk-on", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    if gauge <= 30:
        return {"gauge": gauge, "label": today["regime_label"], "side": "risk-off", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    return None


def _subscriber_filters_index(sub: dict, index_key: str) -> bool:
    indices = sub.get("indices_subscribed", "all")
    if indices == "all" or not indices:
        return True
    return index_key in [s.strip() for s in indices.split(",")]


def detect_and_send() -> dict:
    """Run alert detection. Send matching emails to verified subscribers.

    Returns a summary dict.
    """
    changes = _detect_regime_changes()
    extreme = _detect_composite_extreme()
    subs = subscriptions.list_subscribers(only_verified=True)

    sent_count = 0
    skip_count = 0
    error_count = 0
    composite_sent = 0

    for sub in subs:
        # Per-subscriber alert content
        relevant_changes = [c for c in changes if _subscriber_filters_index(sub, c["index_key"])]
        # Filter by user preferences
        if not sub["alert_any_regime_change"]:
            relevant_changes = [c for c in relevant_changes if (
                (c["new_state"] == "bear" and sub["alert_bear_start"])
                or (c["new_state"] == "bull" and sub["alert_bull_start"])
            )]
        else:
            # Honor at least: bear_start and bull_start preferences when any_regime is also on
            pass

        composite_relevant = extreme is not None and sub["alert_composite_extreme"]

        if not relevant_changes and not composite_relevant:
            continue

        # Build a single digest email per subscriber for today's events
        sections = []
        if relevant_changes:
            sections.append(
                '<h2 style="font-size:18px;color:#fff;margin:0 0 6px;">Regime changes today</h2>'
                + "".join(_format_index_alert(c["index_name"], c["new_state"], c["days_in_regime"], c["prev_state"]) for c in relevant_changes)
            )
        if composite_relevant:
            color = "#34c673" if extreme["side"] == "risk-on" else "#e35454"
            sections.append(
                '<h2 style="font-size:18px;color:#fff;margin:18px 0 6px;">Composite extreme</h2>'
                f'<div style="margin:12px 0;padding:14px 16px;background:#161b22;border:1px solid #232830;border-radius:8px;">'
                f'<div style="color:#fff;font-weight:600;font-size:15px;">Risk-on/off gauge at {extreme["gauge"]:.0f}</div>'
                f'<div style="margin-top:6px;color:#8b95a3;font-size:13px;">'
                f'<span style="color:{color};font-weight:600;">{extreme["side"].upper()}</span> &mdash; markets are unusually one-sided. '
                f'See the <a href="{BASE_URL}/composite" style="color:#6ec1ff;">composite</a>.'
                f'</div></div>'
            )

        # De-dup key per event
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        alert_keys = []
        for c in relevant_changes:
            alert_keys.append(f"regime:{c['index_key']}:{c['new_state']}:{today_str}")
        if composite_relevant:
            alert_keys.append(f"composite:{extreme['side']}:{today_str}")

        # Only send if at least one key is novel
        novel_keys = [k for k in alert_keys if subscriptions.mark_alert_sent(sub["id"], k.split(":")[0], k)]
        if not novel_keys:
            skip_count += 1
            continue

        body = (
            '<h1 style="font-size:22px;color:#fff;margin:0 0 12px;">Today\'s alerts</h1>'
            + "".join(sections)
            + f'<p style="margin-top:20px;color:#8b95a3;font-size:13px;">View the live dashboards at <a href="{BASE_URL}" style="color:#6ec1ff;">regime.iquantlabs.com</a>.</p>'
        )
        subject = f"Regime Compass alert — {today_str}"
        html = email_sender._wrap(body) + f'<p style="margin-top:24px;color:#8b95a3;font-size:12px;text-align:center;">You\'re receiving this because you subscribed to Regime Compass alerts. <a href="{BASE_URL}/unsubscribe?token={sub["unsubscribe_token"]}" style="color:#6ec1ff;">Unsubscribe</a>.</p>'
        result = email_sender.send_email(sub["email"], subject, html)
        if result["sent"]:
            sent_count += 1
        else:
            error_count += 1
        if composite_relevant and any(k.startswith("composite:") for k in novel_keys):
            composite_sent += 1

    return {
        "changes_detected": len(changes),
        "composite_extreme": extreme,
        "subscribers_checked": len(subs),
        "emails_sent": sent_count,
        "emails_errored": error_count,
        "emails_skipped_dedup": skip_count,
    }
