"""
Tests for the Phase 2 live probe.

The probe needs Windows + a running MT5 terminal, so it cannot run against a
real terminal here. Instead the MT5 module is **injected**: `main(..., mt5_module=)`
accepts a fake, which lets the whole probe — every section, in order — be rerun
offline.

That is how the first live-run bug is pinned. `SymbolInfo` on the Handler's
terminal has no `session_quotes`, and the probe died on it, taking the clock,
rates and forming-bar sections with it. There is now a test that runs a fake
terminal with that exact attribute missing and asserts the probe still reaches
the offset measurement and exits 0.
"""

import contextlib
import io
import pathlib
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from live import probe_mt5_live as probe  # noqa: E402
from live.probe_mt5_live import (  # noqa: E402
    decode_broker_label,
    forming_bar_index,
    measure_offset_hours,
    rate_times,
    session_invariants,
    to_ny,
)
from src import config  # noqa: E402


def epoch(y, mo, d, h, mi) -> int:
    """A raw broker label expressed as an epoch (the way MT5 hands it over)."""
    import calendar
    return calendar.timegm(datetime(y, mo, d, h, mi).timetuple())


def broker_stamp_for_ny(year, month, day, hour, minute) -> int:
    """The broker label that the −7h rule turns back into this NY wall clock."""
    import calendar
    ny = datetime(year, month, day, hour, minute)
    return calendar.timegm((ny + timedelta(hours=config.BROKER_OFFSET_HOURS)).timetuple())


def broker_now() -> int:
    """A broker stamp consistent with the REAL current New York offset.

    Date-independent on purpose: it stays correct on either side of a DST
    transition, so these tests do not rot in November.
    """
    from zoneinfo import ZoneInfo
    now = datetime.now(timezone.utc)
    utc_naive = now.replace(tzinfo=None)
    try:
        ny_naive = now.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
    except Exception:  # no tzdata on this host: fall back to the bench fallback
        ny_naive = utc_naive - timedelta(hours=4)
    delta = (ny_naive - utc_naive).total_seconds() + config.BROKER_OFFSET_HOURS * 3600
    return int(now.timestamp() + delta)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeSymbolInfo:
    """A SymbolInfo that behaves like a named tuple and can lack fields.

    `session_quotes` is absent by default — that is the real terminal's shape.
    """

    def __init__(self, **overrides):
        self.name = "NAS100"
        self.path = "Indices\\NAS100"
        self.digits = 1
        self.point = 0.1
        self.trade_tick_size = 0.1
        self.trade_tick_value = 0.1
        self.volume_min = 0.1
        self.volume_step = 0.1
        self.volume_max = 1000.0
        self.trade_mode = 4
        self.filling_mode = 2
        self.spread = 30
        for k, v in overrides.items():
            setattr(self, k, v)

    def _asdict(self):
        return dict(vars(self))


class FakeTerminalInfo:
    def __init__(self, connected=True):
        self.name = "MetaTrader 5"
        self.build = 1234
        self.connected = connected


class FakeAccountInfo:
    def __init__(self, trade_mode=0):
        self.login = 12345678
        self.company = "Gain Global Markets / FOREX.com Global"
        self.server = "FOREX.com-Global-Demo"
        self.currency = "USD"
        self.leverage = 100
        self.trade_mode = trade_mode


class FakeTick:
    def __init__(self, stamp):
        self.time = stamp


class FakeDtype:
    def __init__(self, names):
        self.names = names


class FakeRates:
    """Sequence of tuple rows with a numpy-like dtype, no numpy required."""

    def __init__(self, rows, names=("time", "open", "high", "low", "close", "tick_volume")):
        self._rows = rows
        self.dtype = FakeDtype(names)

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, key):
        if isinstance(key, str):
            raise KeyError(key)          # forces the tuple/names path
        return self._rows[key]

    def __iter__(self):
        return iter(self._rows)


