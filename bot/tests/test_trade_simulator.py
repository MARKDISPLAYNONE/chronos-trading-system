"""Tests for the trade lifecycle simulation (Q7 pessimistic ordering, Q8 fills)."""

import datetime as dt
import pathlib
import sys
import unittest
from decimal import Decimal


def D(x):
    return Decimal(str(x))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.bars import BarSeries, M1Bar  # noqa: E402
from src.trade_calculator import LONG, SHORT, plan_trade  # noqa: E402
from src.trade_simulator import simulate  # noqa: E402

T0 = dt.datetime(2026, 3, 4, 10, 5)
HIGH, LOW, MID = 100.0, 99.0, 99.5
MAX = 5 * 24 * 60

# LONG plan: entry 99.1, SL 98.5, TP 99.6, partial 99.5, R 0.6
# SHORT plan: entry 99.9, SL 100.5, TP 99.4, partial 99.5, R 0.6


def series(*specs, start=T0):
    bars = [
        M1Bar(start + dt.timedelta(minutes=i), o, h, l, c, 100)
        for i, (o, h, l, c) in enumerate(specs)
    ]
    return BarSeries(bars)


class TestLongOutcomes(unittest.TestCase):
    def setUp(self):
        self.plan = plan_trade(LONG, HIGH, LOW, MID)

    def test_straight_to_stop(self):
        s = series((99.1, 99.15, 98.4, 98.5))
        out = simulate(self.plan, s, T0, MAX)
        self.assertTrue(out.resolved)
        self.assertEqual(out.final_outcome, "LOSS")
        self.assertEqual(out.reason, "stop loss hit")
        self.assertEqual(out.final_exit_price, D('98.5'))
        self.assertAlmostEqual(float(out.result_r_multiple), -1.0)

    def test_straight_to_target(self):
        s = series((99.1, 99.7, 99.05, 99.65))
        out = simulate(self.plan, s, T0, MAX)
        self.assertEqual(out.final_outcome, "WIN")
        self.assertEqual(out.reason, "take profit hit")
        self.assertAlmostEqual(float(out.result_r_multiple), 0.5 / 0.6)

    def test_partial_then_breakeven(self):
        s = series(
            (99.3, 99.55, 99.15, 99.5),   # partial at 99.5, stays above entry
            (99.5, 99.52, 99.05, 99.08),  # runner stopped at breakeven
        )
        out = simulate(self.plan, s, T0, MAX)
        self.assertTrue(out.partial_triggered)
        self.assertEqual(out.reason, "breakeven stop hit after partial")
        self.assertEqual(out.partial_exit_price, D('99.5'))
        self.assertEqual(out.final_exit_price, D('99.1'))
        self.assertAlmostEqual(float(out.result_r_multiple), 0.6)
        self.assertEqual(out.final_outcome, "WIN")

    def test_partial_then_target(self):
        s = series(
            (99.3, 99.55, 99.15, 99.5),   # partial, runner not stopped
            (99.5, 99.62, 99.45, 99.6),
        )
        out = simulate(self.plan, s, T0, MAX)
        self.assertTrue(out.partial_triggered)
        self.assertEqual(out.reason, "target hit after partial")
        self.assertAlmostEqual(float(out.result_r_multiple), 0.6 + 0.5 / 6)


class TestPessimisticOrdering(unittest.TestCase):
    """Q7: within one bar -> SL first, then TP, then the partial."""

    def setUp(self):
        self.plan = plan_trade(LONG, HIGH, LOW, MID)

    def test_bar_spanning_both_stop_and_target_is_a_loss(self):
        s = series((99.1, 99.7, 98.4, 99.5))  # touches TP (99.6) and SL (98.5)
        out = simulate(self.plan, s, T0, MAX)
        self.assertEqual(out.final_outcome, "LOSS")
        self.assertEqual(out.reason, "stop loss hit")
        self.assertEqual(out.final_exit_price, D('98.5'))

    def test_bar_spanning_partial_and_stop_is_a_loss_with_no_partial(self):
        s = series((99.1, 99.55, 98.4, 98.9))  # 80% trigger and SL in one bar
        out = simulate(self.plan, s, T0, MAX)
        self.assertFalse(out.partial_triggered)  # SL wins, partial never fires
        self.assertEqual(out.final_outcome, "LOSS")

    def test_runner_stopped_in_the_partial_bar(self):
        # partial fires at 99.5 and the same bar trades back to entry 99.1
        s = series((99.1, 99.55, 99.05, 99.2))
        out = simulate(self.plan, s, T0, MAX)
        self.assertTrue(out.partial_triggered)
        self.assertTrue(out.runner_stopped_in_partial_bar)
        self.assertEqual(out.final_exit_price, D('99.1'))
        self.assertAlmostEqual(float(out.result_r_multiple), 0.6)

    def test_no_fill_assumptions_are_optimistic(self):
        # entry is the plan's entry price, never a later bar's price
        s = series((99.1, 99.7, 99.05, 99.65))
        out = simulate(self.plan, s, T0, MAX)
        self.assertEqual(out.final_exit_price, D('99.6'))  # exact TP, zero slippage


class TestShortOutcomes(unittest.TestCase):
    def setUp(self):
        self.plan = plan_trade(SHORT, HIGH, LOW, MID)

    def test_short_full_stop(self):
        s = series((99.9, 100.6, 99.85, 100.5))
        out = simulate(self.plan, s, T0, MAX)
        self.assertEqual(out.final_outcome, "LOSS")
        self.assertEqual(out.final_exit_price, D('100.5'))
        self.assertAlmostEqual(float(out.result_r_multiple), -1.0)

    def test_short_partial_then_target(self):
        s = series(
            (99.7, 99.88, 99.45, 99.5),   # partial at 99.5, never back at entry
            (99.5, 99.52, 99.38, 99.4),   # TP 99.4
        )
        out = simulate(self.plan, s, T0, MAX)
        self.assertTrue(out.partial_triggered)
        self.assertEqual(out.reason, "target hit after partial")
        self.assertEqual(out.final_exit_price, D('99.4'))
        self.assertGreater(float(out.result_r_multiple), 0)

    def test_short_bar_spanning_partial_and_stop(self):
        s = series((99.9, 100.6, 99.45, 100.2))
        out = simulate(self.plan, s, T0, MAX)
        self.assertFalse(out.partial_triggered)
        self.assertEqual(out.final_outcome, "LOSS")


class TestForwardScanning(unittest.TestCase):
    def test_position_may_close_after_its_window_ends(self):
        # 20 flat bars, then a TP hit well beyond the 30-minute window
        spec = [(99.1, 99.2, 99.05, 99.15)] * 20 + [(99.15, 99.7, 99.1, 99.65)]
        out = simulate(plan_trade(LONG, HIGH, LOW, MID), series(*spec), T0, MAX)
        self.assertTrue(out.resolved)
        self.assertEqual(out.reason, "take profit hit")
        self.assertGreater(out.bars_held, 15)

    def test_unresolved_is_flagged_not_scored(self):
        s = series((99.1, 99.2, 99.05, 99.15))  # never reaches SL/TP/partial
        out = simulate(plan_trade(LONG, HIGH, LOW, MID), s, T0, MAX)
        self.assertFalse(out.resolved)
        self.assertEqual(out.reason, "data_ends_before_trade_closed")


if __name__ == "__main__":
    unittest.main()
