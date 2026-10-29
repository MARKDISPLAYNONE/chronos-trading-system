"""
Tests for `live/audit_triage.py`.

The tool exists because the runner's reasons cannot be trusted at face value, so
the tests are written against *rows*, not against the runner: each test states
what a particular audit row looks like and what the tool must conclude. That also
makes the tests a readable specification of the two defects.
"""

import contextlib
import csv
import datetime as dt
import io
import json
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live.audit_triage import (  # noqa: E402
    ABSENT_AWAITING_DATA,
    ABSENT_AWAITING_TRADE,
    ABSENT_MISSING,
    ABSENT_NOT_DUE,
    CERTIFIABLE_OUTCOMES,
    CERTIFY_FAIL,
    CERTIFY_INCOMPLETE,
    CERTIFY_NOT_APPLICABLE,
    CERTIFY_PASS,
    LIVE_LEGAL_REASONS,
    certify,
    certify_digest,
    load_certify_state,
    main,
    monitor_gaps,
    parse_period,
    rederive,
    render_digest,
    self_check,
    show_row,
    stall_intervals,
    triage_outside_range,
)

OUTAGE = (dt.datetime(2026, 10, 26, 14, 22), dt.datetime(2026, 10, 27, 1, 48))


def time_windows_listing(day):
    """The scheduled windows of `day`, from the module under test's own source."""
    from src import time_windows

    return sorted(time_windows.windows_for_day(day), key=lambda w: w.end)


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


def ny_ts(day, hour=0, minute=0):
    """A NY wall-clock instant as a POSIX timestamp."""
    from zoneinfo import ZoneInfo

    from src import config

    return dt.datetime(day.year, day.month, day.day, hour, minute,
                       tzinfo=ZoneInfo(config.NY_TZ)).timestamp()


