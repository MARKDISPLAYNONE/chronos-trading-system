"""Tests for sweep + reversal detection (PRD §6.5, schema §8.1 rule 1 / Q5)."""

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.bars import M1Bar  # noqa: E402
from src.sweep_detector import (  # noqa: E402
    LONG,
    REASON_AMBIGUOUS_SAME_BAR,
    REASON_NO_SWEEP,
    REASON_SWEEP_NOT_REVERSED,
    SHORT,
    detect,
)

T0 = dt.datetime(2026, 3, 4, 10, 0)
TICK = 0.1
HIGH_A = 100.0
LOW_A = 99.0


def bars(*specs):
    """(open, high, low, close) tuples -> consecutive M1 bars from T0."""
    return [
        M1Bar(T0 + dt.timedelta(minutes=i), o, h, l, c, 100)
        for i, (o, h, l, c) in enumerate(specs)
    ]


def flat(n=4, price=99.8):
    return [(price, price + 0.05, price - 0.05, price)] * n


class TestShortSweep(unittest.TestCase):
    def test_no_sweep(self):
        r = detect(SHORT, bars(*flat(10)), HIGH_A, LOW_A, TICK)
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, REASON_NO_SWEEP)

    def test_sweep_without_reversal(self):
        # sweep bar must NOT also touch the reversal level, otherwise it is
        # the same-bar ambiguity case rather than a plain "no reversal"
        b = bars((99.8, 99.9, 99.7, 99.85), (99.98, 100.6, 99.95, 100.5), *flat(6, 100.4))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, REASON_SWEEP_NOT_REVERSED)
        self.assertIsNotNone(r.sweep_time)

    def test_sweep_then_reversal_on_later_bar(self):
        b = bars((99.8, 99.9, 99.7, 99.85), (99.9, 100.4, 99.9, 100.3), (100.3, 100.35, 99.88, 99.9))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)
        self.assertEqual(r.sweep_time, T0 + dt.timedelta(minutes=1))
        self.assertEqual(r.reversal_time, T0 + dt.timedelta(minutes=2))

    def test_sweep_exactly_one_tick_counts(self):
        # high exactly High_A + 1 tick is a sweep (boundary condition)
        b = bars((99.8, 99.9, 99.7, 99.85), (99.9, 100.1, 99.9, 100.05), (100.05, 100.06, 99.9, 99.9))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)

    def test_reversal_exactly_one_tick_counts(self):
        b = bars((99.8, 99.9, 99.7, 99.85), (99.9, 100.4, 99.9, 100.3), (100.3, 100.35, 99.9, 99.95))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)  # low == High_A - 1 tick exactly

    def test_opposite_direction_first_does_not_invalidate(self):
        # price dives to 98 first (opposite level) -> ignored, then sweeps+reverses
        b = bars(
            (99.8, 99.85, 98.0, 98.6),     # opposite-direction excursion
            (98.6, 98.7, 98.4, 98.5),
            (98.5, 100.3, 98.5, 100.2),    # sweep
            (100.2, 100.25, 99.85, 99.9),  # reversal
        )
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)

    def test_resweep_does_not_reset_state(self):
        b = bars(
            (99.8, 100.3, 99.8, 100.2),    # sweep 1
            (100.2, 100.7, 100.15, 100.6), # sweep again / higher
            (100.6, 100.65, 99.85, 99.9),  # reversal
        )
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)
        self.assertEqual(r.sweep_time, T0)  # first sweep is the recorded one


class TestSameBarRule(unittest.TestCase):
    """Q5: a single bar may only confirm both events if its close proves it."""

    def test_same_bar_and_close_confirms_is_valid(self):
        # straddles both thresholds AND closes below High_A - 1t
        b = bars((99.8, 100.4, 99.7, 99.85))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)
        self.assertEqual(r.sweep_time, r.reversal_time)

    def test_same_bar_without_close_confirmation_is_ambiguous(self):
        # straddles both thresholds but closes back above the reversal level
        # later bars stay strictly above the reversal level (99.9)
        b = bars((99.8, 100.4, 99.7, 99.95), *flat(5, 99.98))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, REASON_AMBIGUOUS_SAME_BAR)
        self.assertTrue(r.ambiguous_encountered)

    def test_ambiguous_bar_resolved_by_a_later_reversal(self):
        b = bars(
            (99.8, 100.4, 99.7, 99.95),    # ambiguous: no close confirmation
            (99.95, 100.0, 99.94, 99.98),  # still above reversal level
            (99.98, 99.99, 99.85, 99.88),  # clean reversal -> ordering proven
        )
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)
        self.assertEqual(r.reversal_time, T0 + dt.timedelta(minutes=2))
        self.assertEqual(r.sweep_time, T0)

    def test_never_resolves_ambiguity_in_the_bots_favour(self):
        # swap the bar order: if the sweep had come last there would be no
        # reversal after it, and the window must not be treated as a setup.
        b = bars((99.8, 99.95, 99.7, 99.9), (99.9, 100.4, 99.7, 99.95))
        r = detect(SHORT, b, HIGH_A, LOW_A, TICK)
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, REASON_AMBIGUOUS_SAME_BAR)


class TestLongSweep(unittest.TestCase):
    def test_long_mirror_sweep_and_reversal(self):
        b = bars(
            (99.2, 99.3, 99.1, 99.15),
            (99.15, 99.2, 98.7, 98.8),    # sweep below Low_A - 1t = 98.9
            (98.8, 99.15, 98.75, 99.1),   # reversal above Low_A + 1t = 99.1
        )
        r = detect(LONG, b, HIGH_A, LOW_A, TICK)
        self.assertTrue(r.triggered)
        self.assertEqual(r.sweep_time, T0 + dt.timedelta(minutes=1))
        self.assertEqual(r.reversal_time, T0 + dt.timedelta(minutes=2))

    def test_long_no_sweep(self):
        r = detect(LONG, bars(*flat(6, 99.4)), HIGH_A, LOW_A, TICK)
        self.assertFalse(r.triggered)
        self.assertEqual(r.reason, REASON_NO_SWEEP)


if __name__ == "__main__":
    unittest.main()
