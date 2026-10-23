"""
Project Chronos — entry / SL / TP / partial trigger (PRD §6.6–§6.8, TRD §4.1.2)
===============================================================================

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

QUANTIZATION (Phase 1 correction — see pricing.py for the full rationale)
------------------------------------------------------------------------
`SL`, `TP` and `partial_trigger` are computed exactly, then snapped to the
instrument's tick grid, directionally:

    SHORT -> floor,  LONG -> ceiling

This moves every price in the direction that hurts us (tighter stop, further
target, harder-to-reach partial) and never in our favour. `entry` is already
on the grid (High_A / Low_A are traded prices, ± one tick), and `Mid_A` /
`Range` are deliberately left UNQUANTIZED so context classification is exact.
Roughly 49% of trades were affected; without this the engine emitted stops
like 25420.75, which no broker can accept.

`R = |entry - SL|` is measured AFTER quantization, because the stop that
actually sits in the market is the real risk.

"Handle" never appears in this module: it is a display unit equal to
`1 / tick_size` ticks, derived at runtime, never stored (schema §4.1) and
never an input to any of this.

All calculations are verified against hand-computed fixtures in
`bot/tests/test_trade_calculator.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from . import config
from .pricing import D, direction_for, quantize_to_tick, round_price

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class TradePlan:
    """Everything decided at entry time. Prices are exact Decimals."""

    direction: str
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    partial_trigger: Decimal
    risk_per_unit: Decimal  # R, in price points, measured post-quantization
    high_a: Decimal
    low_a: Decimal
    mid_a: Decimal
    candle_a_range: Decimal

    #: pre-quantization values, kept for the audit trail so a reviewer can see
    #: exactly what the tick snap changed. Never used for execution or scoring.
    raw_stop_loss: Decimal | None = None
    raw_take_profit: Decimal | None = None
    raw_partial_trigger: Decimal | None = None

    @property
    def tp_distance(self) -> Decimal:
        return abs(self.take_profit - self.entry)

    @property
    def partial_distance(self) -> Decimal:
        return abs(self.partial_trigger - self.entry)

    @property
    def quantization_direction(self) -> str:
        return direction_for(self.direction)

    @property
    def quantized(self) -> bool:
        """True when the tick snap actually moved any order price."""
        if self.raw_stop_loss is None:
            return False
        return (
            self.raw_stop_loss != self.stop_loss
            or self.raw_take_profit != self.take_profit
            or self.raw_partial_trigger != self.partial_trigger
        )

    def handle_distance(self, price) -> Decimal:
        """Display-only convenience: price distance in handles.

        Derived from `tick_size` at call time, exactly as PRD §6.8 requires.
        Must never flow back into any trigger calculation.
        """
        unit = config.handle_size_in_ticks() * config.TICK_SIZE
        return abs(D(price) - self.entry) / unit

    def rounded(self) -> dict[str, Decimal]:
        return {
            "entry": round_price(self.entry),
            "stop_loss": round_price(self.stop_loss),
            "take_profit": round_price(self.take_profit),
            "partial_trigger": round_price(self.partial_trigger),
            "risk_per_unit": round_price(self.risk_per_unit),
        }


def plan_trade(
    direction: str,
    high_a,
    low_a,
    mid_a,
    tick=config.TICK_SIZE,
) -> TradePlan:
    """Compute the full trade plan from Candle A, then snap it to the tick grid."""
    high_a, low_a, mid_a, tick = D(high_a), D(low_a), D(mid_a), D(tick)
    candle_range = high_a - low_a
    if candle_range <= 0:
        raise ValueError(f"degenerate Candle A: high={high_a} low={low_a} (range must be > 0)")

    if direction == SHORT:
        entry = high_a - tick
        raw_sl = high_a + config.SL_RANGE_MULTIPLE * candle_range
        raw_tp = mid_a - tick
        raw_partial = entry - config.PARTIAL_EXIT_FRACTION * (entry - raw_tp)
    elif direction == LONG:
        entry = low_a + tick
        raw_sl = low_a - config.SL_RANGE_MULTIPLE * candle_range
        raw_tp = mid_a + tick
        raw_partial = entry + config.PARTIAL_EXIT_FRACTION * (raw_tp - entry)
    else:
        raise ValueError(f"unknown direction {direction!r}")

    # ---- quantize the three order prices, pessimistically -------------------
    qdir = direction_for(direction)
    stop_loss = quantize_to_tick(raw_sl, tick, qdir)
    take_profit = quantize_to_tick(raw_tp, tick, qdir)
    partial_trigger = quantize_to_tick(raw_partial, tick, qdir)

    # Quantization may push the partial past the target (possible when the
    # TP distance is itself only a tick or two). In that case the partial can
    # never fire before TP, so pin it to TP rather than leaving an
    # out-of-range value. TP is checked before the partial in the simulator,
    # so behaviour is identical either way — this just keeps the plan valid.
    if direction == SHORT:
        if partial_trigger < take_profit:
            partial_trigger = take_profit
        if partial_trigger > entry:
            partial_trigger = entry
    else:
        if partial_trigger > take_profit:
            partial_trigger = take_profit
        if partial_trigger < entry:
            partial_trigger = entry

    # Guard rails: a plan that points the wrong way is a logic bug, not a trade.
    if direction == SHORT:
        if not (stop_loss > entry > take_profit):
            raise ValueError(
                f"SHORT plan misordered after quantization: "
                f"SL={stop_loss} entry={entry} TP={take_profit}"
            )
    else:
        if not (stop_loss < entry < take_profit):
            raise ValueError(
                f"LONG plan misordered after quantization: "
                f"SL={stop_loss} entry={entry} TP={take_profit}"
            )

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
        raw_stop_loss=raw_sl,
        raw_take_profit=raw_tp,
        raw_partial_trigger=raw_partial,
    )


# ---------------------------------------------------------------------------
# R-multiple (Senior Dev, Q9 — binding formula)
# ---------------------------------------------------------------------------
def signed_move(direction: str, entry, price) -> Decimal:
    """Direction-signed price move: positive means favourable."""
    e, p = D(entry), D(price)
    return (p - e) if direction == LONG else (e - p)


def r_multiple(
    direction: str,
    entry,
    risk,
    final_exit,
    *,
    partial_exit_price=None,
) -> Decimal:
    """Realised R for the whole position.

    No partial:
        total_R = (exit - entry) / R                     [sign-mirrored for shorts]
    With partial (9 of 10 out at the trigger, 1 runner):
        partial_R = 0.9 * (trigger - entry) / R
        runner_R  = 0.1 * (final_exit - entry) / R
        total_R   = partial_R + runner_R
    """
    entry, risk = D(entry), D(risk)
    if partial_exit_price is None:
        return signed_move(direction, entry, final_exit) / risk

    partial_r = (
        D(config.CONTRACTS_PARTIAL_EXIT)
        / D(config.CONTRACTS_TOTAL)
        * signed_move(direction, entry, partial_exit_price)
        / risk
    )
    runner_r = (
        D(config.CONTRACTS_RUNNER)
        / D(config.CONTRACTS_TOTAL)
        * signed_move(direction, entry, final_exit)
        / risk
    )
    return partial_r + runner_r
