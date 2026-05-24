"""Recent regime changes across all markets."""
from __future__ import annotations

import sqlite3
from .config import DB_PATH, INDICES


def recent_changes(days: int = 30) -> list[dict]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    changes = []

    for key, cfg in INDICES.items():
        rows = conn.execute(
            "SELECT date, hard_state FROM probabilities WHERE index_key = ? "
            "ORDER BY date DESC LIMIT ?",
            (key, days + 1),
        ).fetchall()

        if len(rows) < 2:
            continue

        for i in range(len(rows) - 1):
            date, state = rows[i]
            _, prev_state = rows[i + 1]
            if state != prev_state:
                changes.append({
                    "date": date,
                    "index_key": key,
                    "index_name": cfg["name"],
                    "country": cfg["country"],
                    "from": prev_state,
                    "to": state,
                })

    conn.close()
    changes.sort(key=lambda c: c["date"], reverse=True)
    return changes


def calendar_data(index_key: str | None = None) -> dict:
    """Monthly regime summary: for each month, the dominant regime per index."""
    if not DB_PATH.exists():
        return {"months": [], "indices": []}

    conn = sqlite3.connect(DB_PATH)
    keys = [index_key] if index_key else list(INDICES.keys())
    all_months = set()
    index_data = {}

    for key in keys:
        rows = conn.execute(
            "SELECT date, hard_state FROM probabilities WHERE index_key = ? ORDER BY date",
            (key,),
        ).fetchall()

        monthly: dict[str, dict[str, int]] = {}
        for date, state in rows:
            month = date[:7]
            all_months.add(month)
            if month not in monthly:
                monthly[month] = {"bear": 0, "neutral": 0, "bull": 0}
            monthly[month][state] += 1

        month_regimes = {}
        for month, counts in monthly.items():
            dominant = max(counts, key=counts.get)
            total = sum(counts.values())
            month_regimes[month] = {
                "regime": dominant,
                "pct": round(counts[dominant] / total * 100, 1),
                "bear_days": counts["bear"],
                "neutral_days": counts["neutral"],
                "bull_days": counts["bull"],
            }

        index_data[key] = {
            "index_key": key,
            "index_name": INDICES[key]["name"],
            "months": month_regimes,
        }

    conn.close()
    months_sorted = sorted(all_months)
    return {"months": months_sorted, "indices": list(index_data.values())}