class TestCertify(unittest.TestCase):
    """The four-part clean-day rule, as a function rather than a judgement.

    Every test pins `now`. Leaving it to the real clock is what made the first
    version unusable: certifying the current date at 09:30 reported twelve
    "missing" windows that had simply not happened yet.
    """

    DAY = dt.date(2026, 10, 28)

    def after_close(self, day=None):
        """A moment when every window of the date has closed (final 00:15 next day)."""
        day = day or self.DAY
        return ny_ts(day + dt.timedelta(days=1), 1, 0)

    def mid_morning(self):
        """09:30 NY: only the windows closing by 09:15 exist."""
        return ny_ts(self.DAY, 9, 30)

    def late_afternoon(self):
        """17:25 NY - the moment the live report of 2026-10-29 was produced.

        Six scheduled windows are still in the future at this point, and they
        are consecutive, which is what the live report was blind to.
        """
        return ny_ts(self.DAY, 17, 25)

    def rows(self, **overrides):
        from src import time_windows

        out = []
        for window in time_windows.windows_for_day(self.DAY):
            row = {
                "trading_date": self.DAY.isoformat(),
                "window_label": window.label,
                "outcome": "NO_SETUP",
                "reason": "no sweep",
            }
            row.update(overrides.get(window.label, {}))
            out.append(row)
        return out

    def labels_closed_by(self, when):
        """Scheduled windows whose close is at or before `when`, in NY terms."""
        from zoneinfo import ZoneInfo

        from src import config
        from src import time_windows

        cutoff = (dt.datetime.fromtimestamp(when, ZoneInfo(config.NY_TZ))
                  .replace(tzinfo=None))
        return [w.label for w in time_windows.windows_for_day(self.DAY)
                if w.end <= cutoff]

    # --- the happy path ---------------------------------------------------
    # --- the 2026-10-29 live report: consecutive future windows -----------
    def test_six_or_more_future_windows_are_never_missing(self):
        """Mid-afternoon, six scheduled windows remain. None is a defect.

        This is the exact shape of the 2026-10-29 report: 15 rows in, six
        windows still ahead, and the verdict claimed every one of them was
        missing from coverage.
        """
        closed = self.labels_closed_by(self.late_afternoon())
        rows = [r for r in self.rows() if r["window_label"] in closed]
        self.assertEqual(len(rows), 15)

        result = certify(self.DAY, rows, now=self.late_afternoon())

        future = [w.label for w in time_windows_listing(self.DAY)
                  if w.label not in closed]
        self.assertGreaterEqual(len(future), 6)
        self.assertEqual(result["status"], CERTIFY_INCOMPLETE, result["failures"])
        # And specifically: no coverage failure at all, not just a non-failing one.
        self.assertEqual([n for n, _d in result["failures"] if n == "coverage"], [])
        kinds = {p["label"]: p["kind"] for p in result["pending"]}
        for label in future:
            self.assertEqual(kinds[label], ABSENT_NOT_DUE,
                             f"{label} is in the future at 17:25 NY")
        self.assertNotIn(ABSENT_MISSING, kinds.values())

    def test_the_future_windows_are_not_named_in_any_failure(self):
        """A failure that *does* exist must not be mixed up with future windows."""
        closed = self.labels_closed_by(self.late_afternoon())
        # One real defect (a stall on a closed window) plus six future windows.
        rows = [dict(r, reason="live_feed_stalled") if r["window_label"] == "09:45-10:15"
                else dict(r) for r in self.rows() if r["window_label"] in closed]
        result = certify(self.DAY, rows, now=self.late_afternoon())

        self.assertEqual(result["status"], CERTIFY_FAIL)     # the stall is real
        text = " ".join(d for _n, d in result["failures"])
        for window in time_windows_listing(self.DAY):
            if window.label in closed:
                continue
            self.assertNotIn(window.label, text)

    def test_a_late_afternoon_day_does_not_count_in_the_digest_streak(self):
        """INCOMPLETE is not a clean day: the streak must not advance on it."""
        closed = self.labels_closed_by(self.late_afternoon())
        rows = [r for r in self.rows() if r["window_label"] in closed]
        results = certify_digest(rows, days=1, now=self.late_afternoon(),
                                 today=self.DAY)
        self.assertEqual([r["status"] for r in results], [CERTIFY_INCOMPLETE])
        rendered = render_digest(results, days=1)
        self.assertIn("consecutive PASS streak: 0", rendered)

    def test_a_clean_day_passes(self):
        result = certify(self.DAY, self.rows(), now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_PASS, result["failures"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["counts"]["rows"], 21)

    def test_rows_for_other_dates_are_ignored(self):
        rows = self.rows()
        rows.append({"trading_date": "2026-10-27", "window_label": "00:45-01:15",
                     "outcome": "WIN", "reason": "take profit hit"})
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_PASS)

    # --- the case that prompted this: a day still in progress -------------
    def test_a_day_still_in_progress_is_incomplete_not_a_failure(self):
        closed = self.labels_closed_by(self.mid_morning())
        rows = [r for r in self.rows() if r["window_label"] in closed]
        result = certify(self.DAY, rows, now=self.mid_morning())
        self.assertEqual(result["status"], CERTIFY_INCOMPLETE)
        self.assertEqual(result["failures"], [])
        self.assertFalse(result["passed"])
        kinds = {p["kind"] for p in result["pending"]}
        self.assertEqual(kinds, {ABSENT_NOT_DUE})
        self.assertEqual(result["counts"]["rows"], len(closed))

    def test_the_first_unclosed_window_is_the_live_reported_one(self):
        """09:45-10:15 was flagged as 'missing' at 09:30 NY on 28 Oct."""
        closed = self.labels_closed_by(self.mid_morning())
        rows = [r for r in self.rows() if r["window_label"] in closed]
        result = certify(self.DAY, rows, now=self.mid_morning())
        not_due = {p["label"] for p in result["pending"] if p["kind"] == ABSENT_NOT_DUE}
        self.assertIn("09:45-10:15", not_due)
        self.assertNotIn("09:45-10:15", {p["label"] for p in result["pending"]
                                         if p["kind"] == ABSENT_MISSING})

    def test_the_same_rows_become_a_failure_once_the_day_is_over(self):
        """Determinism: only the clock differs between these two verdicts."""
        closed = self.labels_closed_by(self.mid_morning())
        rows = [r for r in self.rows() if r["window_label"] in closed]
        self.assertEqual(certify(self.DAY, rows, now=self.mid_morning())["status"],
                         CERTIFY_INCOMPLETE)
        later = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(later["status"], CERTIFY_FAIL)
        self.assertTrue(any(c == "coverage" for c, _ in later["failures"]))

    def test_a_just_closed_window_gets_a_grace_period(self):
        """A row lands on the first poll after the close, not at the close."""
        close = ny_ts(self.DAY, 10, 15)          # 09:45-10:15 closes here
        closed = self.labels_closed_by(close + 30)
        # Everything closed is present, EXCEPT the window that closed 30 s ago:
        # its row cannot have been written yet.
        rows = [r for r in self.rows()
                if r["window_label"] in closed and r["window_label"] != "09:45-10:15"]
        result = certify(self.DAY, rows, now=close + 30)
        self.assertNotIn("09:45-10:15",
                         {p["label"] for p in result["pending"]
                          if p["kind"] == ABSENT_MISSING})
        self.assertIn("09:45-10:15",
                      {p["label"] for p in result["pending"]
                       if p["kind"] == ABSENT_NOT_DUE})

    def test_a_date_that_has_not_begun_is_incomplete(self):
        """Certifying a future date from today: nothing has happened, no failure."""
        future = dt.date(2026, 12, 1)
        result = certify(future, [], now=ny_ts(self.DAY, 9, 30))
        self.assertEqual(result["status"], CERTIFY_INCOMPLETE, result["failures"])
        self.assertEqual(result["counts"]["rows"], 0)
        self.assertEqual({p["kind"] for p in result["pending"]}, {ABSENT_NOT_DUE})

    # --- the three legitimate reasons a closed window has no row ----------
    def test_a_closed_window_with_an_open_trade_is_waiting_not_missing(self):
        rows = [r for r in self.rows() if r["window_label"] != "13:45-14:15"]
        result = certify(self.DAY, rows, now=self.after_close(),
                         pending={"2026-10-28|13:45-14:15"})
        self.assertEqual(result["status"], CERTIFY_INCOMPLETE, result["failures"])
        kinds = {(p["label"], p["kind"]) for p in result["pending"]}
        self.assertEqual(kinds, {("13:45-14:15", ABSENT_AWAITING_TRADE)})

    def test_a_deferred_window_is_waiting_not_missing(self):
        rows = [r for r in self.rows() if r["window_label"] != "13:45-14:15"]
        result = certify(self.DAY, rows, now=self.after_close(),
                         deferred={"2026-10-28|13:45-14:15"})
        self.assertEqual(result["status"], CERTIFY_INCOMPLETE)
        self.assertIn(ABSENT_AWAITING_DATA,
                      {p["kind"] for p in result["pending"]})

    def test_without_state_an_absent_closed_window_is_missing(self):
        """Conservative: no evidence of an open trade means no excuse."""
        rows = [r for r in self.rows() if r["window_label"] != "13:45-14:15"]
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        self.assertIn(ABSENT_MISSING, {p["kind"] for p in result["pending"]})

    def test_a_pending_key_for_a_different_window_does_not_excuse_anything(self):
        rows = [r for r in self.rows() if r["window_label"] != "13:45-14:15"]
        result = certify(self.DAY, rows, now=self.after_close(),
                         pending={"2026-10-28|09:45-10:15"})
        self.assertEqual(result["status"], CERTIFY_FAIL)

    # --- real failures are still failures, mid-day or not -----------------
    def test_a_genuine_gap_mid_day_is_reported_as_a_gap_not_as_coverage(self):
        """The live 28 Oct shape: restart gaps plus windows not yet closed."""
        closed = self.labels_closed_by(self.mid_morning())
        gap_labels = {"04:45-05:15", "05:45-06:15", "06:45-07:15", "07:45-08:15"}
        rows = [dict(r, reason="dry_run_gap_runner_down")
                if r["window_label"] in gap_labels else dict(r)
                for r in self.rows() if r["window_label"] in closed]
        result = certify(self.DAY, rows, now=self.mid_morning())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        conditions = [c for c, _ in result["failures"]]
        self.assertEqual(conditions, ["gaps"],
                         "mid-day absences must not be blamed on coverage")

    def test_missing_window_names_it(self):
        rows = [r for r in self.rows() if r["window_label"] != "09:45-10:15"]
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        coverage = [d for c, d in result["failures"] if c == "coverage"]
        self.assertTrue(any("09:45-10:15" in d for d in coverage), coverage)

    def test_a_duplicate_window_fails_coverage(self):
        rows = self.rows()
        rows.append(dict(rows[0]))
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        self.assertTrue(any("duplicated" in d for c, d in result["failures"]
                            if c == "coverage"))

    def test_an_undecided_outcome_fails(self):
        rows = self.rows(**{"13:45-14:15": {"outcome": "MISSING_EVALUATION",
                                            "reason": "data_ends_before_trade_closed"}})
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        self.assertIn("MISSING_EVALUATION",
                      " ".join(d for c, d in result["failures"] if c == "outcome"))

    def test_a_blank_reason_fails(self):
        rows = self.rows(**{"13:45-14:15": {"outcome": "WIN", "reason": ""}})
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        self.assertTrue(any("empty reason" in d for c, d in result["failures"]))

    def test_range_and_stall_rows_fail(self):
        for reason in ("outside_data_range", "live_feed_stalled"):
            rows = self.rows(**{"04:45-05:15": {"reason": reason}})
            result = certify(self.DAY, rows, now=self.after_close())
            self.assertEqual(result["status"], CERTIFY_FAIL, reason)
            self.assertTrue(any(reason in d for c, d in result["failures"]))

    def test_gap_rows_fail(self):
        rows = self.rows(**{"04:45-05:15": {"reason": "dry_run_gap_runner_down"}})
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_FAIL)
        self.assertTrue(any(c == "gaps" for c, _ in result["failures"]))

    def test_every_failing_condition_is_reported_together(self):
        rows = self.rows(**{
            "04:45-05:15": {"reason": "outside_data_range"},
            "13:45-14:15": {"reason": "dry_run_gap_runner_down"},
        })
        rows = [r for r in rows if r["window_label"] != "09:45-10:15"]
        result = certify(self.DAY, rows, now=self.after_close())
        self.assertEqual({c for c, _ in result["failures"]},
                         {"coverage", "range", "gaps"})

    def test_a_weekend_is_not_applicable(self):
        result = certify(dt.date(2026, 11, 1), [], now=self.after_close())
        self.assertEqual(result["status"], CERTIFY_NOT_APPLICABLE)
        self.assertIn("Sunday", result["detail"])

    def test_missing_evaluation_is_not_certifiable(self):
        self.assertNotIn("MISSING_EVALUATION", CERTIFIABLE_OUTCOMES)

    def test_the_completion_time_is_the_next_morning(self):
        """The date's last window closes 00:15 the following NY day."""
        result = certify(self.DAY, self.rows(), now=self.after_close())
        self.assertEqual(result["complete_after_ny"],
                         dt.datetime(2026, 10, 29, 0, 15))


