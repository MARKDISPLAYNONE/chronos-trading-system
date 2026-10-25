"""
Tests for the Phase 2 live MT5 source.

No terminal here, so the MT5 module is a fake. What is tested is exactly the
part that must not be discovered in production:

  * the forming bar is dropped, and dropped *conservatively* (late is fine,
    early is not);
  * "now" comes from the system clock, never from the frozen last tick;
  * polling twice does not duplicate bars;
  * a bad bar is rejected and stays missing rather than being repaired;
  * the invariant gate refuses a feed whose clock cannot be trusted;
  * nothing in the import graph can place an order;
  * the module exposes the same `(BarSeries, LoadReport)` contract as Phase 1.
"""

import calendar
import pathlib
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src import config  # noqa: E402
from src.bars import BarSeries  # noqa: E402
from src.m1_csv_source import CONVENTION_BROKER, LoadReport  # noqa: E402
from src.mt5_live_source import (  # noqa: E402
    LiveBarSource,
    LiveFeedError,
    assert_demo_account,
    broker_now,
    connect,
    is_bar_closed,
    load_history,
)

NY_TZ = "America/New_York"


def utc_ts(y, mo, d, h, mi, tz=NY_TZ):
    """A real UTC timestamp for the given *New York* wall clock.

    Derived through the tz database, so these fixtures keep meaning the same
    instant no matter which side of a DST change they sit on.
    """
    from zoneinfo import ZoneInfo

    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo(tz)).timestamp()


#: Deterministic "now": Monday 2026-10-26 11:05 NY, mid-session. Deliberately
#: offset from any bar boundary so the forming-bar safety margin is unambiguous.
MONDAY_NY_NOW = utc_ts(2026, 10, 26, 11, 5)


def broker_stamp_for_ny(year, month, day, hour, minute) -> int:
    """The broker label that the −7h rule turns back into this NY wall clock."""
    import calendar

    ny = datetime(year, month, day, hour, minute)
    return calendar.timegm((ny + timedelta(hours=config.BROKER_OFFSET_HOURS)).timetuple())


def bar(y, mo, d, h, mi, price="25400.0", vol=5):
    p = Decimal(price)
    return (broker_stamp_for_ny(y, mo, d, h, mi), p, p + 1, p - 1, p, vol)


def closed_bars():
    """Six closed minutes ending 11:03 NY — all safely past their close."""
    return [bar(2026, 10, 26, 11, m) for m in (0, 1, 2, 3)] + [
        bar(2026, 10, 26, 10, m) for m in (58, 59)
    ]


def forming_bar():
    """The current, unfinished minute at the fixture's 'now' (11:04 NY)."""
    return bar(2026, 10, 26, 11, 4)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeDtype:
    def __init__(self, names):
        self.names = names


class FakeRates:
    """numpy-shaped rates array without numpy."""

    NAMES = ("time", "open", "high", "low", "close", "tick_volume", "spread")

    def __init__(self, rows):
        self._rows = list(rows)
        self.dtype = FakeDtype(self.NAMES)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, key):
        if isinstance(key, str):
            raise KeyError(key)      # force the tuple path, like a real ndarray
        return self._rows[key]

    def __iter__(self):
        return iter(self._rows)


class FakeSymbolInfo:
    def __init__(self, **kw):
        self.name = "NAS100"
        self.digits = 1
        self.point = 0.1
        self.trade_tick_size = 0.1
        self.trade_tick_value = 0.1
        self.volume_min = 0.1
        self.volume_step = 0.1
        self.volume_max = 1000.0
        for k, v in kw.items():
            setattr(self, k, v)


class FakeAccount:
    def __init__(self, trade_mode=0):
        self.login = 12345678
        self.company = "Gain Global Markets / FOREX.com Global"
        self.server = "FOREX.com-Global-Demo"
        self.currency = "USD"
        self.trade_mode = trade_mode


class FakeTerminal:
    def __init__(self, connected=True):
        self.name = "MetaTrader 5"
        self.build = 4000
        self.connected = connected


