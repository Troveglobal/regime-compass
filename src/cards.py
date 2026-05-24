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
NEUTRAL_TEXT = (26, 26, 26)
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


def _text_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _text_height(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def _draw_centered(draw, y: int, text: str, font, fill):
    tw = _text_width(draw, text, font)
    draw.text(((W - tw) // 2, y), text, fill=fill, font=font)


def _draw_prob_bar(draw, cx, y, w, h, bear, neutral, bull, f_label):
    """Draw a centered horizontal stacked probability bar with labels below."""
    x = cx - w // 2
    # Background track
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=(30, 34, 42))

    bear_w = max(int(w * bear), 1) if bear > 0.005 else 0
    neutral_w = max(int(w * neutral), 1) if neutral > 0.005 else 0
    bull_w = w - bear_w - neutral_w if (bear_w + neutral_w) < w else 0

    # Clip segments to rounded shape via mask
    from PIL import Image, ImageDraw as ID
    mask = Image.new("L", (w, h), 0)
    md = ID.Draw(mask)
    md.rounded_rectangle([0, 0, w, h], radius=h // 2, fill=255)

    bar = Image.new("RGB", (w, h), (30, 34, 42))
    bd = ID.Draw(bar)
    px = 0
    if bear_w > 0:
        bd.rectangle([px, 0, px + bear_w, h], fill=COLORS["bear"])
        px += bear_w
    if neutral_w > 0:
        bd.rectangle([px, 0, px + neutral_w, h], fill=COLORS["neutral"])
        px += neutral_w
    if bull_w > 0:
        bd.rectangle([px, 0, px + bull_w, h], fill=COLORS["bull"])

    # Paste bar onto main image using mask
    draw._image.paste(bar, (x, y), mask)

    # Labels below the bar — spread across bar width
    label_y = y + h + 6
    bear_label = f"Bear {bear*100:.0f}%"
    neut_label = f"Neutral {neutral*100:.0f}%"
    bull_label = f"Bull {bull*100:.0f}%"

    # Bear label left-aligned to bar start
    draw.text((x, label_y), bear_label, fill=COLORS["bear"], font=f_label)
    # Neutral label centered
    nw = _text_width(draw, neut_label, f_label)
    draw.text((cx - nw // 2, label_y), neut_label, fill=COLORS["neutral"], font=f_label)
    # Bull label right-aligned to bar end
    bw = _text_width(draw, bull_label, f_label)
    draw.text((x + w - bw, label_y), bull_label, fill=COLORS["bull"], font=f_label)


def _draw_pill(draw, cx, y, label: str, regime: str, f_label, f_regime):
    """Draw a model pill: 'HMM: BULL' with colored rounded-rect background.
    cx is the center x of the pill. Returns pill width."""
    color = COLORS.get(regime, GRAY)
    text_color = NEUTRAL_TEXT if regime == "neutral" else WHITE
    regime_text = regime.upper() if regime in COLORS else "?"

    full_text = f"{label}: {regime_text}"
    tw = _text_width(draw, full_text, f_regime)
    pad_x, pad_y = 16, 8
    pw = tw + pad_x * 2
    ph = _text_height(draw, full_text, f_regime) + pad_y * 2

    rx = cx - pw // 2
    ry = y

    draw.rounded_rectangle([rx, ry, rx + pw, ry + ph], radius=ph // 2, fill=color)
    draw.text((rx + pad_x, ry + pad_y), full_text, fill=text_color, font=f_regime)
    return pw, ph


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

    # ── Build canvas ──
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f12 = _font(12)
    f13 = _font(13)
    f14 = _font(14)
    f15 = _font(15)
    f16 = _font(16)
    f18 = _font(18)
    f20 = _font(20)
    f44 = _font(44)
    f56 = _font(56)

    cx = W // 2  # center x

    # ── 1. Top color bar (4px) ──
    draw.rectangle([0, 0, W, 4], fill=regime_color)

    # ── 2. Brand line ──
    # Compass logo + brand name in white
    brand_text = "Regime Compass"
    brand_bbox = draw.textbbox((0, 0), brand_text, font=f16)
    brand_w = brand_bbox[2] - brand_bbox[0]
    brand_x = (W - brand_w - 24) // 2
    brand_y = 46
    # Draw compass motif (small green/red triangles)
    logo_cx = brand_x
    logo_cy = brand_y + 9
    draw.polygon([(logo_cx, logo_cy - 8), (logo_cx - 5, logo_cy - 1), (logo_cx + 5, logo_cy - 1)], fill=(52, 198, 115))
    draw.polygon([(logo_cx, logo_cy + 8), (logo_cx - 5, logo_cy + 1), (logo_cx + 5, logo_cy + 1)], fill=(227, 84, 84))
    draw.text((logo_cx + 12, brand_y), brand_text, fill=WHITE, font=f16)

    # ── 3. Index name — large, white, centered ──
    _draw_centered(draw, 86, cfg["name"], f56, WHITE)

    # ── 4. Subtitle: country · currency · price ──
    subtitle = f'{cfg["country"]}  ·  {cfg["currency"]}  ·  {price:,.2f}'
    _draw_centered(draw, 164, subtitle, f18, GRAY)

    # ── 5. Regime state — large, colored ──
    _draw_centered(draw, 224, hard_state.upper(), f44, regime_color)

    # ── 6. Confidence + duration ──
    conf_line = f"{confidence:.0f}% confidence  ·  {days}d in regime"
    _draw_centered(draw, 284, conf_line, f16, GRAY)

    # ── 7. Three-model pill row ──
    pill_y = 344
    models = [
        ("HMM", hard_state),
        ("SMA", sma_regime),
        ("EMA", ema_regime),
    ]
    # Measure total width first to center the row
    pill_gap = 16
    pill_widths = []
    for label, regime in models:
        regime_text = regime.upper() if regime in COLORS else "?"
        full = f"{label}: {regime_text}"
        tw = _text_width(draw, full, f15)
        pw = tw + 32  # pad_x * 2
        pill_widths.append(pw)
    total_pills_w = sum(pill_widths) + pill_gap * (len(models) - 1)
    pill_x = cx - total_pills_w // 2

    pill_h = 0
    for i, (label, regime) in enumerate(models):
        pcx = pill_x + pill_widths[i] // 2
        pw, ph = _draw_pill(draw, pcx, pill_y, label, regime, f15, f15)
        pill_h = max(pill_h, ph)
        pill_x += pill_widths[i] + pill_gap

    # ── 8. Probability bar ──
    bar_y = pill_y + pill_h + 40
    bar_w = 500
    bar_h = 18
    _draw_prob_bar(draw, cx, bar_y, bar_w, bar_h, bear, neutral, bull, f14)

    # ── 9. Explainer line ──
    explainer_y = bar_y + bar_h + 40
    explainer = "Three regime models classify this market as risk-on, risk-off, or neutral."
    _draw_centered(draw, explainer_y, explainer, f14, DARK_GRAY)

    # ── 10. Bottom bar ──
    bottom_h = 44
    bottom_y = H - bottom_h
    draw.rectangle([0, bottom_y, W, H], fill=PANEL)
    draw.line([(0, bottom_y), (W, bottom_y)], fill=BORDER, width=1)

    pad_bottom = 32
    draw.text((pad_bottom, bottom_y + 14), "regimecompass.com", fill=GRAY, font=f14)

    # Date centered
    _draw_centered(draw, bottom_y + 14, date, f14, DARK_GRAY)

    # "by iQuant Labs" right-aligned
    iq_text = "by iQuant Labs"
    iq_w = _text_width(draw, iq_text, f14)
    draw.text((W - pad_bottom - iq_w, bottom_y + 14), iq_text, fill=VIOLET, font=f14)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
