"""
SmartFlow (Smart Money India) daily refresh, run from the Regime Compass scheduler.

Pulls the latest NSE bulk/block deals, rebuilds the feed (which also pulls stock &
index closes for the returns layer), and writes out/feed.json — served at
/api/smartmoney. Pure standard library; safe to call repeatedly (idempotent).
"""

import logging

try:
    from . import fetch as _fetch
    from . import pipeline as _pipeline
except ImportError:  # standalone
    import fetch as _fetch
    import pipeline as _pipeline

log = logging.getLogger("regime_compass")


def refresh() -> bool:
    """Fetch latest NSE deals + rebuild the feed. Returns True on success."""
    try:
        _fetch.fetch()          # append today's bulk/block into data/raw/daily
    except Exception as e:      # noqa: BLE001 — never let a fetch hiccup kill the job
        log.warning("[smartmoney] NSE fetch failed (%s) — rebuilding from existing data", e)
    _pipeline.main()            # rebuild out/feed.json from whatever data is present
    return True


if __name__ == "__main__":
    refresh()