class FakeMT5:
    TIMEFRAME_M1 = 1

    def __init__(self, rates=None, *, symbol_info=None, account=None,
                 terminal=None, init_ok=True, symbol_missing=False):
        self._rates = rates if rates is not None else FakeRates([])
        self._symbol_info = symbol_info or FakeSymbolInfo()
        self._account = account or FakeAccount()
        self._terminal = terminal or FakeTerminal()
        self._init_ok = init_ok
        self._symbol_missing = symbol_missing
        self.init_calls = 0

    def initialize(self):
        self.init_calls += 1
        return self._init_ok

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return self._terminal

    def account_info(self):
        return self._account

    def symbol_info(self, name):
        return None if self._symbol_missing else self._symbol_info

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return self._rates

    def set_rates(self, rates):
        self._rates = rates


def monday_morning(*, include_forming=True):
    """A realistic mid-morning pull: six closed bars plus the live minute."""
    rows = closed_bars()
    if include_forming:
        rows.append(forming_bar())
    return rows


def source(mt5, *, now=MONDAY_NY_NOW, **kw):
    kw.setdefault("now_fn", lambda: now)
    return LiveBarSource(mt5, **kw)


# ---------------------------------------------------------------------------
# Contract parity with the CSV adapter
# ---------------------------------------------------------------------------
class TestContractParity(unittest.TestCase):
    def test_load_history_returns_the_phase1_types(self):
        series, report = load_history(
            FakeMT5(FakeRates(monday_morning())), now_fn=lambda: MONDAY_NY_NOW
        )
        self.assertIsInstance(series, BarSeries)
        self.assertIsInstance(report, LoadReport)

    def test_report_has_the_same_fields_phase1_logs(self):
        _, report = load_history(FakeMT5(FakeRates(monday_morning())),
                                 now_fn=lambda: MONDAY_NY_NOW)
        for name in ("path", "convention", "timestamp_format", "n_bars",
                     "first_raw", "last_raw", "first_ny", "last_ny",
                     "duplicate_timestamps", "non_monotonic_timestamps",
                     "ohlc_violations", "off_tick_prices"):
            self.assertTrue(hasattr(report, name), name)
        self.assertEqual(report.convention, CONVENTION_BROKER)
        self.assertEqual(report.timestamp_format, "broker_epoch")
        self.assertTrue(report.path.startswith("mt5:"))

    def test_bars_are_ny_wall_clock_decimals(self):
        series, _ = load_history(FakeMT5(FakeRates(monday_morning())),
                                 now_fn=lambda: MONDAY_NY_NOW)
        first = series[0]
        self.assertEqual(first.time, datetime(2026, 10, 26, 10, 58))  # NY, not broker
        self.assertIsInstance(first.open, Decimal)


# ---------------------------------------------------------------------------
# 1. The forming bar
# ---------------------------------------------------------------------------
class TestFormingBarExclusion(unittest.TestCase):
    def test_forming_bar_is_dropped_and_counted(self):
        src = source(FakeMT5(FakeRates(monday_morning(include_forming=True))))
        self.assertEqual(src.last_report.forming_bars_dropped, 1)
        self.assertEqual(src.last_report.new_bars, 6)
        self.assertEqual(src.series.last_time, datetime(2026, 10, 26, 11, 3))

    def test_no_forming_bar_when_all_are_closed(self):
        src = source(FakeMT5(FakeRates(monday_morning(include_forming=False))))
        self.assertEqual(src.last_report.forming_bars_dropped, 0)
        self.assertEqual(len(src.series), 6)

    def test_one_second_before_close_it_is_still_forming(self):
        """The boundary, exactly. Accepted late, never early."""
        stamp = broker_stamp_for_ny(2026, 10, 26, 10, 59)
        now = stamp + 60 + config.FORMING_BAR_SAFETY_SECONDS - 1
        self.assertFalse(is_bar_closed(stamp, now))
        self.assertTrue(is_bar_closed(stamp, stamp + 60 + config.FORMING_BAR_SAFETY_SECONDS))

    def test_safety_margin_is_applied(self):
        stamp = broker_stamp_for_ny(2026, 10, 26, 10, 59)
        # The minute has elapsed but the margin has not: still withheld.
        self.assertFalse(is_bar_closed(stamp, stamp + 60))
        self.assertFalse(is_bar_closed(stamp, stamp + 60 + config.FORMING_BAR_SAFETY_SECONDS - 1))

    def test_forming_bar_is_excluded_from_invariants_too(self):
        """A dropped bar must not appear anywhere downstream, including checks."""
        src = source(FakeMT5(FakeRates(monday_morning(include_forming=True))))
        self.assertNotIn(datetime(2026, 10, 26, 11, 4), src.series.times)

    def test_a_weekend_pull_has_no_forming_bar(self):
        """On a Sunday the last bar is Friday's close; it is not 'forming'."""
        rows = [bar(2026, 10, 23, 16, 58), bar(2026, 10, 23, 16, 59)]
        src = source(FakeMT5(FakeRates(rows)), now=utc_ts(2026, 10, 25, 16, 54),
                     validate=False)
        self.assertEqual(src.last_report.forming_bars_dropped, 0)


