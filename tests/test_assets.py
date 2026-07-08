"""Tests for the asset-hub math (src/assets.py) — pure functions on
synthetic series, no network. Includes the crypto calendar-alignment test
(7-day own-stats vs business-day cross-asset stats). Run with:

    venv/bin/python -m unittest tests.test_assets -v
"""
import unittest

import numpy as np
import pandas as pd

from src.assets import (
    _is_seven_day,
    corr_stats,
    drawdowns,
    ma200_distance,
    realized_vol,
    rolling_corr,
    weekend_vol_split,
)
from src.config import ASSETS, COUNTRIES, INDICES


def _seven_day(n=800, seed=0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n))), index=idx)


def _bdays(n=560, seed=1):
    idx = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n))), index=idx)


class TestCalendarAlignment(unittest.TestCase):
    def test_seven_day_detection(self):
        self.assertTrue(_is_seven_day(_seven_day()))
        self.assertFalse(_is_seven_day(_bdays()))

    def test_own_stats_use_all_seven_days(self):
        s = _seven_day()
        vol = realized_vol(s)
        # the vol series must include weekend observations (own calendar)
        self.assertTrue((pd.DatetimeIndex(vol.dropna().index).dayofweek >= 5).any())

    def test_cross_asset_corr_uses_business_day_intersection(self):
        crypto, equity = _seven_day(), _bdays()
        corr = rolling_corr(crypto, equity)
        idx = pd.DatetimeIndex(corr.dropna().index)
        # correlation series must contain NO weekend dates
        self.assertFalse((idx.dayofweek >= 5).any())
        self.assertGreater(len(corr.dropna()), 100)

    def test_annualization_factor_by_calendar(self):
        # same per-period sigma → 7-day series annualizes ~sqrt(365/252) higher
        rng = np.random.default_rng(7)
        rets = rng.normal(0, 0.01, 900)
        s7 = pd.Series(100 * np.exp(np.cumsum(rets)), index=pd.date_range("2023-01-01", periods=900, freq="D"))
        s5 = pd.Series(100 * np.exp(np.cumsum(rets[:620])), index=pd.bdate_range("2023-01-01", periods=620))
        v7 = float(realized_vol(s7, 500).dropna().iloc[-1])
        v5 = float(realized_vol(s5, 500).dropna().iloc[-1])
        self.assertAlmostEqual(v7 / v5, np.sqrt(365 / 252), delta=0.08)


class TestVitalsMath(unittest.TestCase):
    def test_drawdowns(self):
        idx = pd.date_range("2024-01-01", periods=400, freq="D")
        vals = np.concatenate([np.linspace(100, 200, 200), np.linspace(200, 150, 200)])
        s = pd.Series(vals, index=idx)
        d = drawdowns(s)
        self.assertAlmostEqual(d["from_ath"], -25.0, places=1)   # 150 vs ATH 200
        self.assertAlmostEqual(d["ath"], 200.0, places=1)
        self.assertLessEqual(d["from_52w_high"], 0)

    def test_ma200_distance(self):
        s = pd.Series(np.full(300, 100.0), index=pd.date_range("2024-01-01", periods=300, freq="D"))
        self.assertAlmostEqual(ma200_distance(s), 0.0)
        self.assertIsNone(ma200_distance(s.head(100)))  # short-history guard

    def test_weekend_vol_split(self):
        # weekends 3x the weekday sigma → split must reflect it
        idx = pd.date_range("2024-01-01", periods=730, freq="D")
        rng = np.random.default_rng(3)
        sigma = np.where(idx.dayofweek >= 5, 0.03, 0.01)
        s = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, sigma))), index=idx)
        split = weekend_vol_split(s)
        self.assertGreater(split["weekend_ann"], 2 * split["weekday_ann"])
        self.assertIsNone(weekend_vol_split(_bdays()))  # not a 7-day asset

    def test_corr_stats_range(self):
        s = _seven_day(); other = _bdays()
        st = corr_stats(rolling_corr(s, other))
        self.assertLessEqual(st["min_1y"], st["current"])
        self.assertGreaterEqual(st["max_1y"], st["current"])

    def test_corr_short_history_guard(self):
        self.assertIsNone(corr_stats(pd.Series(dtype=float)))


class TestAssetRegistry(unittest.TestCase):
    def test_all_assets_are_existing_hmm_markets(self):
        for slug, cfg in ASSETS.items():
            self.assertIn(cfg["key"], INDICES)        # reuse, never re-model
            self.assertNotIn(slug, COUNTRIES)         # no country/asset slug collision
            self.assertIn(cfg["headline_corr"], ("spx", "real10y"))

    def test_headline_stats_per_class(self):
        # real-yield corr headlines metals; SPX corr headlines crypto
        self.assertEqual(ASSETS["gold"]["headline_corr"], "real10y")
        self.assertEqual(ASSETS["bitcoin"]["headline_corr"], "spx")


if __name__ == "__main__":
    unittest.main()
