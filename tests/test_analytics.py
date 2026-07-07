"""Unit tests for the pure analytics functions (regime z-score, calendar
alignment, turbulence, absorption ratio). Run with:

    venv/bin/python -m unittest tests.test_analytics -v
"""
import unittest

import numpy as np
import pandas as pd

from src.analytics import (
    absorption_series,
    align_calendar,
    delta_ar,
    regime_zscore,
    trailing_percentile,
    turbulence_series,
)


class TestRegimeZscore(unittest.TestCase):
    def test_known_input_known_output(self):
        # State sample with mean 0.001, std 0.01 → r_today = -0.019 is z = -2.0
        rng = np.random.default_rng(0)
        state = rng.normal(0.001, 0.01, size=5000)
        mu, sigma = state.mean(), state.std(ddof=1)
        r_today = mu - 2 * sigma
        out = regime_zscore(r_today, state, state)
        self.assertFalse(out["fallback"])
        self.assertAlmostEqual(out["z"], -2.0, places=6)
        self.assertLess(out["percentile"], 5.0)

    def test_exact_small_sample(self):
        # Deterministic check with min_obs relaxed: sample [1,2,3,4,5],
        # mean 3, std ddof1 = sqrt(2.5); r_today=5 → z = 2/sqrt(2.5)
        sample = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        out = regime_zscore(5.0, sample, sample, min_obs=3)
        self.assertAlmostEqual(out["z"], 2.0 / np.sqrt(2.5), places=12)
        self.assertAlmostEqual(out["percentile"], 80.0)  # 4 of 5 strictly below

    def test_fallback_below_min_obs(self):
        state = np.array([0.01] * 10)
        full = np.random.default_rng(1).normal(0, 0.01, 1000)
        out = regime_zscore(0.02, state, full)
        self.assertTrue(out["fallback"])
        self.assertEqual(out["n_obs"], 1000)  # scored vs full history

    def test_zero_sigma_guard(self):
        constant = np.zeros(500)
        out = regime_zscore(0.01, constant, constant)
        self.assertIsNone(out["z"])  # division-by-zero guarded, not inf

    def test_nan_guard(self):
        state = np.array([0.01, np.nan, -0.01] * 100)
        out = regime_zscore(np.nan, state, state)
        self.assertIsNone(out["z"])


class TestAlignCalendar(unittest.TestCase):
    def test_single_day_gap_ffilled_two_day_gap_dropped(self):
        days = pd.date_range("2024-01-01", periods=8, freq="D")
        a = pd.Series(np.arange(8.0), index=days)
        # b misses day 3 (single gap → ffill) and days 5-6 (2-day gap → dropped)
        b = a.drop([days[3], days[5], days[6]])
        out = align_calendar({"a": a, "b": b}, max_ffill=1)
        self.assertIn(days[3], out.index)                  # ffilled
        self.assertEqual(out.loc[days[3], "b"], b.loc[days[2]])
        self.assertNotIn(days[6], out.index)               # 2nd gap day dropped
        self.assertIn(days[7], out.index)                  # resumes after gap

    def test_inner_join_start(self):
        days = pd.date_range("2024-01-01", periods=10, freq="D")
        a = pd.Series(1.0, index=days)
        b = pd.Series(1.0, index=days[4:])  # starts later
        out = align_calendar({"a": a, "b": b})
        self.assertEqual(out.index[0], days[4])


class TestTurbulence(unittest.TestCase):
    def test_planted_outlier_spikes(self):
        # 3 correlated assets, calm gaussian world, one planted shock day
        rng = np.random.default_rng(42)
        n, window = 400, 200
        common = rng.normal(0, 0.008, n)
        X = np.column_stack([common + rng.normal(0, 0.004, n) for _ in range(3)])
        shock_day = 350
        X[shock_day] = [0.08, -0.06, 0.07]  # violent, correlation-breaking move
        rets = pd.DataFrame(X, index=pd.date_range("2020-01-01", periods=n, freq="B"),
                            columns=list("abc"))
        d = turbulence_series(rets, window=window)
        shock_val = d.loc[rets.index[shock_day]]
        others = d.drop(rets.index[shock_day])
        self.assertGreater(shock_val, others.quantile(0.99) * 5)
        self.assertEqual(shock_val, d.max())

    def test_no_lookahead_shock_absent_from_own_baseline(self):
        # The day AFTER the shock, the shock IS in the baseline; the shock day
        # itself must be scored against a baseline that excludes it. If it
        # leaked in, its own distance would shrink materially.
        rng = np.random.default_rng(7)
        n, window = 260, 250
        X = rng.normal(0, 0.01, (n, 3))
        rets = pd.DataFrame(X, index=pd.date_range("2021-01-01", periods=n, freq="B"),
                            columns=list("abc"))
        d = turbulence_series(rets, window=window)
        # series starts exactly at index `window` (first day with a full baseline)
        self.assertEqual(d.index[0], rets.index[window])


class TestAbsorptionRatio(unittest.TestCase):
    def test_single_factor_ar_approaches_one(self):
        # All assets driven by one factor + tiny idiosyncratic noise → the top
        # eigenvector absorbs nearly all variance, AR → 1.0
        rng = np.random.default_rng(3)
        n = 300
        factor = rng.normal(0, 0.02, n)
        X = np.column_stack([factor * b + rng.normal(0, 0.0005, n)
                             for b in (0.8, 1.0, 1.2, 0.9, 1.1)])
        rets = pd.DataFrame(X, index=pd.date_range("2022-01-01", periods=n, freq="B"))
        ar = absorption_series(rets, window=250, n_components=1)
        self.assertGreater(float(ar.iloc[-1]), 0.98)

    def test_independent_assets_ar_low(self):
        rng = np.random.default_rng(4)
        X = rng.normal(0, 0.01, (300, 5))
        rets = pd.DataFrame(X, index=pd.date_range("2022-01-01", periods=300, freq="B"))
        ar = absorption_series(rets, window=250, n_components=1)
        # 5 iid assets: top-1 eigenvalue ≈ 1/5 of variance (plus sampling noise)
        self.assertLess(float(ar.iloc[-1]), 0.45)

    def test_delta_ar_zero_when_stable(self):
        ar = pd.Series(0.7, index=pd.date_range("2020-01-01", periods=300, freq="B"))
        ar += np.linspace(0, 1e-9, 300)  # avoid exactly-zero std
        d = delta_ar(ar)
        self.assertAlmostEqual(float(d.iloc[-1]), 1.0, delta=1.5)  # tiny drift, bounded


class TestTrailingPercentile(unittest.TestCase):
    def test_max_value_is_high_percentile(self):
        s = pd.Series(np.arange(100.0))
        self.assertEqual(trailing_percentile(s, 99.0, window=100), 99.0)
        self.assertIsNone(trailing_percentile(s, np.nan))


if __name__ == "__main__":
    unittest.main()
