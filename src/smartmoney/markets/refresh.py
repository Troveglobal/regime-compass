"""
Global Smart Money daily refresh — one entry point per market, called from the
Regime Compass scheduler after each exchange publishes its end-of-day deals.

Each refresh catches up the last few sessions (heals gaps after downtime),
rebuilds the market's feed and writes out/feed_<mkt>.json — served at
/api/smartmoney/<mkt>. Idempotent; day caches make re-runs cheap.
"""

import logging

try:
    from . import common as X
    from . import id_mkt, tw, us
except ImportError:  # standalone
    import common as X
    import id_mkt
    import tw
    import us

log = logging.getLogger("regime_compass")

MARKETS = {"tw": tw, "id": id_mkt, "us": us}
CATCHUP_DAYS = 7


def refresh(mkt: str) -> bool:
    mod = MARKETS[mkt]
    try:
        mod.fetch(backfill_days=CATCHUP_DAYS)
    except Exception as e:  # noqa: BLE001 — stale feed beats no feed
        log.warning("[smartmoney:%s] fetch failed (%s) — rebuilding from cache", mkt, e)
    feed = mod.build()
    X.write_feed(mkt, feed)
    log.info("[smartmoney:%s] feed rebuilt (latest %s)", mkt, feed["meta"].get("latest_date"))
    return True


def refresh_all():
    for mkt in MARKETS:
        try:
            refresh(mkt)
        except Exception:
            log.exception("[smartmoney:%s] refresh failed", mkt)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        refresh(sys.argv[1])
    else:
        refresh_all()