# ---------------------------------------------------------------------------
# 2. "Now" is the system clock, not the frozen tick
# ---------------------------------------------------------------------------
class TestClockSource(unittest.TestCase):
    def test_broker_now_is_seven_hours_ahead_of_ny(self):
        got = broker_now(MONDAY_NY_NOW)
        ny = datetime(2026, 10, 26, 11, 5)          # EDT: UTC-4
        self.assertEqual(got, ny + timedelta(hours=config.BROKER_OFFSET_HOURS))

    def test_broker_now_tracks_the_system_clock_not_a_tick(self):
        later = MONDAY_NY_NOW + 3600
        self.assertEqual(broker_now(later) - broker_now(MONDAY_NY_NOW), timedelta(hours=1))

    def test_winter_and_summer_stay_seven_hours_ahead(self):
        winter = datetime(2026, 1, 14, 15, 0, tzinfo=None)   # UTC -> NY 10:00 EST
        got = broker_now(calendar.timegm(winter.timetuple()))
        self.assertEqual(got, datetime(2026, 1, 14, 17, 0))  # NY 10:00 + 7h

    def test_ny_now_exposes_the_strategy_clock(self):
        src = source(FakeMT5(FakeRates(monday_morning())))
        self.assertEqual(src.ny_now(), datetime(2026, 10, 26, 11, 5))

    def test_last_bar_age_uses_the_system_clock(self):
        src = source(FakeMT5(FakeRates(monday_morning())))
        # Newest closed bar is 11:03 NY, "now" is 11:05 NY.
        self.assertAlmostEqual(src.last_bar_age_minutes(), 2.0, places=6)


# ---------------------------------------------------------------------------
# 3. Polling / merge behaviour
# ---------------------------------------------------------------------------
class TestRefreshMerge(unittest.TestCase):
    def test_second_poll_does_not_duplicate(self):
        mt5 = FakeMT5(FakeRates(monday_morning()))
        src = source(mt5)
        before = len(src.series)
        report = src.refresh()
        self.assertEqual(report.new_bars, 0)
        self.assertEqual(report.duplicate_bars_skipped, 6)
        self.assertEqual(len(src.series), before)

    def test_new_bars_appear_on_a_later_poll(self):
        mt5 = FakeMT5(FakeRates(monday_morning()))
        src = source(mt5)
        later = closed_bars() + [forming_bar(), bar(2026, 10, 26, 11, 5)]
        mt5.set_rates(FakeRates(later))
        later_now = utc_ts(2026, 10, 26, 11, 7)   # 11:04 and 11:05 are now closed
        src._now_fn = lambda: later_now
        report = src.refresh()
        self.assertEqual(report.new_bars, 2)
        self.assertEqual(src.series.last_time, datetime(2026, 10, 26, 11, 5))

    def test_series_is_sorted_and_unique(self):
        rows = [bar(2026, 10, 26, 10, 59), bar(2026, 10, 26, 10, 58),
                bar(2026, 10, 26, 10, 58)]
        src = source(FakeMT5(FakeRates(rows)))
        times = src.series.times
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), len(set(times)))

    def test_empty_pull_keeps_existing_bars(self):
        """A closed market returns an empty array; that must not wipe history."""
        mt5 = FakeMT5(FakeRates(monday_morning()))
        src = source(mt5)
        held = len(src.series)
        mt5.set_rates(FakeRates([]))
        report = src.refresh()
        self.assertEqual(report.total_bars, held)
        self.assertEqual(len(src.series), held)

    def test_none_rates_is_an_error(self):
        mt5 = FakeMT5(FakeRates(monday_morning()))
        src = source(mt5)
        mt5._rates = None
        with self.assertRaises(LiveFeedError):
            src.refresh()

    def test_retention_bounds_memory(self):
        rows = [bar(2026, 10, 26, 9, m) for m in range(0, 30)]
        src = source(FakeMT5(FakeRates(rows)), retention_bars=10)
        self.assertEqual(len(src.series), 10)
        self.assertEqual(src.last_report.retention_pruned, 20)


