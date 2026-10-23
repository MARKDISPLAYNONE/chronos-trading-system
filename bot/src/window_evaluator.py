"""
Project Chronos — per-window evaluation (PRD §6.4-§6.8, schema §8 / §8.1)
=========================================================================

Pure function of (window, bar series) -> one evaluation record. No I/O, no
clock, no randomness: the same inputs always produce the same row. That is
what makes the Phase 1 report reproducible and the exit criterion ("exactly
one outcome per window") checkable.

Decision order, matching docs/03-APP-FLOW.md §2.3:

    1. no market data for the window          -> NO_SETUP / market_closed_no_data
    2. data short of a full window            -> NO_SETUP / market_closed_no_data
                                                 (session ended) or
                                                 NO_SETUP / missing_market_data
                                                 (a hole while the market traded)
    3. classify Candle B open                 -> VOID -> NO_SETUP / invalid open location
    4. sweep + reversal across Candle B       -> NO_SETUP / "no sweep" | "sweep not reversed"
                                                 | "ambiguous_same_bar_insufficient_granularity"
    5. plan the trade, simulate it forward    -> WIN | LOSS with an exit reason
    6. position never closed before data ends -> MISSING_EVALUATION /
                                                 data_ends_before_trade_closed
                                                 (never silently scored)

Reason 6 is deliberately NOT treated as a trade outcome: scoring it would
mean inventing an exit. MISSING_EVALUATION is the schema value for "this
window could not be evaluated", and the reason string says why. In the Phase 1
dataset this path is expected to fire zero times; the report states whether it
did.
"""

from __future__ import annotations

import bisect
import datetime as dt
from dataclasses import dataclass, field

from . import config
from .bars import BarSeries, M1Bar
from .candle_tracker import Candle, build_candle
from .context_classifier import Context, classify
from .sweep_detector import LONG, SHORT, SweepResult, detect
from .time_windows import Window
from .trade_calculator import TradePlan, plan_trade
from .trade_simulator import TradeOutcome, simulate

# --- controlled vocabulary, extended per Q10 / schema §8 --------------------
REASON_MARKET_CLOSED = "market_closed_no_data"
REASON_MISSING_DATA = "missing_market_data"
REASON_OUTSIDE_RANGE = "outside_data_range"
REASON_NONEXISTENT_LOCAL_TIME = "nonexistent_local_time_dst"
REASON_DATA_ENDS = "data_ends_before_trade_closed"

#: A hole of at least this length is treated as a structural market closure
#: (holiday break, half-day, weekend) rather than a feed hiccup. 60 minutes is
#: the length of the daily maintenance break, i.e. the shortest closure this
#: venue actually has; the longest genuine feed hole in the Phase 1 file is
#: 45 minutes. Both are measured, not assumed — see the report.
STRUCTURAL_GAP_MINUTES = 60

OUTCOME_WIN = "WIN"
OUTCOME_LOSS = "LOSS"
OUTCOME_NO_SETUP = "NO_SETUP"
OUTCOME_MISSING = "MISSING_EVALUATION"


@dataclass
class WindowEvaluation:
    """One row of `window_evaluations` plus the audit trail for the report."""

    window: Window
    context: str
    outcome: str
    reason: str

    candle_a: Candle | None = None
    candle_b: Candle | None = None

    sweep: SweepResult | None = None
    plan: TradePlan | None = None
    trade: TradeOutcome | None = None

    # audit-only fields (NOT part of the schema mirror)
    bars_a: int = 0
    bars_b: int = 0
    flags: list[str] = field(default_factory=list)

    @property
    def trading_date(self) -> dt.date:
        return self.window.trading_date

    @property
    def window_label(self) -> str:
        return self.window.label

    @property
    def traded(self) -> bool:
        return self.trade is not None and self.trade.resolved


