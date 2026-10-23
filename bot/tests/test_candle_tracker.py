"""Tests for Candle A / Candle B aggregation (PRD §6.2, §6.3)."""

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.bars import BarSeries, M1Bar  # noqa: E402
from src.candle_tracker import build_candle  # noqa: E402

T0 = dt.datetime(2026, 3, 4, 9, 45)


def bar(i, o, h, l, c):
    return M1Bar(T0 + dt.timedelta(minutes=i), o, h, l, c, 10)


class TestAggregation(unittest.TestCase):
    def test_ohlc_and_derived_values(self):
        bars = [
            bar(0, 99.5, 99.8, 99.4, 99.6),
            bar(1, 99.6, 100.2, 99.5, 99.9),   # high
            bar(2, 99.9, 100.0, 98.7, 99.2),   # low
            bar(3, 99.2, 99.4, 99.0, 99.3),
        ]
        c = build_candle(BarSeries(bars), T0, 15)
        self.assertIsNotNone(c)
        self.assertEqual(c.open, 99.5)
        self.assertEqual(c.high, 100.2)
        self.assertEqual(c.low, 98.7)
        self.assertEqual(c.close, 99.3)
        self.assertAlmostEqual(c.mid, (100.2 + 98.7) / 2)
        self.assertAlmostEqual(c.range, 100.2 - 98.7)
        self.assertEqual(c.n_bars, 4)
        self.assertFalse(c.complete)

    def test_complete_candle(self):
        bars = [bar(i, 100.0, 100.1, 99.9, 100.0) for i in range(15)]
        c = build_candle(BarSeries(bars), T0, 15)
        self.assertTrue(c.complete)
        self.assertEqual(c.n_bars, 15)

    def test_no_bars_returns_none(self):
        other = BarSeries([bar(20, 100.0, 100.1, 99.9, 100.0)])
        self.assertIsNone(build_candle(other, T0, 15))

    def test_bars_outside_the_candle_are_excluded(self):
        bars = [bar(14, 100.0, 100.5, 99.5, 100.0), bar(15, 101.0, 105.0, 101.0, 104.0)]
        c = build_candle(BarSeries(bars), T0, 15)
        # the 15th minute belongs to Candle B, not Candle A
        self.assertEqual(c.high, 100.5)
        self.assertEqual(c.n_bars, 1)

    def test_mid_need_not_be_tick_aligned(self):
        bars = [bar(0, 99.7, 100.0, 99.7, 99.8)]
        c = build_candle(BarSeries(bars), T0, 15)
        self.assertAlmostEqual(c.mid, 99.85)  # half-tick, and that is correct


if __name__ == "__main__":
    unittest.main()