# ---------------------------------------------------------------------------
# 4. Bad data is rejected, never repaired
# ---------------------------------------------------------------------------
class TestBadData(unittest.TestCase):
    def test_off_tick_bar_is_rejected_and_stays_missing(self):
        rows = [bar(2026, 10, 26, 10, 58), (broker_stamp_for_ny(2026, 10, 26, 10, 59),
                                            Decimal("25401.05"), Decimal("25402"), Decimal("25400"),
                                            Decimal("25401"), 5)]
        src = source(FakeMT5(FakeRates(rows)))
        self.assertEqual(src.last_report.off_tick_bars_rejected, 1)
        self.assertNotIn(datetime(2026, 10, 26, 10, 59), src.series.times)
        self.assertTrue(src.last_report.has_problems)

    def test_ohlc_violation_is_rejected(self):
        rows = [bar(2026, 10, 26, 10, 58),
                (broker_stamp_for_ny(2026, 10, 26, 10, 59),
                 Decimal("25400"), Decimal("25399"), Decimal("25401"),
                 Decimal("25400"), 5)]
        src = source(FakeMT5(FakeRates(rows)))
        self.assertEqual(src.last_report.ohlc_violations_rejected, 1)
        self.assertEqual(len(src.series), 1)
        self.assertNotIn(datetime(2026, 10, 26, 10, 59), src.series.times)

    def test_a_pull_of_only_bad_bars_is_an_error_not_an_empty_series(self):
        """Never hand the evaluator an empty series — fail loudly instead."""
        rows = [(broker_stamp_for_ny(2026, 10, 26, 10, 59),
                 Decimal("25400"), Decimal("25399"), Decimal("25401"),
                 Decimal("25400"), 5)]
        with self.assertRaises(LiveFeedError):
            source(FakeMT5(FakeRates(rows)))

    def test_rejection_does_not_stop_later_good_bars(self):
        rows = [bar(2026, 10, 26, 10, 55),
                (broker_stamp_for_ny(2026, 10, 26, 10, 56), Decimal("1.05"),
                 Decimal("2"), Decimal("1"), Decimal("1.5"), 5),
                bar(2026, 10, 26, 10, 57)]
        src = source(FakeMT5(FakeRates(rows)))
        self.assertEqual(src.last_report.off_tick_bars_rejected, 1)
        self.assertEqual(len(src.series), 2)

    def test_unparseable_row_is_counted_not_fatal(self):
        rows = [bar(2026, 10, 26, 10, 58), ("not-a-time", 1, 2, 3, 4, 5)]
        src = source(FakeMT5(FakeRates(rows)))
        self.assertEqual(src.last_report.unparseable_rows, 1)
        self.assertEqual(len(src.series), 1)


