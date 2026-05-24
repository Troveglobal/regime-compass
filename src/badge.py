"""Embeddable SVG badge showing current regime for an index."""
from __future__ import annotations

import sqlite3

from .config import DB_PATH, INDICES

COLORS = {"bear": "#e35454", "neutral": "#d4a017", "bull": "#34c673"}
TEXT_COLORS = {"bear": "#fff", "neutral": "#1a1a1a", "bull": "#fff"}


def generate_badge(index_key: str, style: str = "flat") -> str:
    cfg = INDICES[index_key]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT hard_state FROM probabilities WHERE index_key = ? ORDER BY date DESC LIMIT 1",
        (index_key,),
    )
    row = cur.fetchone()
    conn.close()

    state = row[0] if row else "unknown"
    color = COLORS.get(state, "#8b95a3")
    text_color = TEXT_COLORS.get(state, "#fff")
    label = cfg["name"]
    value = state.upper()

    label_w = len(label) * 7 + 16
    value_w = len(value) * 7.5 + 20
    total_w = label_w + value_w

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="22" role="img" aria-label="{label}: {value}">
  <title>{label}: {value}</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{total_w}" height="22" rx="4" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_w}" height="22" fill="#1c2330"/>
    <rect x="{label_w}" width="{value_w}" height="22" fill="{color}"/>
    <rect width="{total_w}" height="22" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" text-rendering="geometricPrecision" font-size="11">
    <text x="{label_w / 2}" y="15" fill="#e6edf3">{label}</text>
    <text x="{label_w + value_w / 2}" y="15" fill="{text_color}" font-weight="bold">{value}</text>
  </g>
</svg>"""
