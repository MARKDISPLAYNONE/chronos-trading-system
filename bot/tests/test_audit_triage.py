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
    monitor_gaps,
    parse_period,
    rederive,
    self_check,
    show_row,
    stall_intervals,
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
        self.assertEqual(items[0]["verdict"], "unknown_no_evidence")
        self.assertEqual(counts, {"unknown_no_evidence": 1})

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


class TestAllowList(unittest.TestCase):
    """The 43-of-46 false positive: the list omitted every WIN/LOSS reason."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_trade_exit_reason_is_legal(self):
        from src import trade_simulator

        for reason in (trade_simulator.REASON_TP, trade_simulator.REASON_SL,
                       trade_simulator.REASON_BREAKEVEN_AFTER_PARTIAL,
                       trade_simulator.REASON_TP_AFTER_PARTIAL,
                       trade_simulator.REASON_UNRESOLVED):
            self.assertIn(reason, LIVE_LEGAL_REASONS)

    def test_the_allow_list_is_derived_not_hand_listed(self):
        """A hand-written copy drifts; this one is built from the modules."""
        from src import sweep_detector, trade_simulator

        self.assertIn(trade_simulator.REASON_SL, LIVE_LEGAL_REASONS)
        self.assertIn(sweep_detector.REASON_NO_SWEEP, LIVE_LEGAL_REASONS)

    def test_outside_data_range_is_still_illegal(self):
        self.assertNotIn("outside_data_range", LIVE_LEGAL_REASONS)

    def test_a_clean_file_reports_no_illegal_reasons(self):
        rows = [audit_row(reason="stop loss hit", context="DISCOUNT"),
                audit_row(reason="take profit hit", context="PREMIUM"),
                audit_row(reason="breakeven stop hit after partial", context="PREMIUM"),
                audit_row(reason="target hit after partial", context="DISCOUNT"),
                audit_row(reason="no sweep", context="PREMIUM"),
                audit_row(reason="invalid open location")]
        self._write_all(rows)
        self.assertEqual(main(["--illegal-reasons", "--out-dir", str(self.out)]), 0)

    def _write_all(self, rows, columns=None):
        import csv
        cols = columns or list(rows[0].keys())
        for name in ("window_evaluations_audit.csv", "window_evaluations.csv"):
            with (self.out / name).open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow(r)

    def test_it_falls_back_to_the_audit_csv_and_says_so(self):
        """A missing window_evaluations.csv used to make the check pass silently."""
        rows = [audit_row(reason="outside_data_range", flags="no_bars_in_window")]
        import csv
        with (self.out / "window_evaluations_audit.csv").open(
                "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()),
                               extrasaction="ignore")
            w.writeheader()
            w.writerow(rows[0])
        self.assertEqual(main(["--illegal-reasons", "--out-dir", str(self.out)]), 1)


class TestMonitorEvidence(unittest.TestCase):
    """Classification by measured staleness, not by a pre-supplied outage list."""

    def monitor(self, pairs):
        return [(dt.datetime.fromisoformat(a), dt.datetime.fromisoformat(b))
                for a, b in pairs]

    def test_a_healthy_feed_makes_the_short_tail_case(self):
        mon = self.monitor([("2026-10-26T10:15:30", "2026-10-26T10:15:00")])
        rows = [audit_row(label="09:45-10:15", reason="outside_data_range",
                          flags="incomplete_candle", bars_a=15, bars_b=14)]
        items, counts = triage_outside_range(rows, None, mon)
        self.assertEqual(items[0]["verdict"], "transient_tail")
        self.assertEqual(items[0]["confidence"], "measured")
        self.assertEqual(counts, {"transient_tail": 1})

    def test_a_frozen_feed_makes_it_a_stall(self):
        mon = self.monitor([("2026-10-27T07:15:30", "2026-10-27T06:37:00")])
        rows = [audit_row(day="2026-10-27", label="06:45-07:15",
                          reason="outside_data_range",
                          flags="no_bars_in_window")]
        items, _ = triage_outside_range(rows, None, mon)
        self.assertEqual(items[0]["verdict"], "feed_stalled")
        self.assertAlmostEqual(items[0]["staleness_minutes"], 38.0)

    def test_a_freeze_is_proven_across_the_monitors_own_downtime(self):
        """No sample during the window, but frozen before and after it."""
        mon = self.monitor([("2026-10-27T20:20:00", "2026-10-27T13:13:00"),
                            ("2026-10-28T01:44:00", "2026-10-27T13:13:00")])
        rows = [audit_row(day="2026-10-27", label="21:45-22:15",
                          reason="outside_data_range",
                          flags="no_bars_in_window")]
        items, _ = triage_outside_range(rows, None, mon)
        self.assertEqual(items[0]["verdict"], "feed_stalled")
        self.assertEqual(items[0]["confidence"], "bracketed")

    def test_outage_range_only_still_works_without_a_monitor(self):
        rows = [audit_row(label="14:45-15:15", reason="outside_data_range",
                          flags="no_bars_in_window")]
        items, _ = triage_outside_range(rows, [OUTAGE], None)
        self.assertEqual(items[0]["verdict"], "connectivity")

    def test_stall_intervals_finds_each_freeze(self):
        mon = self.monitor([
            ("2026-10-27T06:00:00", "2026-10-27T05:59:00"),   # healthy
            ("2026-10-27T06:38:00", "2026-10-27T06:37:00"),   # freeze begins
            ("2026-10-27T07:00:00", "2026-10-27T06:37:00"),
            ("2026-10-27T09:35:00", "2026-10-27T06:37:00"),   # last frozen look
            ("2026-10-27T09:36:00", "2026-10-27T09:36:00"),   # recovered
        ])
        spans = stall_intervals(mon)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["stalled_bar_ny"],
                         dt.datetime(2026, 10, 27, 6, 37))
        self.assertAlmostEqual(spans[0]["minutes"], 177.0)

    def test_monitor_gaps_are_reported_separately(self):
        mon = self.monitor([("2026-10-27T20:20:00", "2026-10-27T13:13:00"),
                            ("2026-10-28T01:44:00", "2026-10-27T13:13:00")])
        gaps = monitor_gaps(mon)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual((gaps[0][1] - gaps[0][0]).total_seconds() / 60, 324.0)


class TestKnownBadFile(unittest.TestCase):
    """The reconciliation list must name real windows, or it reconciles nothing."""

    def entries(self):
        path = pathlib.Path(__file__).resolve().parents[1] / "live" / "known_bad_windows.txt"
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.split())
        return out

    def test_every_listed_window_exists_in_the_schedule(self):
        from src import time_windows

        for day_s, label, _category in self.entries():
            day = dt.date.fromisoformat(day_s)
            labels = [w.label for w in time_windows.windows_for_day(day)]
            self.assertIn(label, labels, f"{day} has no window {label}")

    def test_the_list_covers_all_29_outside_data_range_rows(self):
        entries = self.entries()
        self.assertEqual(len(entries), 29)
        self.assertEqual(sum(1 for e in entries if e[2] == "stall"), 22)
        self.assertEqual(sum(1 for e in entries if e[2] == "tail"), 7)

    def test_no_window_is_listed_twice(self):
        keys = [(e[0], e[1]) for e in self.entries()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_eight_windows_of_the_first_stall_are_consecutive(self):
        """14:45 Monday to 00:45 Tuesday: no 15:45/16:45/17:45 slot exists."""
        from src import time_windows

        stall1 = [(d, l) for d, l, c in self.entries()
                  if c == "stall" and (d, l) in {
                      ("2026-10-26", "14:45-15:15"), ("2026-10-26", "18:45-19:15"),
                      ("2026-10-26", "19:45-20:15"), ("2026-10-26", "20:45-21:15"),
                      ("2026-10-26", "21:45-22:15"), ("2026-10-26", "22:45-23:15"),
                      ("2026-10-26", "23:45-00:15"), ("2026-10-27", "00:45-01:15")}]
        self.assertEqual(len(stall1), 8)
        expected = [w.label for w in sorted(time_windows.windows_for_day(
            dt.date(2026, 10, 26)), key=lambda w: w.start_hour) if w.start_hour >= 14]
        expected += [w.label for w in time_windows.windows_for_day(dt.date(2026, 10, 27))
                     if w.start_hour == 0]
        self.assertEqual([l for _d, l in stall1], expected)


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
        known.write_text("# a stall and a late tail\n"
                         "2026-10-26 14:45-15:15 stall\n"
                         "2026-10-26 00:45-01:15 tail\n")
        rc = main(["--annotate", "--known-bad", str(known), "--out-dir",
                   str(self.out)])
        self.assertEqual(rc, 0)
        recon = self.out / "window_evaluations_reconciliation.csv"
        text = recon.read_text()
        self.assertIn("14:45-15:15", text)
        # A stall is NOT runner downtime: we were watching.
        self.assertIn("live_feed_stalled", text)
        self.assertNotIn("dry_run_gap_runner_down", text)
        # A late tail was never correctly evaluated, so no invented outcome.
        self.assertIn("transient_lag_not_evaluated", text)
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