class FakeMT5:
    TIMEFRAME_M1 = 1
    __version__ = "5.0.0 (fake)"

    def __init__(self, *, symbol_info=None, account=None, terminal=None,
                 rates="default", tick_stamp=None, init_ok=True, raises=None):
        self._symbol_info = symbol_info if symbol_info is not None else FakeSymbolInfo()
        self._account = account if account is not None else FakeAccountInfo()
        self._terminal = terminal if terminal is not None else FakeTerminalInfo()
        self._tick_stamp = tick_stamp if tick_stamp is not None else broker_now()
        self._init_ok = init_ok
        self._raises = raises or {}
        if rates == "default":
            # A clean, invariant-satisfying week: Sun 18:00 open, Mon-Thu with a
            # 16:59 -> 18:00 break, Friday close 16:59.
            rows = [
                (broker_stamp_for_ny(2026, 10, 11, 18, 0), 1, 1, 1, 1, 5),
                (broker_stamp_for_ny(2026, 10, 12, 16, 59), 1, 1, 1, 1, 5),
                (broker_stamp_for_ny(2026, 10, 12, 18, 0), 1, 1, 1, 1, 5),
                (broker_stamp_for_ny(2026, 10, 15, 16, 59), 1, 1, 1, 1, 5),
                (broker_stamp_for_ny(2026, 10, 15, 18, 0), 1, 1, 1, 1, 5),
                (broker_stamp_for_ny(2026, 10, 16, 16, 59), 1, 1, 1, 1, 5),
            ]
            self._rates = FakeRates(rows)
        else:
            self._rates = rates
        self.shutdown_called = False

    def initialize(self):
        return self._init_ok

    def shutdown(self):
        self.shutdown_called = True

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        if "terminal_info" in self._raises:
            raise self._raises["terminal_info"]
        return self._terminal

    def account_info(self):
        return self._account

    def symbol_info(self, name):
        return self._symbol_info

    def symbol_info_tick(self, name):
        return FakeTick(self._tick_stamp)

    def copy_rates_from_pos(self, name, timeframe, start, count):
        if "copy_rates" in self._raises:
            raise self._raises["copy_rates"]
        return self._rates


