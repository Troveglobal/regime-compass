"""Tests for the country-hub plumbing: IMF DataMapper parsing (saved
fixture, forecast flags), the parameterized news engine (fixture feed,
cross-entity dedupe), and a regression check that the existing news
section's behavior is unchanged. Run with:

    venv/bin/python -m unittest tests.test_hubs -v
"""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import imf, news
from src.config import COUNTRIES, INDICES

FIXTURES = Path(__file__).parent / "fixtures"


class TestIMFParser(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((FIXTURES / "imf_ngdp_rpch.json").read_text())
        self.now = datetime(2026, 7, 8, tzinfo=timezone.utc)

    def test_series_for(self):
        s = imf.series_for(self.payload, "NGDP_RPCH", "IND")
        self.assertGreater(len(s), 40)
        self.assertIn(2024, s)
        self.assertIsInstance(s[2024], float)
        self.assertEqual(imf.series_for(self.payload, "NGDP_RPCH", "XXX"), {})

    def test_forecast_flags(self):
        v = imf.vitals({"NGDP_RPCH": self.payload}, "IND", now=self.now)
        gdp = v["indicators"]["NGDP_RPCH"]
        self.assertEqual(v["forecast_from"], 2026)
        self.assertEqual(gdp["latest_actual"]["year"], 2025)   # latest completed year
        self.assertEqual(gdp["current_year"]["year"], 2026)
        self.assertEqual(gdp["next_year"]["year"], 2027)
        flags = {p["year"]: p["forecast"] for p in gdp["series"]}
        self.assertFalse(flags[2025])
        self.assertTrue(flags[2026])
        self.assertTrue(flags[2031])

    def test_group_code(self):
        v = imf.vitals({"NGDP_RPCH": self.payload}, "EURO", now=self.now)
        self.assertIn("NGDP_RPCH", v["indicators"])  # Euro-area aggregate resolves

    def test_missing_indicator_survives(self):
        v = imf.vitals({}, "IND", now=self.now)
        self.assertEqual(v["indicators"], {})


RSS_FIXTURE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Fed cuts rates as growth slows - Reuters</title>
<link>https://example.com/fed-cuts</link>
<pubDate>Tue, 07 Jul 2026 10:00:00 GMT</pubDate></item>
<item><title>Nifty hits record high - Economic Times</title>
<link>https://example.com/nifty-high</link>
<pubDate>Tue, 07 Jul 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


class NewsDBTestCase(unittest.TestCase):
    """Route the news module at a throwaway sqlite file."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._orig = news.DB_PATH
        news.DB_PATH = Path(self._tmp.name)

    def tearDown(self):
        news.DB_PATH = self._orig
        Path(self._tmp.name).unlink(missing_ok=True)


class TestNewsEngine(NewsDBTestCase):
    def test_parse_feed_schema(self):
        items = news._parse_feed(RSS_FIXTURE)
        self.assertEqual(len(items), 2)
        self.assertEqual(set(items[0]), {"title", "link", "source", "published_at"})
        self.assertTrue(items[0]["published_at"].startswith("2026-07-07T10:00"))

    def test_gnews_locale_parameterized(self):
        default = news._gnews("India economy")
        self.assertIn("hl=en-IN&gl=IN&ceid=IN%3Aen", default.replace(":", "%3A"))
        us = news._gnews("US economy", ("en-US", "US", "US:en"))
        self.assertIn("gl=US", us)
        self.assertIn("when%3A2d", default.replace(":", "%3A"))  # recency window preserved

    def test_entity_feeds_cover_all_countries(self):
        feeds = news.entity_feeds()
        self.assertEqual(len(feeds), len(COUNTRIES))
        keys = {keys[0] for _, _, keys in feeds}
        self.assertEqual(keys, set(COUNTRIES))

    def test_cross_entity_dedupe(self):
        conn = news._conn()
        now = datetime.now(timezone.utc).isoformat()
        items = news._parse_feed(RSS_FIXTURE)
        # Same global story arrives under two different country tags:
        added_us = news._upsert(conn, items[:1], None, ["usa"], now)
        added_jp = news._upsert(conn, items[:1], None, ["japan"], now)
        self.assertEqual(added_us, 1)
        self.assertEqual(added_jp, 0)  # deduped — story already on the US page
        # ...but market tags (existing news section) are NOT deduped:
        added_spx = news._upsert(conn, items[:1], None, ["spx"], now)
        self.assertEqual(added_spx, 1)
        conn.commit(); conn.close()

    def test_entity_refresh_guard(self):
        conn = news._conn()
        due_before = {k[2][0] for k in news._entities_due(conn)}
        self.assertEqual(due_before, set(COUNTRIES))  # nothing fetched yet
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR REPLACE INTO news_meta (feed_key, last_fetch) VALUES (?,?)", ("india", now))
        conn.commit()
        due_after = {k[2][0] for k in news._entities_due(conn)}
        self.assertNotIn("india", due_after)  # freshly fetched → not due
        conn.close()


class TestNewsRegression(NewsDBTestCase):
    """The existing news section must be unchanged by the refactor."""

    def test_base_feeds_unchanged(self):
        # Exact pre-refactor registry: 4 publisher feeds + 9 Google News
        # queries with the historical en-IN locale and 2-day window.
        self.assertEqual(len(news.FEEDS), 13)
        urls = [u for u, _, _ in news.FEEDS]
        self.assertIn("https://www.marketwatch.com/rss/topstories", urls)
        self.assertIn("https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", urls)
        gnews = [u for u in urls if u.startswith("https://news.google.com/")]
        self.assertEqual(len(gnews), 9)
        for u in gnews:
            self.assertIn("&hl=en-IN&gl=IN&ceid=IN:en", u)  # byte-identical to pre-refactor URLs
        keys = [tuple(k) for _, _, k in news.FEEDS]
        self.assertIn(("btc", "eth"), keys)
        self.assertEqual(keys.count(("global",)), 2)

    def test_latest_output_schema_and_dedupe(self):
        conn = news._conn()
        now = datetime.now(timezone.utc).isoformat()
        items = news._parse_feed(RSS_FIXTURE)
        news._upsert(conn, items, None, ["spx"], now)
        news._upsert(conn, items, None, ["global"], now)  # same links, second tag
        conn.commit(); conn.close()
        combined = news.latest(None, 50)
        self.assertEqual(len(combined), 2)  # GROUP BY link dedupe preserved
        per_key = news.latest("spx", 50)
        self.assertEqual(len(per_key), 2)
        self.assertEqual(set(per_key[0]), {"index_key", "title", "link", "source", "published_at"})
        # ordering: newest published first
        self.assertGreater(per_key[0]["published_at"], per_key[1]["published_at"])

    def test_gnews_source_split(self):
        title, src = news._clean_gnews_title("Fed cuts rates as growth slows - Reuters")
        self.assertEqual(src, "Reuters")
        self.assertEqual(title, "Fed cuts rates as growth slows")


class TestCountryRegistry(unittest.TestCase):
    def test_slugs_and_indices_valid(self):
        for slug, cfg in COUNTRIES.items():
            self.assertNotIn(slug, INDICES)  # news keys must not collide
            self.assertIn(cfg["primary_index"], cfg["indices"])
            for k in cfg["indices"]:
                self.assertIn(k, INDICES)

    def test_smartmoney_only_where_it_exists(self):
        with_sm = {s for s, c in COUNTRIES.items() if c.get("smartmoney")}
        self.assertEqual(with_sm, {"usa", "india"})


if __name__ == "__main__":
    unittest.main()
