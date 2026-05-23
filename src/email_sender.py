"""Resend integration. If RESEND_API_KEY isn't set, emails are logged instead.

For production: sign up at resend.com (3,000 emails/month free), set the env var.
Sending requires a verified sender domain — see Resend docs.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from .config import LOGS_DIR

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM_EMAIL", "Regime Compass <alerts@regimecompass.com>")
RESEND_API_URL = "https://api.resend.com/emails"

LOGS_DIR.mkdir(parents=True, exist_ok=True)
_log_path = LOGS_DIR / "emails.log"


def _log_email(to: str, subject: str, body_html: str, status: str, reason: str = "") -> None:
    """Always log every email attempt (sent or skipped)."""
    import datetime as _dt
    line = (
        f"[{_dt.datetime.now(_dt.timezone.utc).isoformat()}] "
        f"to={to} subject={subject!r} status={status} reason={reason}\n"
    )
    with open(_log_path, "a") as f:
        f.write(line)


def send_email(to: str, subject: str, html: str, text: str | None = None) -> dict:
    """Send an email. Returns {"sent": bool, "reason": str}."""
    if not RESEND_API_KEY:
        _log_email(to, subject, html, "skipped", "RESEND_API_KEY not set")
        return {"sent": False, "reason": "RESEND_API_KEY not configured; logged only"}

    payload = {"from": RESEND_FROM, "to": [to], "subject": subject, "html": html}
    if text:
        payload["text"] = text
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        RESEND_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            _log_email(to, subject, html, "sent", body[:120])
            return {"sent": True, "reason": body[:200]}
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        _log_email(to, subject, html, "error", f"HTTP {e.code}: {body}")
        return {"sent": False, "reason": f"HTTP {e.code}: {body}"}
    except Exception as e:
        _log_email(to, subject, html, "error", repr(e))
        return {"sent": False, "reason": repr(e)}


# ============================================================
# Email templates
# ============================================================

def _wrap(content_html: str) -> str:
    """Wrap content in a minimal branded HTML email."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/></head>
<body style="margin:0;padding:0;background:#0a0d12;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;color:#e6edf3;">
<div style="max-width:560px;margin:0 auto;padding:32px 24px;">
  <div style="font-size:18px;font-weight:700;letter-spacing:3px;color:#c4b5fd;margin-bottom:6px;">REGIME COMPASS</div>
  <div style="height:1px;background:#232830;margin-bottom:24px;"></div>
  {content_html}
  <div style="height:1px;background:#232830;margin-top:32px;margin-bottom:18px;"></div>
  <div style="font-size:12px;color:#8b95a3;line-height:1.55;">
    Regime Compass is a statistical research tool for educational purposes only. Not investment advice.
    Read the <a href="https://regimecompass.com/disclaimer" style="color:#6ec1ff;">full disclaimer</a>.
  </div>
</div>
</body></html>"""


def build_verification_email(base_url: str, verify_token: str) -> tuple[str, str]:
    link = f"{base_url}/verify?token={verify_token}"
    body = f"""
<h2 style="font-size:22px;font-weight:600;color:#fff;margin:0 0 16px;">Confirm your email</h2>
<p style="color:#c9d1d9;line-height:1.6;font-size:15px;">
  Thanks for signing up to Regime Compass alerts. Click the link below to confirm
  your email and start receiving regime updates:
</p>
<p style="margin:24px 0;">
  <a href="{link}" style="display:inline-block;background:#a78bfa;color:#0a0d12;padding:12px 28px;border-radius:8px;font-weight:600;text-decoration:none;">
    Confirm subscription
  </a>
</p>
<p style="color:#8b95a3;font-size:13px;line-height:1.55;">
  If you didn't sign up, you can safely ignore this email — your address won't be added.
</p>
<p style="color:#8b95a3;font-size:12px;line-height:1.55;margin-top:24px;">
  Or paste this link: <br/><span style="color:#6ec1ff;word-break:break-all;">{link}</span>
</p>
"""
    return ("Confirm your Regime Compass subscription", _wrap(body))


def build_alert_email(base_url: str, unsubscribe_token: str, summary_html: str) -> tuple[str, str]:
    unsub_link = f"{base_url}/unsubscribe?token={unsubscribe_token}"
    body = summary_html + f"""
<p style="margin-top:24px;color:#8b95a3;font-size:12px;">
  You're receiving this because you subscribed to Regime Compass alerts.
  <a href="{unsub_link}" style="color:#6ec1ff;">Unsubscribe</a>.
</p>
"""
    return ("Regime Compass alert", _wrap(body))
