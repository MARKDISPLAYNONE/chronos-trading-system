"""
Tests for tick-grid quantization of SL / TP / partial trigger.

Rule (TRD §4.1.2, PRD §6.7): after calculation, snap the three ORDER prices to
the instrument's tick grid, directionally — SHORT floors, LONG ceils. Every
snap must move the price in the direction that hurts the trade, never in its
favour. Mid_A and Range stay exact (Q6).

These tests pin both halves of that: the arithmetic, and the pessimism.
"""

import pathlib
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.pricing import CEILING, FLOOR, is_on_tick_grid, quantize_to_tick  # noqa: E402
from src.trade_calculator import LONG, SHORT, plan_trade  # noqa: E402

TICK = Decimal("0.1")


def D(x):
    return Decimal(str(x))


class TestQuantizePrimitive(unittest.TestCase):
    def test_floor(self):
        self.assertEqual(quantize_to_tick(D("25420.75"), TICK, FLOOR), D("25420.7"))
        self.assertEqual(quantize_to_tick(D("25420.70"), TICK, FLOOR), D("25420.7"))

    def test_ceiling(self):
        self.assertEqual(quantize_to_tick(D("25420.75"), TICK, CEILING), D("25420.8"))
        self.assertEqual(quantize_to_tick(D("25420.70"), TICK, CEILING), D("25420.7"))

    def test_already_on_grid_is_unchanged(self):
        for direction in (FLOOR, CEILING):
            self.assertEqual(quantize_to_tick(D("99.3"), TICK, direction), D("99.3"))

    def test_result_is_always_on_grid(self):
        for raw in ("25420.75", "25430.55", "1.04", "1.06", "99999.99"):
            for direction in (FLOOR, CEILING):
                self.assertTrue(is_on_tick_grid(quantize_to_tick(D(raw), TICK, direction), TICK))

    def test_never_rounds_nearest(self):
        # nearest would send 25420.75 up; floor must send it down
        self.assertLess(quantize_to_tick(D("25420.75"), TICK, FLOOR), D("25420.75"))
        self.assertGreater(quantize_to_tick(D("25420.75"), TICK, CEILING), D("25420.75"))

    def test_rejects_unknown_direction(self):
        with self.assertRaises(ValueError):
            quantize_to_tick(D("1.0"), TICK, "nearest")


class TestOffGridCandleA(unittest.TestCase):
    """Candle A with an ODD tick-count range -> Mid_A is a half-tick."""

    # High 25430.5 / Low 25411.0 -> Range 19.5 = 195 ticks (odd); Mid 25420.75
    HIGH = D("25430.5")
    LOW = D("25411.0")
    MID = D("25420.75")

    def test_mid_really_is_off_grid(self):
        self.assertFalse(is_on_tick_grid(self.MID, TICK))

    def test_short_prices_all_land_on_the_grid(self):
        p = plan_trade(SHORT, self.HIGH, self.LOW, self.MID, TICK)
        for value in (p.entry, p.stop_loss, p.take_profit, p.partial_trigger):
            self.assertTrue(is_on_tick_grid(value, TICK), value)

    def test_long_prices_all_land_on_the_grid(self):
        p = plan_trade(LONG, self.HIGH, self.LOW, self.MID, TICK)
        for value in (p.entry, p.stop_loss, p.take_profit, p.partial_trigger):
            self.assertTrue(is_on_tick_grid(value, TICK), value)

    def test_short_is_rounded_down(self):
        p = plan_trade(SHORT, self.HIGH, self.LOW, self.MID, TICK)
        # SL = High + 0.5*Range = 25440.25 ; TP = Mid - tick = 25420.65
        self.assertEqual(p.raw_stop_loss, D("25440.25"))
        self.assertEqual(p.raw_take_profit, D("25420.65"))
        self.assertEqual(p.stop_loss, D("25440.2"))
        self.assertEqual(p.take_profit, D("25420.6"))
        self.assertLess(p.stop_loss, p.raw_stop_loss)
        self.assertLess(p.take_profit, p.raw_take_profit)

    def test_long_is_rounded_up(self):
        p = plan_trade(LONG, self.HIGH, self.LOW, self.MID, TICK)
        # SL = Low - 0.5*Range = 25401.25 ; TP = Mid + tick = 25420.85
        self.assertEqual(p.raw_stop_loss, D("25401.25"))
        self.assertEqual(p.raw_take_profit, D("25420.85"))
        self.assertEqual(p.stop_loss, D("25401.3"))
        self.assertEqual(p.take_profit, D("25420.9"))
        self.assertGreater(p.stop_loss, p.raw_stop_loss)
        self.assertGreater(p.take_profit, p.raw_take_profit)

    def test_mid_and_range_are_not_quantized(self):
        p = plan_trade(SHORT, self.HIGH, self.LOW, self.MID, TICK)
        self.assertEqual(p.mid_a, self.MID)                     # exact, per Q6
        self.assertEqual(p.candle_a_range, D("19.5"))           # exact