class TestCertifyStateFile(unittest.TestCase):
    def test_a_missing_state_file_yields_empty_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending, deferred = load_certify_state(pathlib.Path(tmp) / "nope.json")
        self.assertEqual((pending, deferred), (set(), set()))

    def test_an_unreadable_state_file_yields_empty_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "runner_state.json"
            path.write_text("{not json")
            pending, deferred = load_certify_state(path)
        self.assertEqual((pending, deferred), (set(), set()))

    def test_keys_are_read_from_both_ledgers(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "runner_state.json"
            path.write_text(json.dumps({
                "pending": {"2026-10-28|13:45-14:15": {"direction": "PREMIUM"}},
                "deferred": {"2026-10-28|09:45-10:15": {}},
            }))
            pending, deferred = load_certify_state(path)
        self.assertEqual(pending, {"2026-10-28|13:45-14:15"})
        self.assertEqual(deferred, {"2026-10-28|09:45-10:15"})


class TestCertifyCli(unittest.TestCase):
    DAY = dt.date(2026, 10, 28)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rows, *, state=None):
        import csv

        cols = ["trading_date", "window_label", "outcome", "reason"]
        if rows:
            cols = list(rows[0].keys())
        for name in ("window_evaluations.csv", "window_evaluations_audit.csv"):
            with (self.out / name).open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in cols})
        if state is not None:
            import json
            (self.out / "runner_state.json").write_text(json.dumps(state))

    def clean_rows(self):
        from src import time_windows

        return [{"trading_date": self.DAY.isoformat(), "window_label": w.label,
                 "outcome": "NO_SETUP", "reason": "no sweep"}
                for w in time_windows.windows_for_day(self.DAY)]

    def run_certify(self, *extra):
        return main(["--certify", self.DAY.isoformat(), "--out-dir", str(self.out),
                     *extra])

    def test_pass_is_exit_zero(self):
        self.write(self.clean_rows())
        self.assertEqual(self.run_certify("--now", "2026-10-29T01:00"), 0)

    def test_fail_is_exit_one(self):
        rows = self.clean_rows()
        rows[0] = dict(rows[0], reason="live_feed_stalled")
        self.write(rows)
        self.assertEqual(self.run_certify("--now", "2026-10-29T01:00"), 1)

    def closed_rows(self, now_iso, day=None):
        """Clean rows for exactly the windows that have closed by `now_iso`."""
        from zoneinfo import ZoneInfo

        from src import config
        from src import time_windows

        day = day or self.DAY
        stamp = dt.datetime.fromisoformat(now_iso)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo(config.NY_TZ))
        cutoff = stamp.replace(tzinfo=None)
        return [{"trading_date": day.isoformat(), "window_label": w.label,
                 "outcome": "NO_SETUP", "reason": "no sweep"}
                for w in time_windows.windows_for_day(day) if w.end <= cutoff]

    def test_incomplete_is_exit_three(self):
        """Distinct from FAIL: the day is not over, nothing is wrong."""
        self.write(self.closed_rows("2026-10-28T09:30"))
        self.assertEqual(self.run_certify("--now", "2026-10-28T09:30"), 3)

    def test_an_already_closed_window_with_no_row_is_still_a_failure(self):
        """The distinction must not become a way to hide a real gap."""
        rows = self.closed_rows("2026-10-28T09:30")
        self.write([r for r in rows if r["window_label"] != "02:45-03:15"])
        self.assertEqual(self.run_certify("--now", "2026-10-28T09:30"), 1)

    def test_not_applicable_is_exit_two(self):
        self.write([])
        self.assertEqual(main(["--certify", "2026-11-01", "--out-dir", str(self.out),
                               "--now", "2026-11-02T01:00"]), 2)

    def test_a_bad_date_is_exit_two(self):
        self.assertEqual(main(["--certify", "28-10-2026", "--out-dir",
                               str(self.out)]), 2)

    def test_an_open_trade_in_the_state_file_changes_fail_into_incomplete(self):
        rows = [r for r in self.clean_rows() if r["window_label"] != "13:45-14:15"]
        self.write(rows)
        self.assertEqual(self.run_certify("--now", "2026-10-29T01:00"), 1)

        self.write(rows, state={"pending": {"2026-10-28|13:45-14:15": {}}})
        self.assertEqual(self.run_certify("--now", "2026-10-29T01:00"), 3)

    def test_naive_now_means_new_york_wall_clock(self):
        """09:30 means 09:30 NY, not 09:30 UTC — which would be 05:30 NY."""
        from live.audit_triage import _parse_now

        expected = ny_ts(self.DAY, 9, 30)
        self.assertEqual(_parse_now("2026-10-28T09:30"), expected)
        self.assertEqual(_parse_now("2026-10-28T13:30+00:00"), expected)

    def test_an_offset_is_honoured(self):
        self.write(self.closed_rows("2026-10-28T09:30"))
        self.assertEqual(self.run_certify("--now", "2026-10-28T13:30+00:00"), 3)

    def test_late_afternoon_is_incomplete_not_fail(self):
        """The live 2026-10-29 shape, end to end through the CLI."""
        self.write(self.closed_rows("2026-10-29T17:25", day=dt.date(2026, 10, 29)))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = main(["--certify", "2026-10-29", "--out-dir", str(self.out),
                       "--now", "2026-10-29T17:25"])
        text = out.getvalue()
        self.assertEqual(rc, 3, text)
        self.assertIn("INCOMPLETE", text)
        # The exact regression: none of those future windows may appear as a
        # failure. They *may* appear in the "wait" line - that is where they
        # belong, named as work still outstanding rather than as a defect.
        failure_lines = [ln for ln in text.splitlines() if "[FAIL]" in ln]
        self.assertEqual(failure_lines, [], text)
        self.assertNotIn("missing", text.lower())
        self.assertIn("6 window(s) have not closed yet", text)

    def test_a_report_states_which_rule_produced_it(self):
        """A stale checkout must be visible in its own output (§15.18)."""
        self.write(self.clean_rows())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.run_certify("--now", "2026-10-29T01:00")
        self.assertIn("certify v2", out.getvalue())

    def test_fail_reports_the_last_runner_activity(self):
        """A FAIL prompts 'was it even running?'; answer it unasked."""
        rows = self.clean_rows()
        rows[0] = dict(rows[0], reason="live_feed_stalled")
        self.write(rows)
        with (self.out / "runner_state.json").open("w") as fh:
            json.dump({"last_heartbeat_utc":
                       ny_ts(self.DAY, 12, 3), "clean_stop": False}, fh)
        with (self.out / "runner_events.csv").open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["event_utc", "ny_wall", "kind", "detail"])
            w.writeheader()
            w.writerow({"event_utc": "2026-10-28T16:00:00+00:00",
                        "ny_wall": "2026-10-28T12:00:00-04:00",
                        "kind": "start", "detail": "runner up"})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = self.run_certify("--now", "2026-10-29T01:00")
        text = out.getvalue()
        self.assertEqual(rc, 1, text)
        self.assertIn("last runner activity", text)
        self.assertIn("2026-10-28 12:03 NY", text)   # heartbeat, the later of the two

    def test_a_bad_now_is_exit_two(self):
        self.write([])
        self.assertEqual(self.run_certify("--now", "this morning"), 2)

    def test_no_log_files_at_all_is_exit_two(self):
        """A missing log is a usage error; an empty one is a verdict."""
        self.assertEqual(self.run_certify("--now", "2026-10-28T09:30"), 2)


