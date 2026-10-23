"""
Project Chronos — entry / SL / TP / partial trigger (PRD §6.6, §6.7, §6.8)
==========================================================================

SHORT (PREMIUM, Candle B opened above Mid_A):
    entry = High_A - 1 tick
    SL    = High_A + 0.5 * Range          (Range = High_A - Low_A)
    TP    = Mid_A  - 1 tick

LONG (DISCOUNT, Candle B opened below Mid_A):
    entry = Low_A + 1 tick
    SL    = Low_A - 0.5 * Range
    TP    = Mid_A  + 1 tick

Partial-exit trigger — RAW PRICE DISTANCE, no unit conversion (PRD §6.8):
    long : trigger = entry + 0.80 * (TP - entry)
    short: trigger = entry - 0.80 * (entry - TP)

"Handle" never appears in this module. It is a display unit equal to
`1 / tick_size` ticks (10 for NAS100) and is derived at runtime if a report
wants to show it. It is never stored (schema §4.1) and never an input.

R (risk unit) = |entry - SL|, i.e. 0.5 * Range + 1 tick on both sides.
Note the TP distance is `0.5 * Range` = R - 1 tick, so a trade that runs
straight to TP is worth slightly LESS than 1R. That is a property of the
strategy as specified, not an arithmetic bug, and it is why the R-multiple
helpers below never assume "TP == +1R".

All calculations are verified against hand-computed fixtures in
`bot/tests/test_trade_calculator.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .pricing import round_price

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class TradePlan:
    """Everything decided at entry time. Prices are unrounded floats."""

    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    partial_trigger: float
    risk_per_unit: float  # R, in price points
    high_a: float
    low_a: float
    mid_a: float
    candle_a_range: float

    @property
    def tp_distance(self) -> float:
        return abs(self.take_profit - self.entry)

    @property
    def partial_distance(self) -> float:
        return abs(self.partial_trigger - self.entry)

    def handle_distance(self, price: float) -> float:
        """Display-only convenience: price distance expressed in handles.

        Derived from `tick_size` at call time, exactly as PRD §6.8 requires.
        This value must never flow back into any trigger calculation.
        """
        return abs(price - self.entry) / (config.handle_size_in_ticks() * config.TICK_SIZE)

    def rounded(self) -> dict[str, float]:
        return {
            "entry": round_price(self.entry),
            "stop_loss": round_price(self.stop_loss),
            "take_profit": round_price(self.take_profit),
            "partial_trigger": round_price(self.partial_trigger),
            "risk_per_unit": round_price(self.risk_per_unit),
        }


def plan_trade(
    direction: str,
    high_a: float,
    low_a: float,
    mid_a: float,
    tick: float = config.TICK_SIZE,
) -> TradePlan:
    """Compute the full trade plan from Candle A. Raises on a degenerate candle."""
    candle_range = high_a - low_a
    if candle_range <= 0:
        raise ValueError(f"degenerate Candle A: high={high_a} low={low_a} (range must be > 0)")

    if direction == SHORT:
        entry = high_a - tick
        stop_loss = high_a + config.SL_RANGE_MULTIPLE * candle_range
        take_profit = mid_a - tick
        partial_trigger = entry - config.PARTIAL_EXIT_FRACTION * (entry - take_profit)
    elif direction == LONG:
        entry = low_a + tick
        stop_loss = low_a - config.SL_RANGE_MULTIPLE * candle_range
        take_profit = mid_a + tick
        partial_trigger = entry + config.PARTIAL_EXIT_FRACTION * (take_profit - entry)
    else:
        raise ValueError(f"unknown direction {direction!r}")

    # Guard rails: a plan that points the wrong way is a logic bug, not a trade.
    if direction == SHORT:
        if not (stop_loss > entry > take_profit):
            raise ValueError(f"SHORT plan misordered: SL={stop_loss} entry={entry} TP={take_profit}")
        if not (entry >= partial_trigger >= take_profit):
            raise ValueError(f"SHORT partial outside [TP, entry]: {partial_trigger}")
    else:
        if not (stop_loss < entry < take_profit):
            raise ValueError(f"LONG plan misordered: SL={stop_loss} entry={entry} TP={take_profit}")
        if not (entry <= partial_trigger <= take_profit):
            raise ValueError(f"LONG partial outside [entry, TP]: {partial_trigger}")

    risk = abs(entry - stop_loss)
    if risk <= 0:
        raise ValueError("risk per unit must be positive")

    return TradePlan(
        direction=direction,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        partial_trigger=partial_trigger,
        risk_per_unit=risk,
        high_a=high_a,
        low_a=low_a,
        mid_a=mid_a,
        candle_a_range=candle_range,
    )


# ---------------------------------------------------------------------------
# R-multiple (Senior Dev, Q9 — binding formula)
# ---------------------------------------------------------------------------
def signed_move(direction: str, entry: float, price: float) -> float:
    """Direction-signed price move: positive means favourable."""
    return (price - entry) if direction == LONG else (entry - price)


def r_multiple(
    direction: str,
    entry: float,
    risk: float,
    final_exit: float,
    *,
    partial_exit_price: float | None = None,
) -> float:
    """Realised R for the whole position.

    No partial:
        total_R = (exit - entry) / R                     [sign-mirrored for shorts]
    With partial (9 of 10 out at the trigger, 1 runner):
        partial_R = 0.9 * (trigger - entry) / R
        runner_R  = 0.1 * (final_exit - entry) / R
        total_R   = partial_R + runner_R
    """
    if partial_exit_price is None:
        return signed_move(direction, entry, final_exit) / risk

    partial_r = (
        config.CONTRACTS_PARTIAL_EXIT
        / config.CONTRACTS_TOTAL
        * signed_move(direction, entry, partial_exit_price)
        / risk
    )
    runner_r = (
        config.CONTRACTS_RUNNER
        / config.CONTRACTS_TOTAL
        * signed_move(direction, entry, final_exit)
        / risk
    )
    return partial_r + runner_r