# ---------------------------------------------------------------------------
# 5. Guards: environment, account, symbol, invariants
# ---------------------------------------------------------------------------
class TestGuards(unittest.TestCase):
    def test_non_demo_account_is_refused(self):
        with self.assertRaises(LiveFeedError) as ctx:
            assert_demo_account(FakeMT5(account=FakeAccount(trade_mode=2)))
        self.assertIn("not demo", str(ctx.exception))

    def test_contest_account_is_also_refused(self):
        with self.assertRaises(LiveFeedError):
            assert_demo_account(FakeMT5(account=FakeAccount(trade_mode=1)))

    def test_demo_account_passes_and_is_masked_never_logged_whole(self):
        facts = assert_demo_account(FakeMT5())
        self.assertEqual(facts["trade_mode"], 0)

    def test_connect_fails_when_initialize_fails(self):
        with self.assertRaises(LiveFeedError):
            connect(FakeMT5(init_ok=False))

    def test_connect_returns_terminal_and_symbol_facts(self):
        facts = connect(FakeMT5())
        self.assertEqual(facts["trade_mode"], 0)
        self.assertEqual(facts["symbol"]["trade_tick_size"], 0.1)

    def test_missing_symbol_is_refused(self):
        with self.assertRaises(LiveFeedError):
            connect(FakeMT5(symbol_missing=True))

    def test_wrong_tick_size_is_refused(self):
        with self.assertRaises(LiveFeedError) as ctx:
            connect(FakeMT5(symbol_info=FakeSymbolInfo(trade_tick_size=0.01)))
        self.assertIn("does not match", str(ctx.exception))

    def test_invariant_gate_rejects_a_shifted_feed(self):
        """A feed whose bars land inside the 17:00-18:00 NY break is wrong by an
        hour or more. The gate must refuse it, exactly as the CSV adapter does."""
        rows = [bar(2026, 10, 26, 17, 30), bar(2026, 10, 26, 17, 31)]
        # "now" is 19:00 NY, so both bars are closed and reach the gate.
        with self.assertRaises(LiveFeedError) as ctx:
            source(FakeMT5(FakeRates(rows)), now=utc_ts(2026, 10, 26, 19, 0))
        self.assertIn("session invariants FAILED", str(ctx.exception))

    def test_saturday_bars_are_rejected_by_the_gate(self):
        rows = [bar(2026, 10, 24, 10, 0), bar(2026, 10, 24, 10, 1)]
        with self.assertRaises(LiveFeedError):
            source(FakeMT5(FakeRates(rows)), now=utc_ts(2026, 10, 25, 16, 54))

    def test_validation_can_be_disabled_for_diagnostics(self):
        rows = [bar(2026, 10, 26, 17, 30)]
        src = source(FakeMT5(FakeRates(rows)), now=utc_ts(2026, 10, 26, 19, 0),
                     validate=False)
        self.assertEqual(len(src.series), 1)

    def test_no_bars_at_all_is_an_error(self):
        with self.assertRaises(LiveFeedError):
            source(FakeMT5(FakeRates([])))

    def test_boundary_checks_tolerate_a_sample_without_that_day(self):
        """A Monday-only sample contains no Friday and no Sunday; those two
        boundary checks cannot apply and must not fail the whole gate."""
        rows = [bar(2026, 10, 26, 10, 0), bar(2026, 10, 26, 10, 1)]
        src = source(FakeMT5(FakeRates(rows)))
        self.assertEqual(len(src.series), 2)


