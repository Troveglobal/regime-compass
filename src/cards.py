"""Shareable regime card — generates a branded PNG for a given index."""
from __future__ import annotations

import io
import os
import sqlite3
import urllib.request
from pathlib import Path

from .config import DB_PATH, INDICES, ROOT

COLORS = {"bear": (227, 84, 84), "neutral": (212, 160, 23), "bull": (52, 198, 115)}
BG = (10, 13, 18)
PANEL = (22, 27, 34)
BORDER = (35, 40, 48)
WHITE = (255, 255, 255)
GRAY = (139, 149, 163)
VIOLET = (167, 139, 250)
W, H = 1200, 630

_FONT_DIR = ROOT / "data" / "fonts"
_FONT_PATH = _FONT_DIR / "Inter.ttf"
_FONT_URL = "https://cdn.jsdelivr.net/gh/rsms/inter@v4.1/docs/font-files/InterVariable.ttf"


def _ensure_font() -> Path:
    if _FONT_PATH.exists():
        return _FONT_PATH
    _FONT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(_FONT_URL, _FONT_PATH)
    except Exception:
        pass
    return _FONT_PATH


def _font(size: int):
    from PIL import ImageFont
    path = _ensure_font()
    if path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
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

    f14 = _font(14)
    f18 = _font(18)
    f22 = _font(22)
    f28 = _font(28)
    f52 = _font(52)
    f72 = _font(72)

    pad = 60

    # Top bar line
    draw.rectangle([0, 0, W, 4], fill=regime_color)

    # Brand
    draw.text((pad, 36), "REGIME COMPASS", fill=VIOLET, font=f18)

    # Index name
    draw.text((pad, 90), cfg["name"], fill=WHITE, font=f72)

    # Country + currency tag
    tag_y = 170
    draw.text((pad, tag_y), f'{cfg["country"]}  ·  {cfg["currency"]}', fill=GRAY, font=f22)

    # Regime state - large
    state_y = 230
    draw.text((pad, state_y), hard_state.upper(), fill=regime_color, font=f52)

    # Confidence pill
    pill_y = 300
    pill_text = f"{confidence:.0f}% confidence  ·  {days}d in regime"
    bbox = draw.textbbox((0, 0), pill_text, font=f22)
    pw = bbox[2] - bbox[0] + 40
    ph = bbox[3] - bbox[1] + 20
    draw.rounded_rectangle([pad, pill_y, pad + pw, pill_y + ph], radius=ph // 2, fill=PANEL, outline=BORDER)
    draw.text((pad + 20, pill_y + 10), pill_text, fill=WHITE, font=f22)

    # Price
    price_y = 370
    draw.text((pad, price_y), f"Price: {price:,.2f} {cfg['currency']}", fill=GRAY, font=f28)

    # Date
    draw.text((pad, price_y + 44), f"As of {date}", fill=GRAY, font=f22)

    # Compass motif (right side)
    cx, cy = W - 140, 200
    r = 80
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BORDER, width=2)
    tri = 55
    draw.polygon([(cx, cy - tri), (cx - 22, cy - 6), (cx + 22, cy - 6)], fill=(52, 198, 115))
    draw.polygon([(cx, cy + tri), (cx - 22, cy + 6), (cx + 22, cy + 6)], fill=(227, 84, 84))
    draw.rectangle([cx + 30, cy - 4, cx + 50, cy + 4], fill=(212, 160, 23))
    draw.rectangle([cx - 50, cy - 4, cx - 30, cy + 4], fill=(212, 160, 23))
    draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=WHITE)
    draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=BG)

    # Bottom bar
    draw.rectangle([0, H - 56, W, H], fill=PANEL)
    draw.text((pad, H - 42), "regimecompass.com", fill=VIOLET, font=f18)
    draw.text((W - pad - 200, H - 42), "by iQuant Labs", fill=GRAY, font=f18)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
