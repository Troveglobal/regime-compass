"""Unit tests for the Macro Surprise Meter math (pure functions in
src/macro.py — no network). Run with:

    venv/bin/python -m unittest tests.test_macro -v
"""
import unittest

import numpy as np
import pandas as pd

from src.macro import (
    SURPRISE_SERIES,
    _pulse,
    _series_frame,
    build_composites,
    staleness_weight,
    surprise_z,
    zone_label,
)


def _monthly(values, start="2015-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="MS")
    return pd.Series(values, index=idx, dtype=float)


def _cfg(sid):
    return next(c for c in SURPRISE_SERIES if c["id"] == sid)


class TestSurpriseZ(unittest.TestCase):
    def test_known_input_known_output(self):
        # Flat pulse of 1.0 for 60 months, then a single 2.0 shock on a
        # series whose prior dispersion is exactly known.
        rng = np.random.default_rng(0)
        base = rng.normal(1.0, 0.5, size=120)
        pulse = _monthly(list(base) + [base.mean() + 1.0])
        z = surprise_z(pulse, sign=+1, sigma_win=60, sigma_min=36)
        # z_t = (pulse - trailing-12 mean) / trailing-60 std — verify by hand
        prior = pulse.shift(1)
        expected = prior.rolling(12).mean().iloc[-1]
        sigma = prior.rolling(60, min_periods=36).std().iloc[-1]
        self.assertAlmostEqual(z.iloc[-1], (pulse.iloc[-1] - expected) / sigma, places=10)

    def test_clipping(self):
        pulse = _monthly([1.0] * 60 + [500.0])
        # constant history → sigma 0 for most, but add tiny noise to avoid the guard
        pulse.iloc[:60] += np.random.default_rng(1).normal(0, 0.01, 60)
        z = surprise_z(pulse, sign=+1, sigma_win=60, sigma_min=36)
        self.assertEqual(z.iloc[-1], 3.0)  # clipped at +3

    def test_zero_sigma_guard(self):
        pulse = _monthly([2.0] * 80)
        z = surprise_z(pulse, sign=+1, sigma_win=60, sigma_min=36)
        self.assertTrue(z.isna().all())  # constant series → no z, not inf

    def test_short_history_excluded(self):
        pulse = _monthly([1.0, 2.0, 1.5] * 5)  # 15 obs < sigma_min 36
        z = surprise_z(pulse, sign=+1, sigma_win=60, sigma_min=36)
        self.assertTrue(z.isna().all())

    def test_no_lookahead_in_moments(self):
        # The latest observation must not enter its own expectation or sigma:
        # append a huge value; expectation at t must be unchanged.
        base = _monthly(list(np.random.default_rng(2).normal(0, 1, 60)))
        with_shock = pd.concat([base, _monthly([100.0], start="2020-01-01")])
        with_shock.index = pd.date_range("2015-01-01", periods=61, freq="MS")
        prior_mean = base.rolling(12).mean().iloc[-1]  # uses obs 49..60
        z = surprise_z(with_shock, sign=+1, sigma_win=60, sigma_min=36)
        expected_at_t = with_shock.shift(1).rolling(12).mean().iloc[-1]
        self.assertAlmostEqual(expected_at_t, base.iloc[-12:].mean(), places=10)
        self.assertGreater(z.iloc[-1], 2.9)  # shock registers as hot, clipped


class TestStalenessDecay(unittest.TestCase):
    def test_halflife(self):
        self.assertAlmostEqual(float(staleness_weight(0)), 1.0)
        self.assertAlmostEqual(float(staleness_weight(45)), 0.5)
        self.assertAlmostEqual(float(staleness_weight(90)), 0.25)

    def test_monotone(self):
        w = staleness_weight(np.array([0, 10, 30, 60, 120]))
        self.assertTrue(np.all(np.diff(w) < 0))


class TestSignInversion(unittest.TestCase):
    def test_icsa_up_pushes_composite_down(self):
        # Rising jobless claims (ICSA sign=-1) must produce a NEGATIVE z and
        # hence push the composite DOWN.
        cfg = _cfg("ICSA")
        rng = np.random.default_rng(3)
        idx = pd.date_range("2018-01-06", periods=320, freq="W-SAT")
        vals = rng.normal(220_000, 8_000, size=320)
        vals[-1] = 320_000  # claims spike
        raw = pd.DataFrame({"date": idx, "value": vals})
        frame = _series_frame(cfg, raw)
        self.assertLess(frame["z"].iloc[-1], 0)  # hotter=LOWER: spike is cold

        days = pd.bdate_range(end=frame.index.max() + pd.Timedelta(days=5), periods=400)
        comps = build_composites({"ICSA": frame}, days)
        self.assertLess(comps["composite"].dropna().iloc[-1], 0)

    def test_unrate_up_is_cold(self):
        cfg = _cfg("UNRATE")
        rng = np.random.default_rng(5)
        vals = list(np.linspace(4.0, 3.8, 80) + rng.normal(0, 0.05, 80)) + [4.6]  # jump in unemployment
        raw = pd.DataFrame({"date": pd.date_range("2015-01-01", periods=81, freq="MS"),
                            "value": vals})
        frame = _series_frame(cfg, raw)
        self.assertLess(frame["z"].iloc[-1], 0)


class TestPublicationLag(unittest.TestCase):
    def test_observation_not_available_before_lag(self):
        cfg = _cfg("PAYEMS")  # lag 35 days from observation date
        rng = np.random.default_rng(4)
        idx = pd.date_range("2015-01-01", periods=80, freq="MS")
        raw = pd.DataFrame({"date": idx, "value": 130_000 + rng.normal(150, 80, 80).cumsum()})
        frame = _series_frame(cfg, raw)
        # every availability date must be exactly obs_date + lag
        gaps = (frame.index - pd.DatetimeIndex(frame["obs_date"])).days
        self.assertTrue((gaps == cfg["lag_days"]).all())

        # composite on a day before the last obs becomes available must not
        # reflect it: it uses the previous observation instead
        last_obs = frame["obs_date"].iloc[-1]
        avail = frame.index[-1]
        day_before = pd.bdate_range(end=avail - pd.Timedelta(days=1), periods=300)
        comps_before = build_composites({"PAYEMS": frame}, day_before)
        used = frame[frame.index <= day_before[-1]]["obs_date"].iloc[-1]
        self.assertLess(used, last_obs)

    def test_weight_reflects_observation_age_not_release_age(self):
        # On the release day itself, weight is already decayed by the lag.
        cfg = _cfg("CPIAUCSL")  # lag 43
        expected_w = float(staleness_weight(cfg["lag_days"]))
        self.assertAlmostEqual(expected_w, 0.5 ** (43 / 45.0))


class TestComposites(unittest.TestCase):
    def _frame(self, sid, z_value, obs, lag):
        return pd.DataFrame(
            {"z": [z_value], "pulse": [z_value], "expected": [0.0], "obs_date": [obs]},
            index=[obs + pd.Timedelta(days=lag)])

    def test_weighted_average_and_subindices(self):
        obs = pd.Timestamp("2024-01-01")
        frames = {
            "PAYEMS": self._frame("PAYEMS", 2.0, obs, 35),        # labor → growth
            "CPIAUCSL": self._frame("CPIAUCSL", -1.0, obs, 43),   # inflation
        }
        days = pd.bdate_range("2024-03-01", periods=5)
        comps = build_composites(frames, days)
        d = days[-1]
        w_pay = float(staleness_weight((d - obs).days))
        w_cpi = w_pay  # same obs date → same age → same weight
        expected = (2.0 * w_pay + -1.0 * w_cpi) / (w_pay + w_cpi)
        self.assertAlmostEqual(comps.loc[d, "composite"], expected, places=10)
        self.assertAlmostEqual(comps.loc[d, "growth"], 2.0, places=10)
        self.assertAlmostEqual(comps.loc[d, "inflation"], -1.0, places=10)


class TestZones(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(zone_label(1.5), "Hot")
        self.assertEqual(zone_label(0.5), "Warm")
        self.assertEqual(zone_label(0.0), "Neutral")
        self.assertEqual(zone_label(-0.5), "Cool")
        self.assertEqual(zone_label(-1.5), "Cold")


class TestPulseTransforms(unittest.TestCase):
    def test_ann_3m(self):
        # 1% per month for 3 months → (1.01^3)^4 - 1 annualized
        s = _monthly([100 * 1.01 ** i for i in range(10)])
        p = _pulse(s, "ann_3m")
        self.assertAlmostEqual(p.iloc[-1], ((1.01 ** 3) ** 4 - 1) * 100, places=6)

    def test_avg3_yoy(self):
        s = _monthly([100.0] * 12 + [110.0] * 12)
        p = _pulse(s, "avg3_yoy")
        self.assertAlmostEqual(p.iloc[-1], 10.0, places=6)


if __name__ == "__main__":
    unittest.main()