def _classify_missing_data(series: BarSeries, start: dt.datetime, end: dt.datetime) -> str:
    """Why is this window incomplete? Three genuinely different answers.

    * `outside_data_range` — the file simply stops before this window. Not a
      market fact and not a strategy fact; the export boundary.
    * `market_closed_no_data` — the venue was shut (holiday break, half-day,
      Friday evening, weekend). An environmental fact, per the Q10 ruling.
    * `missing_market_data` — bars are absent while the venue was trading.
      A feed-quality artefact that the report must own rather than bury.

    Distinguishing them cannot be done from the window alone: on the four US
    holiday Mondays the session closes 13:00 and reopens 18:00, so the missing
    minutes at 13:00 look identical to a feed hole from inside the window.
    The discriminator is the length of the gap that contains the missing
    stretch — measured between the bars immediately bracketing it.
    """
    if end > series.last_time:
        return REASON_OUTSIDE_RANGE

    times = series.times
    # Walk the expected minutes of the window and collect maximal runs that
    # have no bar; classify each run by the gap around it.
    missing_run_start: dt.datetime | None = None
    structural = False
    saw_missing = False

    def close_run(run_start: dt.datetime | None, run_end: dt.datetime) -> bool:
        """Does this missing run sit inside a structural closure?"""
        if run_start is None:
            return False
        li = bisect.bisect_left(times, run_start) - 1
        ri = bisect.bisect_left(times, run_end)
        if ri >= len(times):
            return True  # nothing after: the venue stopped, not a hole
        if li < 0:
            # Nothing traded before this run anywhere in the file: the venue
            # had not opened yet (e.g. the 2026-01-01 New Year reopen at
            # 18:00 NY). That is a closure, not a feed hole. It is also the
            # one place the two are genuinely indistinguishable, so the choice
            # is stated here rather than left implicit.
            return True
        left = times[li]
        right = times[ri]
        return (right - left) >= dt.timedelta(minutes=STRUCTURAL_GAP_MINUTES)

    minute = start
    while minute < end:
        if series.index_of(minute) is None:
            saw_missing = True
            if missing_run_start is None:
                missing_run_start = minute
        else:
            if missing_run_start is not None:
                structural = close_run(missing_run_start, minute) or structural
                missing_run_start = None
        minute += dt.timedelta(minutes=1)
    if missing_run_start is not None:
        structural = close_run(missing_run_start, end) or structural

    if not saw_missing:
        return REASON_MISSING_DATA  # defensive; caller only calls this when incomplete
    return REASON_MARKET_CLOSED if structural else REASON_MISSING_DATA


def evaluate_window(series: BarSeries, window: Window, tick: float = config.TICK_SIZE) -> WindowEvaluation:
    """Evaluate one window end-to-end. Always returns exactly one record."""
    start = window.start
    end = window.end

    # --- 1/2. data availability -------------------------------------------
    if not series.bars_between(start, end):
        return WindowEvaluation(
            window=window,
            context=Context.VOID.value,
            outcome=OUTCOME_NO_SETUP,
            reason=_classify_missing_data(series, start, end),
            flags=["no_bars_in_window"],
        )

    candle_a = build_candle(series, start, 15)
    candle_b = build_candle(series, start + dt.timedelta(minutes=15), 15)

    bars_a = len(candle_a.bars) if candle_a else 0
    bars_b = len(candle_b.bars) if candle_b else 0

    if candle_a is None or candle_b is None or not candle_a.complete or not candle_b.complete:
        reason = _classify_missing_data(series, start, end)
        return WindowEvaluation(
            window=window,
            context=Context.VOID.value,
            outcome=OUTCOME_NO_SETUP,
            reason=reason,
            candle_a=candle_a,
            candle_b=candle_b,
            bars_a=bars_a,
            bars_b=bars_b,
            flags=["incomplete_candle"],
        )

    high_a, low_a, mid_a = candle_a.high, candle_a.low, candle_a.mid

    # --- 3. context --------------------------------------------------------
    classification = classify(candle_b.open, low_a, mid_a, high_a)
    if classification.is_void:
        return WindowEvaluation(
            window=window,
            context=classification.context.value,
            outcome=OUTCOME_NO_SETUP,
            reason=classification.reason or "invalid open location",
            candle_a=candle_a,
            candle_b=candle_b,
            bars_a=bars_a,
            bars_b=bars_b,
        )

    direction = SHORT if classification.context is Context.PREMIUM else LONG

    # --- 4. sweep + reversal ----------------------------------------------
    sweep = detect(direction, list(candle_b.bars), high_a, low_a, tick)
    if not sweep.triggered:
        return WindowEvaluation(
            window=window,
            context=classification.context.value,
            outcome=OUTCOME_NO_SETUP,
            reason=sweep.reason or "no sweep",
            candle_a=candle_a,
            candle_b=candle_b,
            sweep=sweep,
            bars_a=bars_a,
            bars_b=bars_b,
        )

    # --- 5. plan + simulate ------------------------------------------------
    plan = plan_trade(direction, high_a, low_a, mid_a, tick)
    assert sweep.trigger_time is not None
    trade = simulate(plan, series, sweep.trigger_time, config.MAX_BARS_TO_RESOLVE_TRADE)

    flags: list[str] = []
    if trade.open_through_session_break:
        flags.append("open_through_session_break")
    if trade.runner_stopped_in_partial_bar:
        flags.append("runner_stopped_in_partial_bar")

    if not trade.resolved:
        return WindowEvaluation(
            window=window,
            context=classification.context.value,
            outcome=OUTCOME_MISSING,
            reason=REASON_DATA_ENDS,
            candle_a=candle_a,
            candle_b=candle_b,
            sweep=sweep,
            plan=plan,
            trade=trade,
            bars_a=bars_a,
            bars_b=bars_b,
            flags=flags,
        )

    return WindowEvaluation(
        window=window,
        context=classification.context.value,
        outcome=trade.final_outcome,
        reason=trade.reason,
        candle_a=candle_a,
        candle_b=candle_b,
        sweep=sweep,
        plan=plan,
        trade=trade,
        bars_a=bars_a,
        bars_b=bars_b,
        flags=flags,
    )
