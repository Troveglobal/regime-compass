"""Shareable regime card — generates a branded PNG for a given index."""
from __future__ import annotations

import io
import os
import sqlite3
from datetime import datetime, timezone

from .config import DB_PATH, INDICES

COLORS = {"bear": (227, 84, 84), "neutral": (212, 160, 23), "bull": (52, 198, 115)}
BG = (10, 13, 18)
WHITE = (255, 255, 255)
GRAY = (139, 149, 163)
VIOLET = (167, 139, 250)
W, H = 800, 418


def _font(size: int):
    from PIL import ImageFont
    for path in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate_card(index_key: str) -> bytes:
    from PIL import Image, ImageDraw

    cfg = INDICES[index_key]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT date, bear, neutral, bull, hard_state, price_close "
        "FROM probabilities WHERE index_key = ? ORDER BY date DESC LIMIT 1",
        (index_key,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No data for {index_key}")

    date, bear, neutral, bull, hard_state, price = row

    streak = conn.execute(
        "SELECT hard_state FROM probabilities WHERE index_key = ? AND date <= ? ORDER BY date DESC LIMIT 365",
        (index_key, date),
    ).fetchall()
    conn.close()

    days = 0
    for s in streak:
        if s[0] == hard_state:
            days += 1
        else:
            break

    confidence = max(bear, neutral, bull) * 100
    regime_color = COLORS.get(hard_state, GRAY)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_brand = _font(14)
    f_name = _font(32)
    f_regime = _font(48)
    f_detail = _font(18)
    f_url = _font(13)

    # Brand
    draw.text((40, 30), "REGIME COMPASS", fill=VIOLET, font=f_brand)

    # Index name + country
    draw.text((40, 70), cfg["name"], fill=WHITE, font=f_name)
    bbox = draw.textbbox((40, 70), cfg["name"], font=f_name)
    draw.text((bbox[2] + 16, 82), cfg["country"], fill=GRAY, font=f_detail)

    # Regime state
    draw.text((40, 140), hard_state.upper(), fill=regime_color, font=f_regime)

    # Regime pill background
    pill_y = 210
    draw.rounded_rectangle([40, pill_y, 200, pill_y + 36], radius=18, fill=regime_color)
    draw.text((58, pill_y + 8), f"{confidence:.0f}% confidence", fill=WHITE if hard_state != "neutral" else (26, 26, 26), font=f_brand)

    # Details
    draw.text((40, 270), f"{days} days in current regime", fill=GRAY, font=f_detail)
    draw.text((40, 300), f"Price: {price:,.2f} {cfg['currency']}", fill=GRAY, font=f_detail)
    draw.text((40, 330), f"As of {date}", fill=GRAY, font=f_detail)

    # URL watermark
    draw.text((40, H - 40), "regimecompass.com", fill=(*VIOLET, 180), font=f_url)

    # Compass motif (top right)
    cx, cy = W - 80, 80
    tri = 30
    draw.polygon([(cx, cy - tri), (cx - 15, cy - 4), (cx + 15, cy - 4)], fill=(52, 198, 115))
    draw.polygon([(cx, cy + tri), (cx - 15, cy + 4), (cx + 15, cy + 4)], fill=(227, 84, 84))
    draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=WHITE)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
