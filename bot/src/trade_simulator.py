"""
Project Chronos — deterministic trade simulation (Phase 1 only)
==============================================================

Phase 1 places no orders, so the entry/partial/exit lifecycle is simulated.
This module is the Phase 1 analogue of the live `trade_manager.py` (Phase 3)
and must keep the same rules.

Assumptions, all fixed by Senior Dev decision and stated in the report so
nobody mistakes Phase 1 output for realistic P&L:
  * zero spread, zero slippage, zero commission (Q8);
  * entry fills exactly at the computed trigger price (PRD §6.6);
  * exits fill exactly at SL / TP / partial-trigger price (Q8);
  * no gap fills are modelled — a weekend gap through the stop is scored at
    the stop price, which is optimistic; affected trades are counted and
    disclosed in the report rather than hidden.

Intrabar ordering (Q7) — pessimistic, applied to every bar:
    1. stop loss is checked first, for the whole open position;
    2. take profit only if the stop was not touched in that bar;
    3. the 80% partial only if neither SL nor TP was touched in that bar.
So one bar spanning both the 80% trigger and the SL resolves as a LOSS.

Two deliberate consequences worth naming:
  * the trigger bar counts as an open-position bar, so a trigger bar whose
    range already contains the stop is a LOSS. We entered somewhere inside
    that bar and cannot know the order, so we take the worst case;
  * after the 9-contract partial fires inside a bar, the runner's breakeven
    stop is also tested against that same bar. If the bar also traded back to
    entry, the runner is stopped out. This is pessimistic in the same spirit;
    the report counts how often it actually occurs.

A trade is NOT bounded by its window: the live manager holds a position until
it closes, so the simulation keeps scanning forward across the window
boundary (and across session breaks, which are counted).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .bars import BarSeries
from .pricing import ge, le, round_price
from .trade_calculator import LONG, SHORT, TradePlan, r_multiple

# Controlled vocabulary (schema §8)
REASON_TP = "take profit hit"
REASON_SL = "stop loss hit"
REASON_BREAKEVEN_AFTER_PARTIAL = "breakeven stop hit after partial"
REASON_TP_AFTER_PARTIAL = "target hit after partial"
REASON_UNRESOLVED = "data_ends_before_trade_closed"

WIN = "WIN"
LOSS = "LOSS"


@dataclass
class TradeOutcome:
    resolved: bool
    final_outcome: str
    reason: str
    partial_triggered: bool
    partial_exit_time: dt.datetime | None
    partial_exit_price: float | None
    breakeven_sl_price: float | None
    final_exit_time: dt.datetime | None
    final_exit_price: float | None
    exit_kind: str | None
    result_r_multiple: float
    bars_held: int
    open_through_session_break: bool
    runner_stopped_in_partial_bar: bool


def simulate(
    plan: TradePlan,
    series: BarSeries,
    entry_time: dt.datetime,
    max_bars: int,
) -> TradeOutcome:
    """Walk forward from the trigger bar until the position is flat."""
    start_index = series.index_of(entry_time)
    if start_index is None:
        raise ValueError(f"entry bar {entry_time} is not in the series")

    entry = plan.entry
    stop = plan.stop_loss
    partial_fired = False
    partial_time: dt.datetime | None = None
    partial_price: float | None = None
    #: set when the 9 lots were closed this bar and the runner was then stopped
    #: inside the same bar (pessimistic Q7 reading)
    runner_stopped_in_partial_bar = False
    bars_held = 0
    prev_time: dt.datetime | None = None
    crossed_break = False

    for bar in series.iter_from(start_index, limit=max_bars):
        bars_held += 1
        if prev_time is not None and (bar.time - prev_time) > dt.timedelta(minutes=2):
            # a >2 minute hole means a session break or a data gap was crossed
            # while the position was open
            crossed_break = True
        prev_time = bar.time

        if plan.direction == LONG:
            hit_stop = le(bar.low, stop)
            hit_tp = ge(bar.high, plan.take_profit)
            hit_partial = (not partial_fired) and ge(bar.high, plan.partial_trigger)
        else:  # SHORT
            hit_stop = ge(bar.high, stop)
            hit_tp = le(bar.low, plan.take_profit)
            hit_partial = (not partial_fired) and le(bar.low, plan.partial_trigger)

        # 1. Stop first — always, and for whatever is currently open.
        if hit_stop:
            kind = "breakeven" if partial_fired else "sl"
            return _finish(
                plan=plan,
                partial_fired=partial_fired,
                partial_time=partial_time,
                partial_price=partial_price,
                exit_time=bar.time,
                exit_price=stop,
                exit_kind=kind,
                bars_held=bars_held,
                crossed_break=crossed_break,
                runner_stopped=runner_stopped_in_partial_bar,
            )

        # 2. Take profit.
        if hit_tp:
            return _finish(
                plan=plan,
                partial_fired=partial_fired,
                partial_time=partial_time,
                partial_price=partial_price,
                exit_time=bar.time,
                exit_price=plan.take_profit,
                exit_kind="tp",
                bars_held=bars_held,
                crossed_break=crossed_break,
                runner_stopped=runner_stopped_in_partial_bar,
            )

        # 3. Partial exit (9 of 10), then the runner moves to breakeven.
        if hit_partial:
            partial_fired = True
            partial_time = bar.time
            partial_price = plan.partial_trigger
            stop = entry  # breakeven on the remaining contract

            # Pessimistic: the runner existed from somewhere inside this bar.
            # If the bar also traded back to entry, take the stop.
            if plan.direction == LONG:
                runner_hit = le(bar.low, entry)
            else:
                runner_hit = ge(bar.high, entry)
            if runner_hit:
                runner_stopped_in_partial_bar = True
                return _finish(
                    plan=plan,
                    partial_fired=True,
                    partial_time=partial_time,
                    partial_price=partial_price,
                    exit_time=bar.time,
                    exit_price=entry,
                    exit_kind="breakeven",
                    bars_held=bars_held,
                    crossed_break=crossed_break,
                    runner_stopped=True,
                )

    # Ran out of data (or hit the cap) with the position still open.
    return TradeOutcome(
        resolved=False,
        final_outcome=WIN,  # placeholder; the evaluator refuses to score these
        reason=REASON_UNRESOLVED,
        partial_triggered=partial_fired,
        partial_exit_time=partial_time,
        partial_exit_price=partial_price,
        breakeven_sl_price=entry if partial_fired else None,
        final_exit_time=None,
        final_exit_price=None,
        exit_kind=None,
        result_r_multiple=0.0,
        bars_held=bars_held,
        open_through_session_break=crossed_break,
        runner_stopped_in_partial_bar=runner_stopped_in_partial_bar,
    )


def _finish(
    *,
    plan: TradePlan,
    partial_fired: bool,
    partial_time: dt.datetime | None,
    partial_price: float | None,
    exit_time: dt.datetime,
    exit_price: float,
    exit_kind: str,
    bars_held: int,
    crossed_break: bool,
    runner_stopped: bool,
) -> TradeOutcome:
    if partial_fired:
        total_r = r_multiple(
            plan.direction,
            plan.entry,
            plan.risk_per_unit,
            exit_price,
            partial_exit_price=partial_price,
        )
    else:
        total_r = r_multiple(plan.direction, plan.entry, plan.risk_per_unit, exit_price)

    if exit_kind == "tp":
        reason = REASON_TP_AFTER_PARTIAL if partial_fired else REASON_TP
    elif exit_kind == "breakeven":
        reason = REASON_BREAKEVEN_AFTER_PARTIAL
    else:
        reason = REASON_SL

    return TradeOutcome(
        resolved=True,
        final_outcome=WIN if total_r > 0 else LOSS,
        reason=reason,
        partial_triggered=partial_fired,
        partial_exit_time=partial_time,
        partial_exit_price=partial_price,
        breakeven_sl_price=plan.entry if partial_fired else None,
        final_exit_time=exit_time,
        final_exit_price=exit_price,
        exit_kind=exit_kind,
        result_r_multiple=total_r,
        bars_held=bars_held,
        open_through_session_break=crossed_break,
        runner_stopped_in_partial_bar=runner_stopped,
    )


def format_price(p: float | None) -> str:
    return "" if p is None else f"{round_price(p):.5f}".rstrip("0").rstrip(".")
