"""
Project Chronos — Phase 2 live MT5 M1 source
============================================

The live counterpart of `m1_csv_source.py`. Same contract, same output type,
different feed:

    m1_csv_source.load_bars(path)   -> (BarSeries, LoadReport)   # Phase 1
    mt5_live_source.load_history(mt5, symbol) -> (BarSeries, LoadReport)  # Phase 2

Downstream nothing changes. `window_evaluator`, `candle_tracker`,
`context_classifier`, `sweep_detector`, `trade_calculator` and
`trade_simulator` receive a `BarSeries` and cannot tell where it came from.
That is the point: Phase 1's validation carries over untouched, and there is
exactly one place in the system where a raw MT5 timestamp becomes New York
wall-clock time.

--------------------------------------------------------------------------
THREE THINGS THE CSV NEVER TAUGHT US
--------------------------------------------------------------------------
Phase 1 read a closed, complete file. A live feed differs in three ways, and
each one is handled explicitly rather than discovered in production:

1. **The newest bar is still forming.** `copy_rates_from_pos(..., 0, N)`
   returns the bar for the current, unfinished minute. Candle A and Candle B are
   built from *closed* bars only, so accepting a forming bar would let an
   incomplete candle decide a window. It is dropped, and the drop is counted and
   surfaced — never silent. A bar is only accepted once
   `bar.time + 60 + FORMING_BAR_SAFETY_SECONDS <= broker_now`, and the safety
   margin is one-sided on purpose: a bar may be accepted a few seconds late (it
   arrives on the next poll), never early.

2. **"Now" is not the last tick.** The obvious way to learn the broker's current
   time is `symbol_info_tick().time`. It is wrong. On a closed market that stamp
   is frozen at the closing tick, so using it as "now" makes the last bar look
   unfinished forever — the adapter would report a forming bar every Sunday, and
   Phase 2's gap protocol would misclassify the weekend. Current broker time is
   therefore derived from this machine's clock through `America/New_York` + 7h
   (TRD §4.1.1 rule 5). The tick is only ever used for freshness diagnostics.

3. **Polls can return the same bar twice.** Every poll re-reads a trailing
   window, so bars are merged by timestamp; a repeat is counted, not appended.

--------------------------------------------------------------------------
WHAT IS DELIBERATELY *NOT* HERE
--------------------------------------------------------------------------
- **No order functions.** Nothing in this module can place, modify or close an
  order; it does not import anything that can. Phase 2's "zero orders" guarantee
  is structural, and `test_no_order_path.py` asserts it over the import graph.
- **No gap filling.** A missing minute stays missing. The evaluator's existing
  classifier decides whether that means a market closure or a feed hole; the
  adapter only reports what it saw.
- **No re-derivation of the conversion.** `to_ny_wall(..., "broker")` is
  imported from `m1_csv_source`, so the −7h rule has exactly one implementation.
- **No trading-session logic.** Which windows to evaluate is `time_windows`'
  job, unchanged from Phase 1.

--------------------------------------------------------------------------
WINDOWS / ENVIRONMENT NOTE
--------------------------------------------------------------------------
Importing this module pulls in `zoneinfo`-using code, and Windows has no system
tz database. Phase 2 therefore needs the free `tzdata` package installed
(see `bot/requirements.txt`); `ensure_environment()` checks for it and fails with
an actionable message rather than a traceback. The live probe reports the same
thing in its environment section.
"""

from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from . import config
from . import time_windows
from .bars import BarSeries, M1Bar
from .m1_csv_source import (
    CONVENTION_BROKER,
    AdapterError,
    LoadReport,
    check_session_invariants,
    epoch_to_label,
    to_ny_wall,
)
from .pricing import is_on_tick_grid

#: MT5 rate dtypes name the volume column differently across builds/versions.
VOLUME_FIELDS = ("tick_volume", "real_volume", "volume")


class LiveFeedError(AdapterError):
    """The live feed cannot be trusted as strategy input."""


