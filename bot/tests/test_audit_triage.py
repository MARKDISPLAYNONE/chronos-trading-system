"""
Tests for `live/audit_triage.py`.

The tool exists because the runner's reasons cannot be trusted at face value, so
the tests are written against *rows*, not against the runner: each test states
what a particular audit row looks like and what the tool must conclude. That also
makes the tests a readable specification of the two defects.
"""

import datetime as dt
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live.audit_triage import (  # noqa: E402
    LIVE_LEGAL_REASONS,
    main,
    parse_period,
    rederive,
    self_check,
    show_row,
    triage_outside_range,
)

OUTAGE = (dt.datetime(2026, 10, 26, 14, 22), dt.datetime(2026, 10, 27, 1, 48))


def audit_row(day="2026-10-26", label="02:45-03:15", *, reason,
              context="VOID", flags="", bars_a=0, bars_b=0,
              high_a="", mid_a="", low_a="", open_b=""):
    return {
        "trading_date": day, "window_label": label, "context": context,
        "outcome": "NO_SETUP", "reason": reason, "flags": flags,
        "bars_candle_a": str(bars_a), "bars_candle_b": str(bars_b),
        "candle_a_high": high_a, "candle_a_mid": mid_a, "candle_a_low": low_a,
        "candle_b_open": open_b,
        "evaluated_at": f"{day}T03:15:20-04:00",
    }


class TestOutsideRangeTriage(unittest.TestCase):
    def test_a_short_tail_outside_every_outage_is_the_transient_bug(self):
        rows = [audit_row(reason="outside_data_range",
                          flags="incomplete_candle", bars_a=15, bars_b=14)]
        items, counts = triage_outside_range(rows, [OUTAGE])
        self.assertEqual(items[0]["shape"], "short_tail")
        self.assertEqual(items[0]["verdict"], "unexplained_transient_lag")
        self.assertEqual(counts["unexplained_transient_lag"], 1)

    def test_the_same_shape_inside_the_outage_is_connectivity(self):
        rows = [audit_row(label="14:45-15:15", reason="outside_data_range",
                          flags="incomplete_candle", bars_a=15, bars_b=14)]
        items, counts = triage_outside_range(rows, [OUTAGE])
        self.assertEqual(items[0]["verdict"], "connectivity")
        self.assertEqual(counts["connectivity"], 1)

    def test_an_empty_window_inside_the_outage_is_connectivity(self):
        rows = [audit_row(label="18:45-19:15", reason="outside_data_range",
                          flags="no_bars_in_window")]
        items, _ = triage_outside_range(rows, [OUTAGE])
        self.assertEqual(items[0]["shape"], "no_bars_in_window")
        self.assertEqual(items[0]["verdict"], "connectivity")

    def test_without_outage_data_the_tool_says_so_instead_of_guessing(self):
        rows = [audit_row(reason="outside_data_range",
                          flags="incomplete_candle", bars_b=14)]
        items, counts = triage_outside_range(rows, None)
        self.assertEqual(items[0]["verdict"], "unknown_no_outage_data")
        self.assertEqual(counts, {"unknown_no_outage_data": 1})

    def test_only_outside_range_rows_are_returned(self):
        rows = [
            audit_row(label="02:45-03:15", reason="outside_data_range",
                      flags="incomplete_candle", bars_b=14),
            audit_row(label="09:45-10:15", reason="no sweep", context="DISCOUNT"),
        ]
        items, _ = triage_outside_range(rows, [OUTAGE])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["window_label"], "02:45-03:15")

    def test_a_window_straddling_the_outage_start_overlaps(self):
        rows = [audit_row(label="14:45-15:15", reason="outside_data_range",
                          flags="no_bars_in_window")]
        items, _ = triage_outside_range(rows, [OUTAGE])
        self.assertEqual(items[0]["verdict"], "connectivity")

    def test_parse_period(self):
        lo, hi = parse_period("2026-10-26T14:22/2026-10-27T01:48")
        self.assertEqual(lo, dt.datetime(2026, 10, 26, 14, 22))
        self.assertEqual(hi, dt.datetime(2026, 10, 27, 1, 48))


class TestSelfConsistency(unittest.TestCase):
    """Task 4, generalised: a row must agree with its own candles."""

    def test_a_void_row_whose_open_is_inside_the_discount_zone_is_a_contradiction(self):
        # Low_A 25_380.0, Mid_A 25_388.65, High_A 25_397.3 -> discount zone.
        # open 25_380.4 is *inside* it (0.4 above Low_A), which is exactly the
        # case the Handler's manual read suggested for 04:45-05:15.
        row = audit_row(label="04:45-05:15", reason="invalid open location",
                        context="VOID", bars_a=15, bars_b=15,
                        high_a="25397.30000", mid_a="25388.65000",
                        low_a="25380.00000", open_b="25380.40000")
        problems = self_check([row])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["_problem"],
                         "invalid_open_claimed_but_open_is_valid")
        self.assertEqual(problems[0]["_derived"]["context"], "DISCOUNT")

    def test_a_genuine_void_row_is_accepted(self):
        row = audit_row(label="04:45-05:15", reason="invalid open location",
                        context="VOID", bars_a=15, bars_b=15,
                        high_a="25397.30000", mid_a="25388.65000",
                        low_a="25380.00000", open_b="25379.80000")
        self.assertEqual(self_check([row]), [])
        self.assertEqual(rederive(row)["context"], "VOID")

    def test_an_open_exactly_at_low_a_is_void_not_discount(self):
        """Strict inequality: equal-to-boundary is VOID, per the classifier."""
        row = audit_row(reason="invalid open location", context="VOID",
                        high_a="25397.30000", mid_a="25388.65000",
                        low_a="25380.00000", open_b="25380.00000")
        self.assertEqual(self_check([row]), [])

    def test_incomplete_rows_are_not_checked_for_classification(self):
        """A window flagged incomplete never reached the classifier."""
        row = audit_row(reason="outside_data_range", context="VOID",
                        flags="incomplete_candle", bars_b=14,
                        low_a="", mid_a="", high_a="", open_b="")
        self.assertEqual(self_check([row]), [])

    def test_a_valid_zone_row_is_accepted_on_its_numbers(self):
        row = audit_row(reason="no sweep", context="DISCOUNT",
                        high_a="25397.30000", mid_a="25388.65000",
                        low_a="25380.00000", open_b="25384.00000")
        self.assertEqual(self_check([row]), [])

    def test_rederive_returns_none_without_candles(self):
        self.assertIsNone(rederive(audit_row(reason="no sweep")))

    def test_show_row_reports_the_contradiction(self):
        row = audit_row(label="04:45-05:15", reason="invalid open location",
                        context="VOID", bars_a=15, bars_b=15,
                        high_a="25397.30000", mid_a="25388.65000",
                        low_a="25380.00000", open_b="25380.40000")
        text = show_row(row)
        self.assertIn("CONTRADICTION", text)
        self.assertIn("0.4", text)


