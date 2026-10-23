"""Tests for entry/SL/TP/partial maths and R-multiples (PRD §6.6-§6.8, Q9).

All expected values are hand-calculated in the comments next to them.
"""

import pathlib
import sys
import unittest
from decimal import Decimal


def D(x):
    return Decimal(str(x))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.trade_calculator import LONG, SHORT, plan_trade, r_multiple  # noqa: E402

HIGH, LOW = 100.0, 99.0
MID = 99.5
TICK = 0.1
# Range = 1.0, R = 0.5 * 1.0 + 0.1 = 0.6
# TP distance = Mid -/+ tick - entry = 0.5 = R - 1 tick


class TestShortPlan(unittest.TestCase):
    def setUp(self):
        self.p = plan_trade(SHORT, HIGH, LOW, MID, TICK)

    def test_entry(self):
        self.assertEqual(self.p.entry, D('99.9'))      # High_A - 1 tick

    def test_stop_loss(self):
        self.assertEqual(self.p.stop_loss, D('100.5'))  # High_A + 0.5*Range

    def test_take_profit(self):
        self.assertEqual(self.p.take_profit, D('99.4'))  # Mid_A - 1 tick

    def test_partial_trigger_is_raw_price_distance(self):
        # 99.9 - 0.80 * (99.9 - 99.4) = 99.9 - 0.4 = 99.5
        self.assertEqual(self.p.partial_trigger, D('99.5'))

    def test_risk_unit(self):
        self.assertEqual(self.p.risk_per_unit, D('0.6'))


class TestLongPlan(unittest.TestCase):
    def setUp(self):
        self.p = plan_trade(LONG, HIGH, LOW, MID, TICK)

    def test_entry(self):
        self.assertEqual(self.p.entry, D('99.1'))       # Low_A + 1 tick

    def test_stop_loss(self):
        self.assertEqual(self.p.stop_loss, D('98.5'))   # Low_A - 0.5*Range

    def test_take_profit(self):
        self.assertEqual(self.p.take_profit, D('99.6'))  # Mid_A + 1 tick

    def test_partial_trigger(self):
        # 99.1 + 0.80 * (99.6 - 99.1) = 99.1 + 0.4 = 99.5
        self.assertEqual(self.p.partial_trigger, D('99.5'))

    def test_tp_is_slightly_less_than_one_r(self):
        # A property of the spec, not a bug: TP - entry == R - 1 tick.
        self.assertEqual(self.p.tp_distance, self.p.risk_per_unit - D('0.1'))


class TestRMultiple(unittest.TestCase):
    R = D('0.6')

    def test_full_stop_is_minus_one_r(self):
        # (98.5 - 99.1) / 0.6 = -1.0
        self.assertAlmostEqual(float(r_multiple(LONG, 99.1, self.R, 98.5)), -1.0)

    def test_clean_tp_run_for_short(self):
        # (99.9 - 99.4) / 0.6 = 0.833...
        self.assertAlmostEqual(float(r_multiple(SHORT, 99.9, self.R, 99.4)), 0.5 / 0.6)

    def test_partial_then_breakeven(self):
        # 0.9 * (99.5 - 99.1)/0.6 + 0.1 * 0 = 0.9 * 0.6667 = 0.6
        self.assertAlmostEqual(
            float(r_multiple(LONG, 99.1, self.R, 99.1, partial_exit_price=99.5)), 0.6
        )

    def test_partial_then_tp(self):
        # 0.9 * 0.4/0.6 + 0.1 * 0.5/0.6 = 0.6 + 0.08333 = 0.68333
        self.assertAlmostEqual(
            float(r_multiple(LONG, 99.1, self.R, 99.6, partial_exit_price=99.5)), 0.6 + 0.5 / 6
        )

    def test_partial_then_breakeven_is_a_win(self):
        r = r_multiple(LONG, 99.1, self.R, 99.1, partial_exit_price=99.5)
        self.assertGreater(float(r), 0)

    def test_wide_range_approaches_the_072_limit(self):
        # As Candle A's range grows, the tick becomes negligible and
        # partial-then-breakeven approaches 0.9 * 0.8 = 0.72R.
        wide = plan_trade(LONG, D('1100.0'), D('1000.0'), D('1050.0'), D('0.1'))
        r = r_multiple(
            LONG, wide.entry, wide.risk_per_unit, wide.entry,
            partial_exit_price=wide.partial_trigger,
        )
        # partial_R = 0.9 * distance / R  (the 0.9 is the 9-of-10 size weight)
        self.assertAlmostEqual(float(r), float(D('0.9') * wide.partial_distance / wide.risk_per_unit))
        self.assertAlmostEqual(float(r), 0.7186, places=4)  # 0.9 * 40.0 / 50.1


class TestGuards(unittest.TestCase):
    def test_degenerate_candle_raises(self):
        with self.assertRaises(ValueError):
            plan_trade(LONG, 100.0, 100.0, 100.0, TICK)

    def test_unknown_direction_raises(self):
        with self.assertRaises(ValueError):
            plan_trade("SIDEWAYS", HIGH, LOW, MID, TICK)


if __name__ == "__main__":
    unittest.main()