class TestDigest(unittest.TestCase):
    """The streak view: one line per trading day, newest last."""

    MONDAY = dt.date(2026, 10, 26)

    def clean_week(self, *, skip=()):
        """A row for every window of Mon-Fri, 26-30 Oct 2026."""
        from src import time_windows

        rows = []
        for offset in range(5):
            day = self.MONDAY + dt.timedelta(days=offset)
            if day in skip:
                continue
            for w in time_windows.windows_for_day(day):
                rows.append({"trading_date": day.isoformat(),
                             "window_label": w.label,
                             "outcome": "NO_SETUP", "reason": "no sweep"})
        return rows

    def after_friday(self):
        return ny_ts(dt.date(2026, 10, 31), 1, 0)

    def test_five_clean_days_reach_the_phase_exit_criteria(self):
        rendered = render_digest(
            certify_digest(self.clean_week(), days=5, now=self.after_friday(),
                           today=dt.date(2026, 10, 30)),
            days=5)
        self.assertEqual(
            len([ln for ln in rendered.splitlines()
                 if re.match(r"\s+\d{4}-\d{2}-\d{2}\s+" + CERTIFY_PASS, ln)]), 5)
        self.assertIn("consecutive PASS streak: 5", rendered)
        self.assertIn("Phase 2 exit", rendered)

    def test_weekends_do_not_take_a_line(self):
        """`--digest 5` is five decisions, not five calendar days."""
        results = certify_digest(self.clean_week(), days=5,
                                 now=self.after_friday(),
                                 today=dt.date(2026, 10, 30))
        self.assertEqual([r["date"] for r in results],
                         [self.MONDAY + dt.timedelta(days=i) for i in range(5)])

    def test_a_broken_day_resets_the_streak(self):
        results = certify_digest(self.clean_week(skip=(dt.date(2026, 10, 29),)),
                                 days=5, now=self.after_friday(),
                                 today=dt.date(2026, 10, 30))
        rendered = render_digest(results, days=5)
        self.assertIn("consecutive PASS streak: 1", rendered)   # Fri only
        self.assertIn(f"{dt.date(2026, 10, 29)}  FAIL", rendered)

    def test_the_newest_day_may_be_open_without_breaking_the_streak(self):
        # Friday is half-done at noon: rows for the windows that have closed,
        # none yet for the rest. That is a day in progress, not a broken day.
        friday = dt.date(2026, 10, 30)
        rows = self.clean_week(skip=(friday,))
        now = ny_ts(friday, 12, 0)
        rows += [{"trading_date": friday.isoformat(), "window_label": w.label,
                  "outcome": "NO_SETUP", "reason": "no sweep"}
                 for w in time_windows_listing(friday)
                 if w.end <= dt.datetime.combine(friday, dt.time(12, 0))]
        results = certify_digest(rows, days=5, now=now, today=friday)
        self.assertEqual(results[-1]["status"], CERTIFY_INCOMPLETE)
        rendered = render_digest(results, days=5)
        self.assertIn("today is still open", rendered)
        self.assertIn("consecutive PASS streak: 4", rendered)

    def test_a_fail_line_names_its_criteria(self):
        rows = self.clean_week()
        rows[0] = dict(rows[0], reason="dry_run_gap_runner_down")
        results = certify_digest(rows, days=1, now=self.after_friday(),
                                 today=dt.date(2026, 10, 26))
        rendered = render_digest(results, days=1)
        self.assertIn("FAIL", rendered)
        self.assertIn("gaps", rendered)

    def test_the_digest_is_readable_through_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            rows = self.clean_week()
            cols = ["trading_date", "window_label", "outcome", "reason"]
            with (out / "window_evaluations.csv").open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                rc = main(["--digest", "5", "--out-dir", str(out),
                           "--now", "2026-10-31T01:00"])
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            self.assertEqual(text.count("  PASS"), 5)
            self.assertIn("consecutive PASS streak: 5", text)

    def test_a_nonpositive_digest_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["--digest", "0", "--out-dir", tmp])
        self.assertEqual(rc, 2)


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