# ---------------------------------------------------------------------------
# Environment / connection
# ---------------------------------------------------------------------------
def ensure_environment() -> None:
    """Fail early and actionably if the host cannot run Phase 2.

    Windows ships no IANA tz database, so `ZoneInfo("America/New_York")` raises
    unless the free `tzdata` package is installed. Strategy code needs that
    database (window localisation, DST guards), so this is checked before a
    terminal is even touched.
    """
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(config.NY_TZ)
        ZoneInfo(config.UTC_TZ)
    except Exception as exc:  # pragma: no cover - host dependent
        raise LiveFeedError(
            "this machine has no IANA timezone database, so Phase 2 cannot run.\n"
            "  Windows does not ship one. Install the free package:\n"
            "      pip install tzdata\n"
            f"  (underlying error: {type(exc).__name__}: {exc})"
        ) from exc


def assert_demo_account(mt5: Any) -> dict:
    """Refuse to run against anything but a demo account.

    Phase 2 is a dry run and must never touch live capital. This is a hard stop,
    not a warning: the account is checked before any window is evaluated.
    """
    account = mt5.account_info()
    if account is None:
        raise LiveFeedError(
            f"account_info() returned None (last_error={mt5.last_error()}) — "
            "is the terminal logged in?"
        )
    trade_mode = getattr(account, "trade_mode", None)
    if trade_mode != 0:
        raise LiveFeedError(
            f"account trade_mode={trade_mode!r} is not demo (0=demo, 1=contest, "
            f"2=real) — refusing to run Phase 2 against a non-demo account"
        )
    return {
        "login": getattr(account, "login", None),
        "company": getattr(account, "company", None),
        "server": getattr(account, "server", None),
        "currency": getattr(account, "currency", None),
        "trade_mode": trade_mode,
    }


def connect(mt5: Any, *, require_demo: bool = True) -> dict:
    """`initialize()` + account guard. Returns a small facts dict for logging."""
    ensure_environment()
    if not mt5.initialize():
        raise LiveFeedError(
            f"MetaTrader5.initialize() failed (last_error={mt5.last_error()}). "
            "Is the terminal installed, running and logged in?"
        )
    info = mt5.terminal_info()
    if info is None:
        raise LiveFeedError(f"terminal_info() returned None (last_error={mt5.last_error()})")
    facts: dict = {
        "terminal": getattr(info, "name", None),
        "build": getattr(info, "build", None),
        "connected": getattr(info, "connected", None),
    }
    if require_demo:
        facts.update(assert_demo_account(mt5))
    facts["symbol"] = _assert_symbol(mt5, config.INSTRUMENT_SYMBOL)
    return facts