class TestPessimism(unittest.TestCase):
    """The snap must never improve a trade. This is the whole point of the rule."""

    HIGH = D("25430.5")
    LOW = D("25411.0")
    MID = D("25420.75")

    def test_short_stop_moves_closer_to_entry(self):
        p = plan_trade(SHORT, self.HIGH, self.LOW, self.MID, TICK)
        self.assertLess(p.stop_loss - p.entry, p.raw_stop_loss - p.entry)  # tighter stop

    def test_short_target_moves_further_from_entry(self):
        p = plan_trade(SHORT, self.HIGH, self.LOW, self.MID, TICK)
        self.assertGreater(p.entry - p.take_profit, p.entry - p.raw_take_profit)

    def test_short_partial_never_moves_closer_to_entry(self):
        p = plan_trade(SHORT, self.HIGH, self.LOW, self.MID, TICK)
        # this particular 80% level lands exactly on-grid, so it must not move;
        # the invariant is "never closer to entry", not "always strictly further"
        self.assertGreaterEqual(p.entry - p.partial_trigger, p.entry - p.raw_partial_trigger)

    def test_short_partial_is_floored_when_off_grid(self):
        # High 100.2 / Low 99.0 -> Range 1.2, Mid 99.6, entry 100.1, TP 99.5
        # raw partial = 100.1 - 0.8*0.6 = 99.62 -> floor -> 99.6
        p = plan_trade(SHORT, D("100.2"), D("99.0"), D("99.6"), TICK)
        self.assertEqual(p.raw_partial_trigger, D("99.62"))
        self.assertEqual(p.partial_trigger, D("99.6"))
        self.assertLess(p.partial_trigger, p.raw_partial_trigger)

    def test_long_stop_moves_closer_to_entry(self):
        p = plan_trade(LONG, self.HIGH, self.LOW, self.MID, TICK)
        self.assertLess(p.entry - p.stop_loss, p.entry - p.raw_stop_loss)

    def test_long_target_moves_further_from_entry(self):
        p = plan_trade(LONG, self.HIGH, self.LOW, self.MID, TICK)
        self.assertGreater(p.take_profit - p.entry, p.raw_take_profit - p.entry)

    def test_long_partial_never_moves_closer_to_entry(self):
        p = plan_trade(LONG, self.HIGH, self.LOW, self.MID, TICK)
        self.assertGreaterEqual(p.partial_trigger - p.entry, p.raw_partial_trigger - p.entry)

    def test_long_partial_is_ceiled_when_off_grid(self):
        p = plan_trade(LONG, D("100.2"), D("99.0"), D("99.6"), TICK)
        self.assertEqual(p.raw_partial_trigger, D("99.58"))
        self.assertEqual(p.partial_trigger, D("99.6"))
        self.assertGreater(p.partial_trigger, p.raw_partial_trigger)


class TestRiskAndConsistency(unittest.TestCase):
    def test_risk_is_measured_after_quantization(self):
        p = plan_trade(SHORT, D("25430.5"), D("25411.0"), D("25420.75"), TICK)
        self.assertEqual(p.risk_per_unit, abs(p.entry - p.stop_loss))

    def test_plan_stays_ordered_after_quantization(self):
        # sweep a range of odd and even tick-count ranges in both directions
        for ticks in range(2, 60):
            low = D("25000.0")
            high = low + TICK * ticks
            mid = (high + low) / 2
            for direction in (SHORT, LONG):
                p = plan_trade(direction, high, low, mid, TICK)
                if direction == SHORT:
                    self.assertTrue(p.stop_loss > p.entry > p.take_profit, (ticks, p))
                    self.assertTrue(p.entry >= p.partial_trigger >= p.take_profit, (ticks, p))
                else:
                    self.assertTrue(p.stop_loss < p.entry < p.take_profit, (ticks, p))
                    self.assertTrue(p.entry <= p.partial_trigger <= p.take_profit, (ticks, p))

    def test_partial_never_precedes_target(self):
        # with a 2-tick range the TP distance is tiny; quantization must not
        # leave the partial beyond TP (which would be an invalid plan)
        p = plan_trade(SHORT, D("100.2"), D("100.0"), D("100.1"), TICK)
        self.assertGreaterEqual(p.partial_trigger, p.take_profit)
        p = plan_trade(LONG, D("100.2"), D("100.0"), D("100.1"), TICK)
        self.assertLessEqual(p.partial_trigger, p.take_profit)

    def test_even_tick_range_is_left_alone(self):
        # Range = 20.0 = 200 ticks (even) -> Mid on grid -> no snap needed
        p = plan_trade(SHORT, D("100.0"), D("80.0"), D("90.0"), TICK)
        self.assertFalse(p.quantized)
        self.assertEqual(p.stop_loss, p.raw_stop_loss)
        self.assertEqual(p.take_profit, p.raw_take_profit)


if __name__ == "__main__":
    unittest.main()