def run_probe(mt5, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = probe.main(list(args), mt5_module=mt5)
    return code, buf.getvalue()


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------
class TestConversion(unittest.TestCase):
    """The constant −7h rule (TRD §4.1.1) — the whole conversion."""

    def test_broker_label_is_decoded_verbatim(self):
        e = epoch(2026, 7, 15, 23, 30)
        self.assertEqual(decode_broker_label(e), datetime(2026, 7, 15, 23, 30))

    def test_minus_seven_hours_across_a_day_boundary(self):
        self.assertEqual(to_ny(epoch(2026, 7, 17, 23, 59)), datetime(2026, 7, 17, 16, 59))

    def test_rule_is_constant_in_both_regimes(self):
        self.assertEqual(to_ny(epoch(2026, 1, 14, 22, 0)), datetime(2026, 1, 14, 15, 0))
        self.assertEqual(to_ny(epoch(2026, 7, 14, 22, 0)), datetime(2026, 7, 14, 15, 0))

    def test_offset_constant_is_the_one_config_declares(self):
        self.assertEqual(config.BROKER_OFFSET_HOURS, 7)


class TestMeasuredOffset(unittest.TestCase):
    def test_summer_broker_utc_plus_3(self):
        t = 1_780_000_000.0
        self.assertAlmostEqual(measure_offset_hours(int(t) + 3 * 3600, t), 3.0)

    def test_winter_broker_utc_plus_2(self):
        t = 1_780_000_000.0
        self.assertAlmostEqual(measure_offset_hours(int(t) + 2 * 3600, t), 2.0)

    def test_fall_back_is_observable_as_a_one_hour_step(self):
        """What the live DST monitor is looking for on Sun 1 Nov 2026."""
        t = 1_793_520_000.0
        before = measure_offset_hours(int(t) + 3 * 3600, t)
        after = measure_offset_hours(int(t) + 2 * 3600, t)
        self.assertAlmostEqual(before - after, 1.0)


class TestFormingBar(unittest.TestCase):
    def test_bar_is_forming_until_its_minute_closes(self):
        t = epoch(2026, 10, 26, 14, 30)
        self.assertEqual(forming_bar_index([t - 60, t], t + 59), 1)
        self.assertIsNone(forming_bar_index([t - 60, t], t + 60))

    def test_empty_is_not_forming(self):
        self.assertIsNone(forming_bar_index([], 0))


class TestRateTimes(unittest.TestCase):
    """The rates array must be readable whether or not numpy is present."""

    def test_tuple_rows_with_dtype_names(self):
        rows = [(100, 1, 2, 0, 1, 5), (160, 1, 2, 0, 1, 5)]
        self.assertEqual(rate_times(FakeRates(rows)), [100, 160])

    def test_none_is_empty_not_an_error(self):
        self.assertEqual(rate_times(None), [])


class TestSessionInvariants(unittest.TestCase):
    def _ny(self, *specs):
        return [datetime(*s) for s in specs]

    def test_clean_week_passes(self):
        times = self._ny((2026, 10, 11, 18, 0), (2026, 10, 12, 16, 59), (2026, 10, 12, 18, 0),
                         (2026, 10, 15, 16, 59), (2026, 10, 15, 18, 0), (2026, 10, 16, 16, 59))
        rows = dict((name, ok) for name, ok, _ in session_invariants(times))
        self.assertTrue(all(rows.values()), rows)

    def test_bar_inside_the_break_fails(self):
        rows = dict((name, ok) for name, ok, _ in session_invariants(self._ny((2026, 10, 12, 17, 30))))
        self.assertFalse([ok for name, ok in rows.items() if "break" in name][0])

    def test_friday_bar_after_close_fails(self):
        rows = dict((name, ok) for name, ok, _ in session_invariants(self._ny((2026, 10, 16, 17, 5))))
        self.assertFalse([ok for name, ok in rows.items() if "Friday" in name][0])

    def test_sunday_bar_before_open_fails(self):
        rows = dict((name, ok) for name, ok, _ in session_invariants(self._ny((2026, 11, 1, 17, 30))))
        self.assertFalse([ok for name, ok in rows.items() if "Sunday" in name][0])

    def test_one_hour_shift_is_caught(self):
        times = self._ny((2026, 10, 11, 17, 0), (2026, 10, 12, 15, 59), (2026, 10, 12, 17, 0),
                         (2026, 10, 16, 15, 59))
        self.assertFalse(all(ok for _, ok, _ in session_invariants(times)))


# ---------------------------------------------------------------------------
# The regression: a missing cosmetic field must never kill the probe
# ---------------------------------------------------------------------------
class TestProbeSurvivesMissingFields(unittest.TestCase):
    def test_real_terminal_shape_without_session_quotes_still_completes(self):
        """The exact bug: SymbolInfo has no `session_quotes`, so the run died.

        Everything that matters — the broker-offset measurement, the rates dump
        and the forming-bar check — must still be produced, and the verdict must
        be a pass, not an escalation.
        """
        info = FakeSymbolInfo()
        self.assertFalse(hasattr(info, "session_quotes"))
        code, out = run_probe(FakeMT5(symbol_info=info))
        self.assertEqual(code, 0, out)
        self.assertIn("measured broker - UTC", out)
        self.assertIn("newest bar still forming?", out)
        self.assertIn("session_quotes", out)          # reported, not fatal
        self.assertIn("NOTES", out)
        self.assertNotIn("ESCALATE", out)

    def test_missing_session_deals_is_also_survivable(self):
        code, out = run_probe(FakeMT5(symbol_info=FakeSymbolInfo(session_deals=7)))
        self.assertEqual(code, 0, out)
        self.assertIn("measured broker - UTC", out)

    def test_optional_fields_all_present_does_not_crash(self):
        info = FakeSymbolInfo(session_deals=7, session_quotes=9)
        code, out = run_probe(FakeMT5(symbol_info=info))
        self.assertEqual(code, 0, out)
        self.assertIn("7 / 9", out)

    def test_a_section_that_raises_does_not_stop_the_rest(self):
        """Terminal info blowing up must not cost us the clock or the rates."""
        fake = FakeMT5(raises={"terminal_info": RuntimeError("boom")})
        code, out = run_probe(fake)
        self.assertEqual(code, 1)                      # escalation, correctly
        self.assertIn("[SECTION FAILED] RuntimeError: boom", out)
        self.assertIn("measured broker - UTC", out)    # still measured
        self.assertIn("session invariants", out)

    def test_symbol_without_tick_size_or_point_escalates_cleanly(self):
        class NoPoint:
            name, path, digits = "NAS100", "x", 1
            trade_tick_size = None
            point = None
        code, out = run_probe(FakeMT5(symbol_info=NoPoint()))
        self.assertEqual(code, 1)
        self.assertIn("neither trade_tick_size nor point", out)

    def test_field_helper_never_raises(self):
        class Hostile:
            @property
            def boom(self):
                raise RuntimeError("nope")
        self.assertEqual(probe.field(Hostile(), "boom", "dflt"), "dflt")
        self.assertEqual(probe.field(object(), "nothing", "dflt"), "dflt")


# ---------------------------------------------------------------------------
# Guard rails the probe must keep enforcing
# ---------------------------------------------------------------------------
class TestProbeVerdicts(unittest.TestCase):
    def test_clean_fake_passes(self):
        code, out = run_probe(FakeMT5())
        self.assertEqual(code, 0, out)
        self.assertIn("[PASS] no Saturday bars", out)
        self.assertIn("NY+7 model                  : CONSISTENT", out)

    def test_real_account_escalates(self):
        code, out = run_probe(FakeMT5(account=FakeAccountInfo(trade_mode=2)))
        self.assertEqual(code, 1)
        self.assertIn("NOT a demo account", out)

    def test_disconnected_terminal_escalates(self):
        code, out = run_probe(FakeMT5(terminal=FakeTerminalInfo(connected=False)))
        self.assertEqual(code, 1)
        self.assertIn("disconnected", out)

    def test_wrong_tick_size_escalates(self):
        code, out = run_probe(FakeMT5(symbol_info=FakeSymbolInfo(trade_tick_size=0.01)))
        self.assertEqual(code, 1)
        self.assertIn("does not match Phase 1's", out)

    def test_no_rates_escalates_but_clock_still_reported(self):
        code, out = run_probe(FakeMT5(rates=None))
        self.assertEqual(code, 1)
        self.assertIn("copy_rates_from_pos returned nothing", out)
        self.assertIn("measured broker - UTC", out)

    def test_no_tick_escalates(self):
        code, out = run_probe(FakeMT5(tick_stamp=0))
        self.assertEqual(code, 1)
        self.assertIn("could not be measured", out)
        self.assertIn("forming-bar check skipped", out)

    def test_impossible_offset_escalates(self):
        """A feed that is not UTC+2/+3 breaks the NY+7 model outright."""
        code, out = run_probe(FakeMT5(tick_stamp=int(time.time()) + 9 * 3600))
        self.assertEqual(code, 1)
        self.assertIn("does not hold on this feed", out)

    def test_failed_initialize_reports_and_stops(self):
        code, out = run_probe(FakeMT5(init_ok=False))
        self.assertEqual(code, 1)
        self.assertIn("initialize() failed", out)

    def test_spread_is_captured_as_a_note_for_phase_3(self):
        code, out = run_probe(FakeMT5())
        self.assertIn("30 points", out)
        self.assertIn("Phase 3", out)

    def test_shutdown_is_always_called(self):
        fake = FakeMT5()
        run_probe(fake)
        self.assertTrue(fake.shutdown_called)


class TestNoOrderSurface(unittest.TestCase):
    """Structural guarantee: the probe must not be able to trade."""

    FORBIDDEN = ("order_send", "order_check", "OrderSend", "positions_close")

    def test_probe_source_never_references_an_order_call(self):
        src = (pathlib.Path(__file__).resolve().parents[1]
               / "live" / "probe_mt5_live.py").read_text(encoding="utf-8")
        body = src.split('"""', 2)[-1]     # skip the module docstring
        for name in self.FORBIDDEN:
            self.assertNotIn(name, body, f"probe must not reference {name}")


if __name__ == "__main__":
    unittest.main()