def _assert_symbol(mt5: Any, symbol: str) -> dict:
    """Confirm the symbol exists and its tick size matches the engine's."""
    info = mt5.symbol_info(symbol)
    if info is None:
        raise LiveFeedError(
            f"symbol {symbol!r} not found on this terminal "
            f"(last_error={mt5.last_error()}) — refusing to substitute a lookalike"
        )
    tick_size = getattr(info, "trade_tick_size", None) or getattr(info, "point", None)
    if tick_size is None:
        raise LiveFeedError(f"symbol {symbol!r} exposes neither trade_tick_size nor point")
    if abs(Decimal(str(tick_size)) - config.TICK_SIZE) > Decimal("1e-12"):
        raise LiveFeedError(
            f"symbol {symbol!r} trade_tick_size={tick_size} does not match "
            f"config.TICK_SIZE={config.TICK_SIZE}; the quantization rule "
            f"(TRD §4.1.2) is built on that value"
        )
    return {
        "name": getattr(info, "name", symbol),
        "digits": getattr(info, "digits", None),
        "point": getattr(info, "point", None),
        "trade_tick_size": tick_size,
        "trade_tick_value": getattr(info, "trade_tick_value", None),
        "volume_min": getattr(info, "volume_min", None),
        "volume_step": getattr(info, "volume_step", None),
        "volume_max": getattr(info, "volume_max", None),
    }


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------
def broker_now(now_utc: float | None = None) -> dt.datetime:
    """The broker's current wall clock, from this machine's clock.

    Deliberately not from `symbol_info_tick()`, which freezes when the market
    closes (TRD §4.1.1 rule 5). Falls back to a fixed −4h NY estimate **only**
    if tzdata is absent, and `ensure_environment()` has already made that a
    startup failure — the fallback exists so a misconfiguration cannot silently
    shift bars in a way that looks like real data.
    """
    if now_utc is None:
        import time

        now_utc = time.time()
    utc_now = dt.datetime.fromtimestamp(now_utc, dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        ny_now = utc_now.astimezone(ZoneInfo(config.NY_TZ)).replace(tzinfo=None)
    except Exception:  # pragma: no cover - guarded at startup
        ny_now = utc_now.replace(tzinfo=None) - dt.timedelta(hours=4)
    return ny_now + dt.timedelta(hours=config.BROKER_OFFSET_HOURS)


def broker_epoch(now_utc: float | None = None) -> int:
    """`broker_now()` as the integer epoch MT5 uses for bar stamps."""
    return calendar.timegm(broker_now(now_utc).timetuple())


def is_bar_closed(bar_epoch: int, now_broker_epoch: int) -> bool:
    """A bar stamped T covers [T, T+60s). Closed once that minute has elapsed.

    The safety margin absorbs clock skew between this machine and the broker and
    is one-sided: bars may be accepted late, never early.
    """
    return bar_epoch + 60 + config.FORMING_BAR_SAFETY_SECONDS <= now_broker_epoch


# ---------------------------------------------------------------------------
# Rate-array access (works with or without numpy)
# ---------------------------------------------------------------------------
def _names_of(rates: Iterable) -> tuple | None:
    names = getattr(getattr(rates, "dtype", None), "names", None)
    return tuple(names) if names else None


def _cell(row: Any, names: tuple | None, field_name: str, index: int | None = None):
    """Read one column from a structured-array row, dict row or tuple row."""
    try:
        return row[field_name]           # numpy structured / dict
    except Exception:
        pass
    if index is not None:
        try:
            return row[index]
        except Exception:
            pass
    raise LiveFeedError(f"cannot read field {field_name!r} from MT5 rate row {row!r}")


def rate_row_max_minutes(rates: Any) -> int:
    """Minutes spanned by a rates array, for reporting only."""
    times = [int(_cell(r, _names_of(rates), "time", 0)) for r in rates]
    return 0 if len(times) < 2 else (max(times) - min(times)) // 60


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@dataclass
class RefreshReport:
    """What one poll did. Every counter is logged, none is silently swallowed."""

    rows_seen: int = 0
    new_bars: int = 0
    forming_bars_dropped: int = 0
    duplicate_bars_skipped: int = 0
    off_tick_bars_rejected: int = 0
    ohlc_violations_rejected: int = 0
    unparseable_rows: int = 0
    total_bars: int = 0
    last_bar_time: dt.datetime | None = None
    largest_gap_minutes: int = 0
    retention_pruned: int = 0
    broker_now: dt.datetime | None = None

    @property
    def has_problems(self) -> bool:
        return bool(
            self.off_tick_bars_rejected
            or self.ohlc_violations_rejected
            or self.unparseable_rows
        )

    def summary(self) -> str:
        parts = [
            f"rows={self.rows_seen}",
            f"new={self.new_bars}",
            f"forming_dropped={self.forming_bars_dropped}",
            f"dupes={self.duplicate_bars_skipped}",
            f"total={self.total_bars}",
        ]
        if self.off_tick_bars_rejected:
            parts.append(f"OFF_TICK={self.off_tick_bars_rejected}")
        if self.ohlc_violations_rejected:
            parts.append(f"OHLC_BAD={self.ohlc_violations_rejected}")
        if self.unparseable_rows:
            parts.append(f"UNPARSEABLE={self.unparseable_rows}")
        if self.retention_pruned:
            parts.append(f"pruned={self.retention_pruned}")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------
class LiveBarSource:
    """Rolling M1 `BarSeries` fed from a live MT5 terminal.

    Usage (the runner's shape):

        src = LiveBarSource(mt5, validate=True)   # first pull + invariant gate
        while running:
            report = src.refresh()                # cheap, idempotent
            for window in windows_closed_since(...):
                evaluate_window(src.series, window)
            sleep(poll_seconds)

    `evaluate_window` is a pure function of `(series, window)`, so re-evaluating
    a window as bars arrive is exactly how an open trade resolves. Nothing here
    decides when a window is scored; that stays the caller's rule.
    """

    def __init__(
        self,
        mt5: Any,
        symbol: str = config.INSTRUMENT_SYMBOL,
        *,
        history_bars: int = config.LIVE_HISTORY_BARS,
        retention_bars: int = config.LIVE_RETENTION_BARS,
        validate: bool = True,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._mt5 = mt5
        self.symbol = symbol
        self.history_bars = history_bars
        self.retention_bars = retention_bars
        self.validate = validate
        self._now_fn = now_fn or (lambda: __import__("time").time())
        self._bars: dict[dt.datetime, M1Bar] = {}
        self._symbol_info: dict = {}
        self._session_checks: dict[str, dict] = {}
        #: "passed" | "not_evaluable" | "failed" (failed never returns).
        self.gate_verdict: str = "unknown"
        #: The per-check detail behind `gate_verdict`, for logging and tests.
        self.session_checks: dict[str, dict] = self._session_checks
        #: Boundary checks the fetched window was too shallow to judge.
        self._session_not_evaluable: dict[str, dict] = {}
        self.gate_warnings: list[str] = []
        self.first_raw: dt.datetime | None = None
        self.last_raw: dt.datetime | None = None
        self.reports: list[RefreshReport] = []
        # The initial pull doubles as the invariant gate (TRD §4.1.1 rule 4).
        self._symbol_info = _assert_symbol(mt5, symbol)
        report = self.refresh()
        if not self._bars:
            raise LiveFeedError(
                f"the terminal returned no usable M1 bars for {symbol!r} — "
                "check Market Watch and the symbol history depth"
            )
        if validate:
            self._gate_on_invariants()

    # -- public -----------------------------------------------------------
    @property
    def series(self) -> BarSeries:
        """Current bars as the Phase 1 container. Rebuilt on demand, cheap."""
        return BarSeries(sorted(self._bars.values(), key=lambda b: b.time))

    @property
    def symbol_info(self) -> dict:
        return dict(self._symbol_info)

    @property
    def last_report(self) -> RefreshReport | None:
        return self.reports[-1] if self.reports else None

    def ny_now(self) -> dt.datetime:
        """Current New York wall clock — the clock every strategy rule uses."""
        return to_ny_wall(broker_now(self._now_fn()), CONVENTION_BROKER)

    def last_bar_age_minutes(self) -> float | None:
        """Minutes since the newest bar, in New York wall clock.

        This is the runner's feed-liveness signal: during trading hours it should
        stay near zero; a large value means the feed or the venue is quiet. Note
        it is computed from `ny_now()` (system clock), never from the last tick,
        for the reason in TRD §4.1.1 rule 5.
        """
        if not self._bars:
            return None
        return (self.ny_now() - max(self._bars)).total_seconds() / 60.0

    def refresh(self) -> RefreshReport:
        """Pull the trailing window and merge any closed bars we did not have."""
        rep = RefreshReport()
        now_utc = self._now_fn()
        now_broker_epoch = broker_epoch(now_utc)
        rep.broker_now = broker_now(now_utc)

        rates = self._mt5.copy_rates_from_pos(
            self.symbol, self._mt5.TIMEFRAME_M1, 0, self.history_bars
        )
        if rates is None:
            raise LiveFeedError(
                f"copy_rates_from_pos returned None for {self.symbol!r} "
                f"(last_error={self._mt5.last_error()})"
            )
        if len(rates) == 0:
            # An empty pull is not an error on a closed market, but it must never
            # be mistaken for "no data ever" either: keep the bars we already have.
            rep.total_bars = len(self._bars)
            self.reports.append(rep)
            return rep

        names = _names_of(rates)
        for row in rates:
            rep.rows_seen += 1
            try:
                raw_epoch = int(_cell(row, names, "time", 0))
                o, h, l, c = (
                    Decimal(str(_cell(row, names, key, idx)))
                    for key, idx in (("open", 1), ("high", 2), ("low", 3), ("close", 4))
                )
                volume = int(float(self._volume_of(row, names)))
            except (LiveFeedError, ValueError, TypeError, InvalidOperation) as exc:
                rep.unparseable_rows += 1
                del exc
                continue

            # --- 1. forming bar: never strategy input -----------------------
            if not is_bar_closed(raw_epoch, now_broker_epoch):
                rep.forming_bars_dropped += 1
                continue

            # --- 2. NY wall clock, via the ONE conversion -------------------
            ny_wall = to_ny_wall(epoch_to_label(raw_epoch), CONVENTION_BROKER)

            # --- 3. duplicate poll rows -------------------------------------
            if ny_wall in self._bars:
                rep.duplicate_bars_skipped += 1
                continue

            # --- 4. reject, never repair ------------------------------------
            # A rejected bar stays missing, which the evaluator already handles
            # as incomplete data. Guessing a value would be worse than a gap.
            if h < l or not (l <= o <= h) or not (l <= c <= h):
                rep.ohlc_violations_rejected += 1
                continue
            if any(not is_on_tick_grid(p, config.TICK_SIZE) for p in (o, h, l, c)):
                rep.off_tick_bars_rejected += 1
                continue

            self._bars[ny_wall] = M1Bar(ny_wall, o, h, l, c, volume)
            rep.new_bars += 1
            label = epoch_to_label(raw_epoch)
            if self.first_raw is None or label < self.first_raw:
                self.first_raw = label
            if self.last_raw is None or label > self.last_raw:
                self.last_raw = label

        self._prune(rep)
        rep.total_bars = len(self._bars)
        rep.last_bar_time = max(self._bars) if self._bars else None
        rep.largest_gap_minutes = self._largest_gap_minutes()
        self.reports.append(rep)
        return rep

    # -- internals --------------------------------------------------------
    def _volume_of(self, row: Any, names: tuple | None):
        for candidate in VOLUME_FIELDS:
            try:
                return _cell(row, names, candidate)
            except LiveFeedError:
                continue
        return 0

    def _prune(self, rep: RefreshReport) -> None:
        """Bound memory. Retention must exceed MAX_BARS_TO_RESOLVE_TRADE."""
        if len(self._bars) <= self.retention_bars:
            return
        keep = sorted(self._bars)[-self.retention_bars:]
        cutoff = keep[0]
        dropped = [t for t in self._bars if t < cutoff]
        for t in dropped:
            del self._bars[t]
        rep.retention_pruned = len(dropped)

    def _largest_gap_minutes(self) -> int:
        times = sorted(self._bars)
        if len(times) < 2:
            return 0
        return max(
            int((b - a).total_seconds() // 60) for a, b in zip(times, times[1:])
        )

    def _now_ny(self) -> dt.datetime:
        """Current New York wall clock, naive — the frame the checks are in."""
        from zoneinfo import ZoneInfo

        instant = dt.datetime.fromtimestamp(self._now_fn(), dt.timezone.utc)
        return instant.astimezone(ZoneInfo(config.NY_TZ)).replace(tzinfo=None)

    def _gate_on_invariants(self) -> None:
        """Refuse to run if the live clock cannot be trusted.

        The same four checks the CSV adapter enforces (TRD §4.1.1 rule 4). A feed
        that fails them is either mis-converted or not the instrument we think it
        is; in both cases evaluating windows would produce plausible nonsense.

        Two of the four are *boundary* checks — "the week ends at 16:59 Friday"
        and "it starts at 18:00 Sunday". A trailing window that happens to contain
        no Friday (or no Sunday) cannot satisfy them, and that is a property of
        the sample, not of the feed, so those two are only enforced when the
        sample actually contains such a day. The other two are always enforced.
        """
        series = BarSeries(sorted(self._bars.values(), key=lambda b: b.time))
        now_ny = self._now_ny()
        checks = check_session_invariants(series, now_ny=now_ny)
        self._session_checks = checks
        self.session_checks = checks        # public alias, same dict

        # The two boundary checks can only be judged if the sample actually
        # contains the boundary. Deciding that on "is there any Friday bar" was
        # wrong in both directions, and this gate has already failed live because
        # of it:
        #
        #   * early on a Friday, the only Friday present is the one still in
        #     progress. Its last bar is 05:59, not 16:59, so a completed Friday
        #     that has not happened yet was reported as a Friday that closed
        #     early — a hard crash on a healthy feed.
        #   * on any other weekday a 3000-bar window reaches back about two
        #     trading days, so no Friday was present at all and the check was
        #     skipped in silence. That is the worse failure: it looked like a
        #     pass.
        #
        # So each boundary check is now judged against the instant it was *due*,
        # and a check whose boundary has not been fetched is reported as
        # `not_evaluable` — never as a pass, and never as a crash. A check whose
        # boundary *is* in the sample is judged exactly as before; a real
        # violation still stops the run.
        oldest = series.times[0] if series.times else None
        boundaries = {
            "friday_close_1659_ny": time_windows.most_recent_friday_close(now_ny),
            "sunday_open_1800_ny": time_windows.most_recent_sunday_open(now_ny),
        }

        failed: dict[str, dict] = {}
        not_evaluable: dict[str, dict] = {}
        for name, detail in checks.items():
            if detail["passed"]:
                continue
            if detail.get("evaluable", True) is False:
                # The sample contains no completed instance of the boundary this
                # check is about — no closed Friday, no Sunday session. It cannot
                # speak to it either way, and saying "failed" would be inventing
                # a verdict from missing evidence.
                due = boundaries.get(name)
                not_evaluable[name] = {
                    **detail,
                    "status": "not_evaluable",
                    "due_ny": due.isoformat(sep=" ") if due else None,
                    "oldest_bar_ny": oldest.isoformat(sep=" ") if oldest else None,
                }
            else:
                failed[name] = detail

        self.gate_verdict = (
            "failed" if failed else ("not_evaluable" if not_evaluable else "passed")
        )
        self._session_not_evaluable = not_evaluable
        if failed:
            raise LiveFeedError(
                "live session invariants FAILED — refusing to evaluate windows "
                f"against a feed whose clock cannot be trusted: {failed}. "
                "See docs/02-TRD.md §4.1.1; do not patch the offset to make this pass."
            )
        if not_evaluable:
            # Deliberately not fatal, and deliberately not silent: the window is
            # too shallow to judge this boundary, and the fix is more history,
            # not a different verdict.
            self.gate_warnings = []
            for name in not_evaluable:
                due = boundaries[name]
                self.gate_warnings.append(
                    f"{name}: not evaluable — the fetched window starts at "
                    f"{oldest:%Y-%m-%d %H:%M} NY, after the "
                    f"{due:%Y-%m-%d %H:%M} NY boundary it would have to judge "
                    f"({not_evaluable[name]['detail']}). Deepen history_bars for "
                    f"this check to run.")

    # -- integration with the rest of the engine --------------------------
    def load_report(self) -> LoadReport:
        """The Phase 1 `LoadReport` shape, so live and backtest logs match."""
        series = self.series
        last = self.last_report
        return LoadReport(
            path=f"mt5:{self.symbol}",
            convention=CONVENTION_BROKER,
            timestamp_format="broker_epoch",
            n_bars=len(series),
            first_raw=self.first_raw.isoformat(sep=" ") if self.first_raw else "",
            last_raw=self.last_raw.isoformat(sep=" ") if self.last_raw else "",
            first_ny=series.first_time.isoformat(sep=" "),
            last_ny=series.last_time.isoformat(sep=" "),
            duplicate_timestamps=last.duplicate_bars_skipped if last else 0,
            non_monotonic_timestamps=0,
            ohlc_violations=last.ohlc_violations_rejected if last else 0,
            off_tick_prices=last.off_tick_bars_rejected if last else 0,
            non_positive_volume=0,
            session_checks=dict(self._session_checks),
        )


# ---------------------------------------------------------------------------
# CSV-compatible entry point
# ---------------------------------------------------------------------------
def load_history(
    mt5: Any,
    symbol: str = config.INSTRUMENT_SYMBOL,
    *,
    history_bars: int = config.LIVE_HISTORY_BARS,
    validate: bool = True,
    now_fn: Callable[[], float] | None = None,
) -> tuple[BarSeries, LoadReport]:
    """One-shot snapshot, matching `m1_csv_source.load_bars`'s contract exactly.

    The runner uses `LiveBarSource` for the polling loop; this exists so a
    one-off inspection (or a test) can obtain `(series, report)` the same way
    Phase 1 does, and so the contract is demonstrably interchangeable.
    """
    source = LiveBarSource(
        mt5, symbol, history_bars=history_bars, validate=validate, now_fn=now_fn
    )
    return source.series, source.load_report()
