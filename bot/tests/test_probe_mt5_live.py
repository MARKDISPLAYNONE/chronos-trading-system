"""
Tests for the Phase 2 live probe's pure helpers.

The probe itself needs Windows + a running MT5 terminal, so it cannot run in CI
or in the Agent's sandbox. What CAN be tested — and is tested here — is every
line that does not touch MT5: the −7h conversion, the broker-clock measurement,
forming-bar detection, and the session invariants.

That split is deliberate. If the live behaviour later disagrees with the CSV
assumptions, the disagreement will be in the *thin* MT5 layer, because the logic
underneath it is pinned by these tests.
"""

import pathlib
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live.probe_mt5_live import (  # noqa: E402
    decode_broker_label,
    forming_bar_index,
    measure_offset_hours,
    session_invariants,
    to_ny,
)
from src import config  # noqa: E402


def epoch(y, mo, d, h, mi) -> int:
    """A raw broker label expressed as an epoch (the way MT5 hands it over)."""
    import calendar
    return calendar.timegm(datetime(y, mo, d, h, mi).timetuple())


class TestConversion(unittest.TestCase):
    """The constant −7h rule (TRD §4.1.1) — the whole conversion."""

    def test_broker_label_is_decoded_verbatim(self):
        e = epoch(2026, 7, 15, 23, 30)
        self.assertEqual(decode_broker_label(e), datetime(2026, 7, 15, 23, 30))

    def test_minus_seven_hours_across_a_day_boundary(self):
        # Friday 16:59 NY is 23:59 on the broker clock.
        e = epoch(2026, 7, 17, 23, 59)
        self.assertEqual(to_ny(e), datetime(2026, 7, 17, 16, 59))

    def test_rule_is_constant_in_both_regimes(self):
        """No DST branch: a winter stamp and a summer stamp both just lose 7h."""
        winter = epoch(2026, 1, 14, 22, 0)   # broker UTC+2 -> NY EST
        summer = epoch(2026, 7, 14, 22, 0)   # broker UTC+3 -> NY EDT
        self.assertEqual(to_ny(winter), datetime(2026, 1, 14, 15, 0))
        self.assertEqual(to_ny(summer), datetime(2026, 7, 14, 15, 0))

    def test_offset_constant_is_the_one_config_declares(self):
        self.assertEqual(config.BROKER_OFFSET_HOURS, 7)


class TestMeasuredOffset(unittest.TestCase):
    """broker_stamp − true_utc_stamp is the offset the adapter must see."""

    def test_summer_broker_utc_plus_3(self):
        true_utc = 1_780_000_000.0
        self.assertAlmostEqual(measure_offset_hours(int(true_utc) + 3 * 3600, true_utc), 3.0)

    def test_winter_broker_utc_plus_2(self):
        true_utc = 1_780_000_000.0
        self.assertAlmostEqual(measure_offset_hours(int(true_utc) + 2 * 3600, true_utc), 2.0)

    def test_fall_back_is_observable_as_a_one_hour_step(self):
        """What the live DST monitor is looking for on Sun 1 Nov 2026.

        Before the switch the broker runs UTC+3; after it, UTC+2. The step is
        exactly −1h and happens while New York is still on EDT.
        """
        true_utc = 1_793_520_000.0
        before = measure_offset_hours(int(true_utc) + 3 * 3600, true_utc)
        after = measure_offset_hours(int(true_utc) + 2 * 3600, true_utc)
        self.assertAlmostEqual(before - after, 1.0)


class TestFormingBar(unittest.TestCase):
    """The newest live bar is unfinished; the CSV never had one."""

    def test_bar_is_forming_until_its_minute_closes(self):
        t = epoch(2026, 10, 26, 14, 30)
        self.assertEqual(forming_bar_index([t - 60, t], t + 59), 1)
        self.assertIsNone(forming_bar_index([t - 60, t], t + 60))

    def test_empty_is_not_forming(self):
        self.assertIsNone(forming_bar_index([], 0))


class TestSessionInvariants(unittest.TestCase):
    """The four invariants Phase 1 gates on, applied to live bars."""

    def _ny(self, *specs):
        return [datetime(*s) for s in specs]

    def test_clean_week_passes(self):
        # Sun open 18:00, Mon-Thu with a 16:59 -> 18:00 break, Fri close 16:59.
        times = self._ny((2026, 10, 25, 18, 0), (2026, 10, 26, 16, 59), (2026, 10, 26, 18, 0),
                         (2026, 10, 29, 16, 59), (2026, 10, 29, 18, 0), (2026, 10, 30, 16, 59))
        rows = dict((name, ok) for name, ok, _ in session_invariants(times))
        self.assertTrue(all(rows.values()), rows)

    def test_bar_inside_the_break_fails(self):
        times = self._ny((2026, 10, 26, 17, 30))
        rows = dict((name, ok) for name, ok, _ in session_invariants(times))
        self.assertFalse([ok for name, ok in rows.items() if "break" in name][0])

    def test_friday_bar_after_close_fails(self):
        times = self._ny((2026, 10, 30, 17, 5))
        rows = dict((name, ok) for name, ok, _ in session_invariants(times))
        self.assertFalse([ok for name, ok in rows.items() if "Friday" in name][0])

    def test_sunday_bar_before_open_fails(self):
        times = self._ny((2026, 11, 1, 17, 30))
        rows = dict((name, ok) for name, ok, _ in session_invariants(times))
        self.assertFalse([ok for name, ok in rows.items() if "Sunday" in name][0])

    def test_one_hour_shift_is_caught(self):
        """If the −7h rule were applied to an already-correct NY stamp (i.e. a
        double conversion), the break, the Friday close and the Sunday open all
        land an hour early and at least one invariant must fail."""
        times = self._ny((2026, 10, 25, 17, 0), (2026, 10, 26, 15, 59), (2026, 10, 26, 17, 0),
                         (2026, 10, 30, 15, 59))
        rows = session_invariants(times)
        self.assertFalse(all(ok for _, ok, _ in rows))


class TestNoOrderSurface(unittest.TestCase):
    """Structural guarantee: the probe must not be able to trade.

    This is the cheap, static half. The Phase 2 runner gets the same treatment
    over its whole import graph (see test_no_order_path.py, added with it).
    """

    FORBIDDEN = ("order_send", "order_check", "OrderSend", "positions_close")

    def test_probe_source_never_references_an_order_call(self):
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "live" / "probe_mt5_live.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        # Strip the docstring region: it *names* the forbidden calls to say it
        # does not use them, which is the one place mentioning them is correct.
        body = code.split('"""', 2)[-1]
        for name in self.FORBIDDEN:
            self.assertNotIn(name, body, f"probe must not reference {name}")


if __name__ == "__main__":
    unittest.main()
