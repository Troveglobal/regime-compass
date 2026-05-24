"""Weekly digest email — summarizes current regime state across all markets."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import composite as composite_mod
from . import email_sender
from . import ma_regime
from . import subscriptions
from .config import DB_PATH, INDICES

BASE_URL = "https://regimecompass.com"


def _regime_color(state: str) -> str:
    return {"bear": "#e35454", "neutral": "#d4a017", "bull": "#34c673"}.get(state, "#8b95a3")


def _regime_row(name: str, state: str, detail: str) -> str:
    c = _regime_color(state)
    return (
        f'<tr>'
        f'<td style="padding:10px 12px;border-bottom:1px solid #232830;color:#e6edf3;font-weight:600;">{name}</td>'
        f'<td style="padding:10px 12px;border-bottom:1px solid #232830;text-align:center;">'
        f'<span style="display:inline-block;padding:3px 10px;border-radius:12px;background:{c};color:white;'
        f'font-weight:700;text-transform:uppercase;letter-spacing:1px;font-size:11px;">{state}</span></td>'
        f'<td style="padding:10px 12px;border-bottom:1px solid #232830;color:#8b95a3;font-size:13px;">{detail}</td>'
        f'</tr>'
    )


def build_digest_html() -> str:
    conn = sqlite3.connect(DB_PATH)
    rows_html = []

    for key, cfg in INDICES.items():
        cur = conn.execute(
            "SELECT date, bear, neutral, bull, hard_state, price_close "
            "FROM probabilities WHERE index_key = ? ORDER BY date DESC LIMIT 1",
            (key,),
        )
        row = cur.fetchone()
        if not row:
            continue
        date, bear, neutral, bull, hard_state, price = row

        streak = conn.execute(
            "SELECT hard_state FROM probabilities WHERE index_key = ? AND date <= ? ORDER BY date DESC LIMIT 365",
            (key, date),
        ).fetchall()
        days = 0
        for s in streak:
            if s[0] == hard_state:
                days += 1
            else:
                break

        try:
            sma = ma_regime.today(key, 200, kind="sma")
            sma_state = sma.get("regime", "?")
        except Exception:
            sma_state = "?"

        detail = f"{days}d in regime · SMA: {sma_state} · HMM: {(max(bear, neutral, bull) * 100):.0f}% confidence"
        rows_html.append(_regime_row(cfg["name"], hard_state, detail))

    conn.close()

    try:
        comp = composite_mod.composite_today()
        gauge = comp.get("gauge", 50)
        gauge_label = "Risk-Off" if gauge < 35 else "Risk-On" if gauge > 65 else "Neutral"
    except Exception:
        gauge = 50
        gauge_label = "Neutral"

    gauge_color = _regime_color("bear" if gauge < 35 else "bull" if gauge > 65 else "neutral")
    now = datetime.now(timezone.utc).strftime("%d %b %Y")

    return f"""
<h2 style="font-size:22px;font-weight:600;color:#fff;margin:0 0 6px;">Weekly Regime Digest</h2>
<p style="color:#8b95a3;font-size:13px;margin:0 0 20px;">{now}</p>

<div style="margin:0 0 24px;padding:16px;background:#161b22;border:1px solid #232830;border-radius:10px;text-align:center;">
  <div style="font-size:12px;color:#8b95a3;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px;">Composite Gauge</div>
  <div style="font-size:36px;font-weight:700;color:{gauge_color};">{gauge:.0f}</div>
  <div style="font-size:13px;color:#c9d1d9;">{gauge_label}</div>
</div>

<table style="width:100%;border-collapse:collapse;font-size:14px;">
<tr>
  <th style="padding:8px 12px;text-align:left;color:#8b95a3;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #2d3540;">Market</th>
  <th style="padding:8px 12px;text-align:center;color:#8b95a3;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #2d3540;">Regime</th>
  <th style="padding:8px 12px;text-align:left;color:#8b95a3;font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid #2d3540;">Detail</th>
</tr>
{''.join(rows_html)}
</table>

<p style="margin:24px 0 0;text-align:center;">
  <a href="{BASE_URL}" style="display:inline-block;background:#a78bfa;color:#0a0d12;padding:12px 28px;border-radius:8px;font-weight:600;text-decoration:none;">
    View full dashboard
  </a>
</p>
"""


def send_weekly_digest() -> dict:
    subs = subscriptions.list_subscribers(only_verified=True)
    if not subs:
        return {"sent": 0, "skipped": "no verified subscribers"}

    digest_html = build_digest_html()
    sent = 0
    for sub in subs:
        unsub_link = f"{BASE_URL}/unsubscribe?token={sub['unsubscribe_token']}"
        body = digest_html + f"""
<p style="margin-top:24px;color:#8b95a3;font-size:12px;">
  You're receiving this because you subscribed to Regime Compass alerts.
  <a href="{unsub_link}" style="color:#6ec1ff;">Unsubscribe</a>.
</p>
"""
        subject = "Regime Compass — Weekly Digest"
        html = email_sender._wrap(body)
        result = email_sender.send_email(sub["email"], subject, html)
        if result["sent"]:
            sent += 1

    return {"sent": sent, "total_subscribers": len(subs)}