class TestKnownBadFile(unittest.TestCase):
    """The reconciliation list must name real windows, or it reconciles nothing."""

    def test_every_listed_window_exists_in_the_schedule(self):
        import datetime as dt

        from src import time_windows

        path = pathlib.Path(__file__).resolve().parents[1] / "live" / "known_bad_windows.txt"
        entries = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line.split())

        self.assertEqual(len(entries), 8, "the outage covered eight windows")
        for day_s, label in entries:
            day = dt.date.fromisoformat(day_s)
            labels = [w.label for w in time_windows.windows_for_day(day)]
            self.assertIn(label, labels, f"{day} has no window {label}")

    def test_the_listed_windows_are_consecutive_through_the_outage(self):
        """14:45 Monday to 00:45 Tuesday: eight windows, no gaps in the sequence."""
        import datetime as dt

        from src import time_windows

        path = pathlib.Path(__file__).resolve().parents[1] / "live" / "known_bad_windows.txt"
        entries = [line.split() for line in path.read_text().splitlines()
                   if line.strip() and not line.startswith("#")]
        # From 14:45 on the 26th, every remaining window of that trading day
        # (the 17:00-18:00 break means there is no 15:45/16:45/17:45 slot), then
        # the 00:45 window which belongs to the 27th.
        day = dt.date(2026, 10, 26)
        expected = [w.label for w in sorted(time_windows.windows_for_day(day),
                                            key=lambda w: w.start_hour)
                    if w.start_hour >= 14]
        expected += [w.label for w in time_windows.windows_for_day(dt.date(2026, 10, 27))
                     if w.start_hour == 0]
        self.assertEqual([e[1] for e in entries], expected)


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rows, name="window_evaluations_audit.csv", columns=None):
        import csv
        cols = columns or list(rows[0].keys())
        with (self.out / name).open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def test_exit_code_is_nonzero_when_an_unexplained_row_exists(self):
        self._write([audit_row(reason="outside_data_range",
                               flags="incomplete_candle", bars_b=14)])
        rc = main(["--outside-range", "--outages",
                   "2026-10-26T14:22/2026-10-27T01:48", "--out-dir",
                   str(self.out)])
        self.assertEqual(rc, 1)

    def test_exit_code_is_zero_when_everything_is_explained(self):
        self._write([audit_row(label="14:45-15:15", reason="outside_data_range",
                               flags="no_bars_in_window")])
        rc = main(["--outside-range", "--outages",
                   "2026-10-26T14:22/2026-10-27T01:48", "--out-dir",
                   str(self.out)])
        self.assertEqual(rc, 0)

    def test_illegal_reason_sweep_flags_outside_data_range(self):
        rows = [audit_row(reason="outside_data_range", flags="incomplete_candle")]
        self.assertNotIn("outside_data_range", LIVE_LEGAL_REASONS)
        self._write(rows, "window_evaluations.csv", columns=list(rows[0].keys()))
        self._write(rows)
        rc = main(["--illegal-reasons", "--out-dir", str(self.out)])
        self.assertEqual(rc, 1)

    def test_annotate_appends_and_never_rewrites(self):
        self._write([audit_row(label="14:45-15:15", reason="outside_data_range",
                               flags="no_bars_in_window")])
        known = self.out / "known_bad.txt"
        known.write_text("# the 8 outage windows\n2026-10-26 14:45-15:15\n")
        rc = main(["--annotate", "--known-bad", str(known), "--out-dir",
                   str(self.out)])
        self.assertEqual(rc, 0)
        recon = self.out / "window_evaluations_reconciliation.csv"
        text = recon.read_text()
        self.assertIn("14:45-15:15", text)
        self.assertIn("dry_run_gap_runner_down", text)
        original = (self.out / "window_evaluations_audit.csv").read_text()
        self.assertIn("outside_data_range", original,
                      "the original row must be left exactly as it was")

        # Idempotent: running again adds nothing.
        before = len(text)
        main(["--annotate", "--known-bad", str(known), "--out-dir", str(self.out)])
        self.assertEqual(len(recon.read_text()), before)

    def test_missing_audit_file_is_an_error_not_a_crash(self):
        self.assertEqual(main(["--outside-range", "--out-dir", str(self.out)]), 2)


if __name__ == "__main__":
    unittest.main()
