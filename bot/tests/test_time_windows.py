"""Tests for window definitions, trading-day logic and DST handling (PRD §6.1)."""

import datetime as dt
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.time_windows import (  # noqa: E402
    ALL_WINDOW_LABELS,
    EXCLUDED_WINDOW_HOURS,
    WINDOW_HOURS,
    Window,
    is_trading_day,
    localize,
    trading_days,
    window_label,
)


class TestWindowSet(unittest.TestCase):
    def test_exactly_21_windows(self):
        self.assertEqual(len(WINDOW_HOURS), 21)
        self.assertEqual(len(set(WINDOW_HOURS)), 21)
        self.assertEqual(len(ALL_WINDOW_LABELS), 21)

    def test_labels_match_prd(self):
        self.assertEqual(window_label(23), "23:45-00:15")
        self.assertEqual(window_label(0), "00:45-01:15")
        self.assertEqual(window_label(14), "14:45-15:15")
        self.assertEqual(window_label(18), "18:45-19:15")
        self.assertEqual(window_label(22), "22:45-23:15")

    def test_full_label_list_matches_prd_order(self):
        self.assertEqual(
            ALL_WINDOW_LABELS,
            (
                "23:45-00:15", "00:45-01:15", "01:45-02:15", "02:45-03:15",
                "03:45-04:15", "04:45-05:15", "05:45-06:15", "06:45-07:15",
                "07:45-08:15", "08:45-09:15", "09:45-10:15", "10:45-11:15",
                "11:45-12:15", "12:45-13:15", "13:45-14:15", "14:45-15:15",
                "18:45-19:15", "19:45-20:15", "20:45-21:15", "21:45-22:15",
                "22:45-23:15",
            ),
        )

    def test_maintenance_windows_excluded(self):
        # 15:45-16:15, 16:45-17:15, 17:45-18:15 must never be evaluated.
        for hour in EXCLUDED_WINDOW_HOURS:
            self.assertNotIn(hour, WINDOW_HOURS)
        self.assertEqual(set(EXCLUDED_WINDOW_HOURS), {15, 16, 17})
        self.assertNotIn("15:45-16:15", ALL_WINDOW_LABELS)
        self.assertNotIn("16:45-17:15", ALL_WINDOW_LABELS)
        self.assertNotIn("17:45-18:15", ALL_WINDOW_LABELS)


class TestWindowBoundaries(unittest.TestCase):
    def test_candle_a_and_b_split(self):
        w = Window(dt.date(2026, 3, 4), 9)
        self.assertEqual(w.start, dt.datetime(2026, 3, 4, 9, 45))
        self.assertEqual(w.candle_a_start, dt.datetime(2026, 3, 4, 9, 45))
        self.assertEqual(w.candle_b_start, dt.datetime(2026, 3, 4, 10, 0))
        self.assertEqual(w.end, dt.datetime(2026, 3, 4, 10, 15))
        self.assertEqual((w.end - w.start), dt.timedelta(minutes=30))

    def test_midnight_crossing_window(self):
        w = Window(dt.date(2026, 3, 4), 23)
        self.assertEqual(w.start, dt.datetime(2026, 3, 4, 23, 45))
        self.assertEqual(w.candle_b_start, dt.datetime(2026, 3, 5, 0, 0))
        self.assertEqual(w.end, dt.datetime(2026, 3, 5, 0, 15))
        # trading_date is the date Candle A STARTS (schema §7)
        self.assertEqual(w.trading_date, dt.date(2026, 3, 4))


class TestTradingDays(unittest.TestCase):
    def test_weekend_is_not_a_trading_day(self):
        self.assertFalse(is_trading_day(dt.date(2026, 3, 7)))  # Saturday
        self.assertFalse(is_trading_day(dt.date(2026, 3, 8)))  # Sunday
        self.assertTrue(is_trading_day(dt.date(2026, 3, 9)))   # Monday

    def test_trading_days_range(self):
        days = trading_days(dt.date(2026, 3, 6), dt.date(2026, 3, 9))  # Fri..Mon
        self.assertEqual(days, [dt.date(2026, 3, 6), dt.date(2026, 3, 9)])

    def test_sunday_session_never_evaluated(self):
        # 2026-03-08 is a Sunday that carries a full 18:00-24:00 session in the
        # data; it must still produce zero windows.
        self.assertEqual(trading_days(dt.date(2026, 3, 8), dt.date(2026, 3, 8)), [])


class TestDstLocalisation(unittest.TestCase):
    def test_spring_forward_gap_is_detected(self):
        # 2026-03-08 02:00 EST -> 03:00 EDT: 02:xx does not exist.
        _, exists = localize(dt.datetime(2026, 3, 8, 2, 45))
        self.assertFalse(exists)

    def test_normal_times_exist(self):
        for hour in (1, 3, 4):
            _, exists = localize(dt.datetime(2026, 3, 8, hour, 45))
            self.assertTrue(exists, f"{hour}:45 should exist on 2026-03-08")

    def test_offsets_either_side_of_transition(self):
        before, _ = localize(dt.datetime(2026, 3, 6, 9, 45))
        after, _ = localize(dt.datetime(2026, 3, 9, 9, 45))
        self.assertEqual(before.utcoffset(), dt.timedelta(hours=-5))  # EST
        self.assertEqual(after.utcoffset(), dt.timedelta(hours=-4))   # EDT

    def test_dst_transition_is_a_sunday(self):
        # Both US transitions land on Sunday, which is why Mon-Fri windows can
        # never fall inside the gap or the repeated hour.
        d = dt.date(2026, 3, 8)
        self.assertEqual(d.weekday(), 6)
        self.assertFalse(is_trading_day(d))


if __name__ == "__main__":
    unittest.main()
