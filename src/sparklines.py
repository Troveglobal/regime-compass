"""Sparkline feed: last 90 trading days of {date, close, regime state} per
market, for the regime-ribbon mini charts on the home grid. Precomputed into
data/analytics/sparklines.json — tiny payload, values rounded, states packed
as an index into `state_labels`.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

from .config import DATA_DIR, DB_PATH, INDICES, STATE_LABELS

OUT_PATH = DATA_DIR / "analytics" / "sparklines.json"
N_DAYS = 90


def build() -> dict:
    conn = sqlite3.connect(DB_PATH)
    markets = []
    try:
        for key in INDICES:
            rows = conn.execute(
                "SELECT date, price_close, hard_state FROM probabilities "
                "WHERE index_key = ? ORDER BY date DESC LIMIT ?", (key, N_DAYS),
            ).fetchall()
            if not rows:
                continue
            rows.reverse()
            # [date, close (4 sig-figs is plenty for a 30px spark), state index]
            points = [[d, float(f"{p:.6g}"), STATE_LABELS.index(s)] for d, p, s in rows]
            markets.append({"index_key": key, "name": INDICES[key]["name"], "points": points})
    finally:
        conn.close()
    if not markets:
        raise RuntimeError("no sparkline data in probabilities table")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "as_of": max(m["points"][-1][0] for m in markets),
        "state_labels": STATE_LABELS,
        "n_days": N_DAYS,
        "markets": markets,
    }


def refresh() -> dict:
    out = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, separators=(",", ":")))
    print(f"[sparklines] wrote {OUT_PATH} ({len(out['markets'])} markets)", flush=True)
    return out


if __name__ == "__main__":
    try:
        refresh()
    except Exception as e:
        print(f"[sparklines] FAILED: {e}", file=sys.stderr, flush=True)
        raise
