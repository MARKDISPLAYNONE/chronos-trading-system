"""End-to-end tests for one window evaluation (the decision order in §8.1)."""

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.bars import BarSeries, M1Bar  # noqa: E402
from src.time_windows import Window  # noqa: E402
from src.window_evaluator import (  # noqa: E402
    OUTCOME_LOSS,
    OUTCOME_NO_SETUP,
    OUTCOME_WIN,
    REASON_MARKET_CLOSED,
    REASON_MISSING_DATA,
    REASON_OUTSIDE_RANGE,
    evaluate_window,
)

DAY = dt.date(2026, 3, 4)  # Wednesday
W = Window(DAY, 9)  # 09:45-10:15
START = dt.datetime(2026, 3, 4, 9, 45)


def b(minute, o, h, l, c):
    return M1Bar(START + dt.timedelta(minutes=minute), o, h, l, c, 10)


def candle_a(high=100.0, low=99.0, close=99.6):
    """15 bars forming Candle A with the requested extremes."""
    bars = []
    for i in range(15):
        o = close if i else low
        hh = high if i == 5 else max(o, 99.7)
        ll = low if i == 10 else min(o, 99.7)
        c_ = close if i == 14 else (99.7 if i < 14 else close)
        bars.append(b(i, o, max(hh, c_, o), min(ll, c_, o), c_))
    return bars


class TestDataAvailability(unittest.TestCase):
    def test_no_bars_at_all_is_market_closed(self):
        # series exists but has nothing anywhere near the window
        far = BarSeries([b(0, 100.0, 100.1, 99.9, 100.0).__class__(
            dt.datetime(2026, 3, 4, 12, 0), 100.0, 100.1, 99.9, 100.0, 10)])
        ev = evaluate_window(far, W)
        self.assertEqual(ev.outcome, OUTCOME_NO_SETUP)
        self.assertEqual(ev.reason, REASON_MARKET_CLOSED)

    def test_file_ending_before_the_window_is_outside_range(self):
        series = BarSeries(candle_a())  # series stops at 09:59
        ev = evaluate_window(series, W)
        self.assertEqual(ev.outcome, OUTCOME_NO_SETUP)
        self.assertEqual(ev.reason, REASON_OUTSIDE_RANGE)

    def test_hole_while_market_open_is_missing_data(self):
        # Candle A complete, one Candle B minute missing, trading continues after
        bars = candle_a() + [b(15 + i, 99.8, 99.9, 99.7, 99.8) for i in range(15) if i != 4]
        bars += [b(40 + i, 99.8, 99.9, 99.7, 99.8) for i in range(30)]
        ev = evaluate_window(BarSeries(bars), W)
        self.assertEqual(ev.reason, REASON_MISSING_DATA)


class TestDecisionPaths(unittest.TestCase):
    def test_void_open(self):
        # Candle A closes exactly at its high -> Candle B opens at High_A -> VOID
        bars = candle_a(close=100.0)
        bars += [b(15 + i, 100.0, 100.0, 99.9, 99.95) for i in range(15)]
        ev = evaluate_window(BarSeries(bars), W)
        self.assertEqual(ev.context, "VOID")
        self.assertEqual(ev.reason, "invalid open location")

    def test_no_sweep(self):
        bars = candle_a()
        bars += [b(15 + i, 99.8, 99.9, 99.7, 99.8) for i in range(15)]
        ev = evaluate_window(BarSeries(bars), W)
        self.assertEqual(ev.context, "PREMIUM")
        self.assertEqual(ev.reason, "no sweep")

    def test_full_trade_take_profit(self):
        bars = candle_a()                       # High 100.0 Low 99.0 Mid 99.5
        bars += [b(15 + i, 99.8, 99.9, 99.7, 99.8) for i in range(6)]
        bars += [b(21, 99.85, 100.35, 99.8, 100.3)]   # sweep to +1 tick and beyond
        bars += [b(22, 100.3, 100.32, 99.85, 99.86)]  # reversal -> SHORT at 99.9
        bars += [b(23, 99.86, 99.88, 99.45, 99.5)]    # partial triggers at 99.5
        bars += [b(24, 99.5, 99.52, 99.35, 99.36)]    # TP 99.4 hit
        bars += [b(25 + i, 99.36, 99.4, 99.3, 99.35) for i in range(5)]  # fill Candle B
        ev = evaluate_window(BarSeries(bars), W)
        self.assertEqual(ev.context, "PREMIUM")
        self.assertEqual(ev.outcome, OUTCOME_WIN)
        self.assertEqual(ev.trade.final_outcome, OUTCOME_WIN)
        self.assertTrue(ev.trade.partial_triggered)
        self.assertEqual(ev.reason, "target hit after partial")
        self.assertEqual(ev.plan.direction, "SHORT")
        self.assertAlmostEqual(ev.plan.entry, 99.9)
        self.assertAlmostEqual(ev.plan.stop_loss, 100.5)
        self.assertAlmostEqual(ev.plan.take_profit, 99.4)

    def test_full_stop_loss(self):
        bars = candle_a()
        bars += [b(15 + i, 99.8, 99.9, 99.7, 99.8) for i in range(6)]
        bars += [b(21, 99.85, 100.35, 99.8, 100.3)]
        bars += [b(22, 100.3, 100.32, 99.85, 99.86)]   # SHORT at 99.9
        bars += [b(23, 99.86, 100.6, 99.8, 100.5)]     # SL 100.5
        bars += [b(24 + i, 100.5, 100.55, 100.45, 100.5) for i in range(6)]  # fill Candle B
        ev = evaluate_window(BarSeries(bars), W)
        self.assertEqual(ev.outcome, OUTCOME_LOSS)
        self.assertEqual(ev.reason, "stop loss hit")
        self.assertAlmostEqual(ev.trade.result_r_multiple, -1.0)

    def test_every_evaluation_has_a_non_empty_reason(self):
        bars = candle_a()
        bars += [b(15 + i, 99.8, 99.9, 99.7, 99.8) for i in range(15)]
        ev = evaluate_window(BarSeries(bars), W)
        self.assertTrue(ev.reason)
        self.assertIn(ev.outcome, {"WIN", "LOSS", "NO_SETUP", "MISSING_EVALUATION"})


if __name__ == "__main__":
    unittest.main()
