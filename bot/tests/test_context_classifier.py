"""Tests for Premium/Discount/Void classification (PRD §6.4, Q6)."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.context_classifier import (  # noqa: E402
    REASON_INVALID_OPEN_LOCATION,
    Context,
    classify,
)

LOW, MID, HIGH = 99.0, 99.5, 100.0


class TestClassification(unittest.TestCase):
    def test_premium_when_open_between_mid_and_high(self):
        c = classify(99.8, LOW, MID, HIGH)
        self.assertIs(c.context, Context.PREMIUM)
        self.assertIsNone(c.reason)

    def test_discount_when_open_between_low_and_mid(self):
        c = classify(99.2, LOW, MID, HIGH)
        self.assertIs(c.context, Context.DISCOUNT)

    def test_void_at_exact_midpoint(self):
        c = classify(99.5, LOW, MID, HIGH)
        self.assertIs(c.context, Context.VOID)
        self.assertEqual(c.reason, REASON_INVALID_OPEN_LOCATION)

    def test_void_at_high(self):
        self.assertIs(classify(100.0, LOW, MID, HIGH).context, Context.VOID)

    def test_void_above_high(self):
        self.assertIs(classify(100.4, LOW, MID, HIGH).context, Context.VOID)

    def test_void_at_low(self):
        self.assertIs(classify(99.0, LOW, MID, HIGH).context, Context.VOID)

    def test_void_below_low(self):
        self.assertIs(classify(98.6, LOW, MID, HIGH).context, Context.VOID)

    def test_one_tick_above_mid_is_premium(self):
        self.assertIs(classify(99.6, LOW, MID, HIGH).context, Context.PREMIUM)

    def test_one_tick_below_mid_is_discount(self):
        self.assertIs(classify(99.4, LOW, MID, HIGH).context, Context.DISCOUNT)

    def test_sub_epsilon_difference_still_counts_as_the_midpoint(self):
        # Mid_A is produced by division, so an open that is *meant* to equal it
        # can differ by ~1e-16. That must read VOID, not DISCOUNT.
        epsilon_noise_mid = 99.5 + 1e-12
        self.assertIs(
            classify(99.5, LOW, epsilon_noise_mid, HIGH).context, Context.VOID
        )

    def test_a_real_tick_difference_is_not_tolerated(self):
        # One tick away from Mid_A is genuinely the other half, not noise.
        self.assertIs(classify(99.4, LOW, MID, HIGH).context, Context.DISCOUNT)
        self.assertIs(classify(99.6, LOW, MID, HIGH).context, Context.PREMIUM)


class TestHalfTickMidpoint(unittest.TestCase):
    """Q6: Mid_A is arithmetic, not tick-aligned, and that is correct."""

    def test_odd_range_puts_mid_on_half_tick(self):
        # High 100.0 / Low 99.7 -> Range 0.3 (3 ticks) -> Mid 99.85
        low, high = 99.7, 100.0
        mid = (low + high) / 2
        self.assertAlmostEqual(mid, 99.85)
        # No tick-aligned open can equal 99.85, so the VOID-at-mid branch is
        # simply unreachable for this candle. It must not be "fixed".
        for price in (99.8, 99.9):
            self.assertIsNot(classify(price, low, mid, high).context, Context.VOID)

    def test_even_range_mid_is_reachable(self):
        low, high = 99.0, 100.0  # Range 1.0 = 10 ticks -> Mid 99.5
        self.assertIs(classify(99.5, low, 99.5, high).context, Context.VOID)


class TestInvalidInput(unittest.TestCase):
    def test_inconsistent_candle_raises(self):
        with self.assertRaises(ValueError):
            classify(99.5, 100.0, 99.5, 99.0)


if __name__ == "__main__":
    unittest.main()
