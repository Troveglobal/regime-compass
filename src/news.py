"""News aggregation — headlines only, always linking out to the publisher.

Strictly aggregation: we store title / link / source / timestamp from public
RSS feeds and never fetch or reproduce article bodies. Each headline is tagged
with the market(s) it concerns so the frontend can show it next to that
market's current regime.

Feeds are a mix of direct publisher RSS and Google News RSS queries for
markets without a dedicated feed. Fetches are best-effort: a dead feed is
logged and skipped, never fatal.
"""
from __future__ import annotations

import email.utils
import hashlib
import html
import logging
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .config import DB_PATH

log = logging.getLogger("regime_compass.news")

FETCH_TIMEOUT_SEC = 12
MAX_ITEMS_PER_FEED = 25
RETENTION_DAYS = 10
_UA = "Mozilla/5.0 (compatible; RegimeCompass/1.0; +https://www.regimecompass.com)"


def _gnews(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q=" + quote(query + " when:2d")
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )


# feed url -> (source label or None to use per-item source, [index_keys])
# index_keys tag which market a headline belongs to; "global" is the macro tab.
FEEDS: list[tuple[str, str | None, list[str]]] = [
    # Direct publisher feeds
    ("https://www.marketwatch.com/rss/topstories", "MarketWatch", ["global"]),
    ("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "Economic Times", ["nifty"]),
    ("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", ["btc", "eth"]),
    ("https://cointelegraph.com/rss", "Cointelegraph", ["btc", "eth"]),
    # Google News queries (per-item publisher becomes the source)
    (_gnews('"S&P 500" OR "Wall Street" stocks'), None, ["spx"]),
    (_gnews('Nasdaq tech stocks'), None, ["nasdaq"]),
    (_gnews('"Euro Stoxx" OR DAX OR "European stocks"'), None, ["stoxx50"]),
    (_gnews('"Nikkei 225" OR "Japan stocks"'), None, ["nikkei"]),
    (_gnews('KOSPI OR "Korean stocks"'), None, ["kospi"]),
    (_gnews('"Shanghai Composite" OR "China stocks"'), None, ["shcomp"]),
    (_gnews('gold price bullion'), None, ["gold"]),
    (_gnews('silver price'), None, ["silver"]),
    (_gnews('"Federal Reserve" OR inflation OR "central bank" markets'), None, ["global"]),
]


# ---------------- storage ----------------

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS news_items (
            id TEXT PRIMARY KEY,
            index_key TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            source TEXT,
            published_at TEXT,
            fetched_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_news_key_pub ON news_items (index_key, published_at DESC)")
    return conn


# ---------------- parsing ----------------

def _text(el) -> str:
    return html.unescape("".join(el.itertext()).strip()) if el is not None else ""


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    """Parse RSS 2.0 / Atom into [{title, link, source, published_at}]."""
    root = ET.fromstring(xml_bytes)
    items = []
    ns_atom = "{http://www.w3.org/2005/Atom}"

    for item in root.iter("item"):  # RSS 2.0
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        pub = _text(item.find("pubDate"))
        src = _text(item.find("source"))
        if title and link:
            items.append({"title": title, "link": link, "source": src or None, "published_at": _to_iso(pub)})

    if not items:  # Atom fallback
        for entry in root.iter(ns_atom + "entry"):
            title = _text(entry.find(ns_atom + "title"))
            link_el = entry.find(ns_atom + "link")
            link = link_el.get("href") if link_el is not None else ""
            pub = _text(entry.find(ns_atom + "updated"))
            if title and link:
                items.append({"title": title, "link": link, "source": None, "published_at": pub or None})
    return items[:MAX_ITEMS_PER_FEED]


def _to_iso(rfc822: str) -> str | None:
    if not rfc822:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(rfc822)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _clean_gnews_title(title: str) -> tuple[str, str | None]:
    """Google News titles end in ' - Publisher'; split it out as the source."""
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        if head and 0 < len(tail) <= 40:
            return head, tail
    return title, None


# ---------------- refresh ----------------

def refresh() -> dict:
    """Fetch all feeds and upsert new headlines. Returns per-feed counts."""
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _conn()
    added, failed = 0, 0
    for url, source_label, index_keys in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SEC) as resp:
                raw = resp.read()
            parsed = _parse_feed(raw)
        except Exception as e:
            failed += 1
            log.warning("[news] feed failed %s: %r", url.split("?")[0], e)
            continue

        for it in parsed:
            title, src = it["title"], it["source"] or source_label
            if source_label is None:
                title, gsrc = _clean_gnews_title(title)
                src = it["source"] or gsrc or "Google News"
            for key in index_keys:
                row_id = hashlib.sha256((key + "|" + it["link"]).encode()).hexdigest()[:24]
                try:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO news_items (id, index_key, title, link, source, published_at, fetched_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (row_id, key, title[:300], it["link"], src, it["published_at"], now_iso),
                    )
                    added += cur.rowcount
                except sqlite3.Error as e:
                    log.warning("[news] insert failed: %r", e)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    conn.execute(
        "DELETE FROM news_items WHERE COALESCE(published_at, fetched_at) < ?", (cutoff,)
    )
    conn.commit()
    n_total = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    conn.close()
    log.info("[news] refresh: +%d new, %d feeds failed, %d stored", added, failed, n_total)
    return {"added": added, "feeds_failed": failed, "stored": n_total}


def has_items() -> bool:
    try:
        conn = _conn()
        n = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        conn.close()
        return n > 0
    except sqlite3.Error:
        return False


# ---------------- read API ----------------

def latest(index_key: str | None = None, limit: int = 30) -> list[dict]:
    conn = _conn()
    if index_key:
        rows = conn.execute(
            "SELECT index_key, title, link, source, published_at FROM news_items "
            "WHERE index_key = ? ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
            (index_key, limit),
        ).fetchall()
    else:
        # De-duplicate across market tags when showing the combined view
        rows = conn.execute(
            "SELECT index_key, title, link, source, published_at FROM news_items "
            "GROUP BY link ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [
        {"index_key": r[0], "title": r[1], "link": r[2], "source": r[3], "published_at": r[4]}
        for r in rows
    ]
