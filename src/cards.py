"""Shareable regime card — generates a data-rich PNG showing regime snapshot."""
from __future__ import annotations

import io
import os
import sqlite3
import urllib.request
from pathlib import Path

from . import ma_regime
from .config import DB_PATH, INDICES, ROOT

COLORS = {"bear": (227, 84, 84), "neutral": (212, 160, 23), "bull": (52, 198, 115)}
BG = (10, 13, 18)
PANEL = (22, 27, 34)
BORDER = (45, 50, 58)
WHITE = (255, 255, 255)
GRAY = (139, 149, 163)
DARK_GRAY = (100, 108, 120)
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
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _draw_prob_bar(draw, x, y, w, h, bear, neutral, bull):
    bear_w = int(w * bear)
    neutral_w = int(w * neutral)
    bull_w = w - bear_w - neutral_w
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=(30, 34, 42))
    cx = x
    if bear_w > 0:
        draw.rectangle([cx, y, cx + bear_w, y + h], fill=COLORS["bear"])
    cx += bear_w
    if neutral_w > 0:
        draw.rectangle([cx, y, cx + neutral_w, y + h], fill=COLORS["neutral"])
    cx += neutral_w
    if bull_w > 0:
        draw.rectangle([cx, y, cx + bull_w, y + h], fill=COLORS["bull"])
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, outline=(30, 34, 42), width=1)


def _draw_regime_pill(draw, x, y, state, font):
    color = COLORS.get(state, GRAY)
    text = state.upper()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    pw, ph = tw + 20, bbox[3] - bbox[1] + 12
    text_color = WHITE if state != "neutral" else (26, 26, 26)
    draw.rounded_rectangle([x, y, x + pw, y + ph], radius=ph // 2, fill=color)
    draw.text((x + 10, y + 6), text, fill=text_color, font=font)
    return pw


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

    try:
        sma200 = ma_regime.today(index_key, 200, kind="sma")
        sma_regime = sma200.get("regime", "?")
    except Exception:
        sma_regime = "?"
    try:
        ema200 = ma_regime.today(index_key, 200, kind="ema")
        ema_regime = ema200.get("regime", "?")
    except Exception:
        ema_regime = "?"

    confidence = max(bear, neutral, bull) * 100
    regime_color = COLORS.get(hard_state, GRAY)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f12 = _font(12)
    f14 = _font(14)
    f16 = _font(16)
    f20 = _font(20)
    f24 = _font(24)
    f48 = _font(48)
    f56 = _font(56)

    pad = 48
    right_col = 660

    # ── Top color bar ──
    draw.rectangle([0, 0, W, 5], fill=regime_color)

    # ── Left column: index info ──
    draw.text((pad, 28), "REGIME COMPASS", fill=VIOLET, font=f14)
    draw.text((pad, 68), cfg["name"], fill=WHITE, font=f56)
    draw.text((pad, 138), f'{cfg["country"]}  ·  {cfg["currency"]}  ·  {price:,.2f}', fill=GRAY, font=f20)

    # ── HMM regime state ──
    draw.text((pad, 190), "HMM REGIME", fill=DARK_GRAY, font=f12)
    draw.text((pad, 210), hard_state.upper(), fill=regime_color, font=f48)
    draw.text((pad, 270), f"{confidence:.0f}% confidence  ·  {days}d in regime", fill=GRAY, font=f16)

    # ── Probability bar ──
    draw.text((pad, 310), "PROBABILITY", fill=DARK_GRAY, font=f12)
    bar_w = right_col - pad - 40
    _draw_prob_bar(draw, pad, 330, bar_w, 24, bear, neutral, bull)
    draw.text((pad, 362), f"Bear {bear*100:.0f}%", fill=COLORS["bear"], font=f14)
    draw.text((pad + bar_w // 2 - 30, 362), f"Neutral {neutral*100:.0f}%", fill=COLORS["neutral"], font=f14)
    draw.text((pad + bar_w - 80, 362), f"Bull {bull*100:.0f}%", fill=COLORS["bull"], font=f14)

    # ── Right column: model agreement panel ──
    panel_x, panel_y = right_col, 28
    panel_w, panel_h = W - right_col - pad, 360
    draw.rounded_rectangle(
        [panel_x, panel_y, panel_x + panel_w, panel_y + panel_h],
        radius=16, fill=PANEL, outline=BORDER,
    )

    inner_pad = 28
    ix = panel_x + inner_pad
    iy = panel_y + inner_pad

    draw.text((ix, iy), "MODEL AGREEMENT", fill=DARK_GRAY, font=f12)
    iy += 30

    models = [
        ("Hidden Markov Model", hard_state),
        ("200-day SMA", sma_regime),
        ("200-day EMA", ema_regime),
    ]
    for label, regime in models:
        draw.text((ix, iy), label, fill=GRAY, font=f16)
        iy += 24
        if regime in COLORS:
            _draw_regime_pill(draw, ix, iy, regime, f14)
        else:
            draw.text((ix, iy), regime.upper(), fill=GRAY, font=f14)
        iy += 40

    agreement = sum(1 for _, r in models if r == hard_state)
    if agreement == 3:
        ag_text, ag_color = "All 3 models agree", COLORS.get(hard_state, GRAY)
    elif agreement == 2:
        ag_text, ag_color = "2 of 3 models agree", COLORS.get(hard_state, GRAY)
    else:
        ag_text, ag_color = "Models disagree", COLORS["neutral"]

    draw.text((ix, iy + 10), ag_text, fill=ag_color, font=f20)

    # ── Bottom bar ──
    draw.rectangle([0, H - 50, W, H], fill=PANEL)
    draw.line([(0, H - 50), (W, H - 50)], fill=BORDER, width=1)
    draw.text((pad, H - 38), f"regimecompass.com  ·  {date}", fill=GRAY, font=f14)
    draw.text((W - pad - 120, H - 38), "by iQuant Labs", fill=VIOLET, font=f14)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