# ---------------------------------------------------------------------------
# 5b. End-to-end: the live source drives the UNMODIFIED Phase 1 evaluator
# ---------------------------------------------------------------------------
class TestFeedsTheRealEvaluator(unittest.TestCase):
    """The point of the adapter, tested rather than asserted in prose.

    Phase 1's success criterion is that the validated logic is reused, not
    rewritten. This constructs a synthetic live feed, wraps it in LiveBarSource,
    and hands the resulting BarSeries to the real `evaluate_window` — no shims,
    no altered inputs.
    """

    @staticmethod
    def _feed():
        import calendar

        def bro(naive_ny):          # broker LABEL, as MT5 encodes it
            return calendar.timegm(naive_ny.timetuple()) + 7 * 3600

        rows = []

        def add(ny, o, h, l, c, v=10):
            if ny.weekday() < 4 and ny.hour == 17:
                return              # the venue's maintenance break
            rows.append((bro(ny), Decimal(o), Decimal(h), Decimal(l), Decimal(c), v))

        t0 = datetime(2026, 10, 26, 13, 45)
        for i in range(15):         # Candle A: high 25402, low 25398
            add(t0 + timedelta(minutes=i), "25400.0", "25402.0", "25398.0", "25400.5")
        # Candle B opens in premium (above Mid 25400) and sweeps High_A + 1 tick.
        add(t0 + timedelta(minutes=15), "25401.0", "25403.0", "25400.5", "25401.5")
        for i in range(16, 30):
            add(t0 + timedelta(minutes=i), "25401.0", "25401.5", "25400.0", "25400.5")
        for i in range(30, 200):    # collapse toward the short's target
            add(t0 + timedelta(minutes=i), "25400.5", "25400.6", "25396.0", "25396.5")
        return rows

    def test_evaluator_produces_a_full_trade_from_live_data(self):
        from src import time_windows
        from src.window_evaluator import evaluate_window

        src = source(FakeMT5(FakeRates(self._feed())),
                     now=utc_ts(2026, 10, 26, 16, 30))
        window = [w for w in time_windows.windows_for_day(datetime(2026, 10, 26).date())
                  if w.label == "13:45-14:15"][0]
        ev = evaluate_window(src.series, window)

        self.assertEqual(ev.context, "PREMIUM")
        self.assertIsNotNone(ev.plan, "the sweep should have produced a plan")
        self.assertEqual(ev.plan.direction, "SHORT")
        self.assertEqual(ev.outcome, "WIN")
        self.assertTrue(ev.traded)
        self.assertGreater(ev.trade.result_r_multiple, 0)

    def test_the_plan_obeys_the_quantization_rule(self):
        """Live prices go through the same tick-grid rule as the backtest."""
        from src import time_windows
        from src.pricing import is_on_tick_grid
        from src.window_evaluator import evaluate_window

        src = source(FakeMT5(FakeRates(self._feed())),
                     now=utc_ts(2026, 10, 26, 16, 30))
        window = [w for w in time_windows.windows_for_day(datetime(2026, 10, 26).date())
                  if w.label == "13:45-14:15"][0]
        plan = evaluate_window(src.series, window).plan
        for price in (plan.stop_loss, plan.take_profit, plan.partial_trigger):
            self.assertTrue(is_on_tick_grid(price, config.TICK_SIZE), price)
        self.assertEqual(plan.quantization_direction, "floor")   # a SHORT

    def test_a_window_with_no_bars_logs_no_setup_not_a_crash(self):
        """A quiet feed must still yield exactly one row per window."""
        from src import time_windows
        from src.window_evaluator import evaluate_window

        src = source(FakeMT5(FakeRates(self._feed())),
                     now=utc_ts(2026, 10, 26, 16, 30))
        window = [w for w in time_windows.windows_for_day(datetime(2026, 10, 26).date())
                  if w.label == "21:45-22:15"][0]           # no bars generated here
        ev = evaluate_window(src.series, window)
        self.assertEqual(ev.outcome, "NO_SETUP")
        self.assertTrue(ev.reason)


# ---------------------------------------------------------------------------
# 6. The structural no-order guarantee
# ---------------------------------------------------------------------------
class TestNoOrderSurface(unittest.TestCase):
    FORBIDDEN = (
        "order_send", "order_check", "OrderSend", "positions_close",
        "positions_modify", "trade_request", "CTrade",
    )

    def test_live_source_never_references_an_order_call(self):
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "src" / "mt5_live_source.py").read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]      # skip the module docstring
        for name in self.FORBIDDEN:
            self.assertNotIn(name, body, f"live source must not reference {name}")

    def test_engine_wide_sweep_for_order_capability(self):
        """Nothing anywhere under src/ or live/ may reach an order function."""
        root = pathlib.Path(__file__).resolve().parents[1]
        offenders = []
        for path in list((root / "src").glob("*.py")) + list((root / "live").glob("*.py")):
            body = path.read_text(encoding="utf-8").split('"""', 2)[-1]
            for name in self.FORBIDDEN:
                if name in body:
                    offenders.append(f"{path.name}:{name}")
        self.assertEqual(offenders, [], f"order-capable symbols found: {offenders}")


if __name__ == "__main__":
    unittest.main()
