"""
Project Chronos — Broker Timezone Verification Script
========================================================

PURPOSE
-------
Confirms how MT5 (Forex.com Global / GAIN Capital broker feed) timestamps
relate to actual New York local time, which is the ONLY timezone the
Zeussy Macro Time strategy logic cares about (all 21 daily windows are
defined in NY local time per docs/01-PRD.md Section 6).

CONFIRMED FINDING (see docs/02-TRD.md Section 4.1.1 for full writeup):
-----------------------------------------------------------------------
Forex.com / GAIN Capital's broker server clock runs on what the trading
community calls "NY+7": the broker server time is ALWAYS exactly 7 hours
ahead of New York LOCAL time, and it follows **US DST transition dates**
(2nd Sunday in March -> 1st Sunday in November), not EU DST dates.

    - During US Daylight Time (EDT, UTC-4): broker time = UTC+3
    - During US Standard Time (EST, UTC-5): broker time = UTC+2

This means the broker's UTC offset itself SHIFTS on US DST dates, even
though "NY+7" never changes. An earlier working theory (now disproven)
assumed the broker ran on a fixed Africa/Nairobi (UTC+3, no DST) clock.
That theory happened to produce correct-looking results when tested
during US DST, but would have silently broken after the Nov 1 2026 US
DST rollback (broker -> UTC+2, Nairobi stays UTC+3). DO NOT use a fixed
UTC+3 or Nairobi assumption anywhere in this codebase.

HARD RULE FOR ALL STRATEGY LOGIC
---------------------------------
Strategy code must NEVER reference broker server time or any hardcoded
hour offset. It must only ever convert raw UTC timestamps (what MT5's
`time` field actually represents, in Unix epoch) into
`America/New_York` using a proper DST-aware timezone library
(`zoneinfo` or `pytz`). The "Broker Display" column in this script exists
ONLY for human cross-checking against the MT5 terminal's own chart —
it is a debug convenience, never a logic dependency.

HOW TO USE THIS SCRIPT
-----------------------
1. Run it while MT5 terminal is open and logged into the demo account.
2. Compare the "As Broker Display (NY+7)" column against the timestamp
   shown on the same bar in MT5's own H1 chart for NAS100. They should
   match exactly.
3. Compare the "As NY (strategy uses this)" column against what you know
   to be true NY local time for that bar. This is the value the trading
   logic will use for all window boundary calculations.
4. RE-RUN THIS SCRIPT after Nov 1, 2026 (US DST end date) and again after
   the next US DST start date (2nd Sunday March 2027) to reconfirm the
   NY+7 relationship continues to hold after each transition. If it does
   not, STOP all bot development and escalate to Handler/Senior Dev
   immediately — the entire timezone assumption would need re-validation.

"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import MetaTrader5 as mt5

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
SYMBOL = "NAS100"           # Confirmed API symbol string (docs/05-DATABASE-SCHEMA.md)
TIMEFRAME = mt5.TIMEFRAME_H1
NUM_BARS = 10                # How many most-recent bars to inspect

NY_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

BROKER_OFFSET_HOURS = 7      # "NY+7" — for DEBUG DISPLAY ONLY. Never used in strategy logic.


def broker_display_time(ny_time: datetime) -> datetime:
    """
    DEBUG / CROSS-REFERENCE ONLY.

    Computes what the broker's own server clock would show for a given
    NY local time, using the confirmed NY+7 convention. This exists
    solely so a human can visually cross-check this script's output
    against the MT5 terminal's own chart timestamps.

    STRATEGY LOGIC MUST NEVER CALL THIS FUNCTION. All window
    calculations, context determination, sweep/reversal detection, and
    SL/TP logic must operate on `as_ny`, never on this value.
    """
    return ny_time + timedelta(hours=BROKER_OFFSET_HOURS)


def main():
    if not mt5.initialize():
        print(f"ERROR: MT5 initialize() failed. Error code: {mt5.last_error()}")
        return

    try:
        rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, NUM_BARS)

        if rates is None or len(rates) == 0:
            print(f"ERROR: No rates returned for symbol '{SYMBOL}'. "
                  f"Check symbol is correct and visible in Market Watch.")
            return

        print(f"\nBroker Timezone Verification — Symbol: {SYMBOL}  Timeframe: H1")
        print(f"Confirmed convention: Broker = NY local time + 7 hours, follows US DST dates.")
        print("=" * 110)
        header = (
            f"{'Raw Epoch':>12} | "
            f"{'As UTC':>20} | "
            f"{'As NY (strategy uses this)':>27} | "
            f"{'As Broker Display (NY+7, debug only)':>37}"
        )
        print(header)
        print("-" * 110)

        for bar in rates:
            raw_epoch = int(bar["time"])

            # MT5 raw epoch value is timezone-agnostic Unix time.
            # Interpret as UTC first — this is the only safe universal anchor.
            as_utc = datetime.fromtimestamp(raw_epoch, tz=UTC_TZ)

            # Convert to NY local time using a proper DST-aware library.
            # THIS is the value all strategy logic (window boundaries,
            # Candle A / Candle B timing, etc.) must use.
            as_ny = as_utc.astimezone(NY_TZ)

            # Debug-only broker display reconstruction for human cross-check
            # against the MT5 terminal's own chart.
            as_broker_display = broker_display_time(as_ny.replace(tzinfo=None))

            print(
                f"{raw_epoch:>12} | "
                f"{as_utc.strftime('%Y-%m-%d %H:%M:%S %Z'):>20} | "
                f"{as_ny.strftime('%Y-%m-%d %H:%M:%S %Z'):>27} | "
                f"{as_broker_display.strftime('%Y-%m-%d %H:%M:%S'):>37} (NY+7, debug only)"
            )

        print("=" * 110)
        print(
            "\nCROSS-CHECK INSTRUCTIONS:\n"
            "  1. Open the MT5 terminal, view NAS100 H1 chart.\n"
            "  2. Confirm the 'As Broker Display (NY+7)' column above matches\n"
            "     the timestamps shown directly on the MT5 chart for these bars.\n"
            "  3. The 'As NY' column is what all strategy/window logic will use.\n"
            "     It must represent true, correct New York local time.\n"
            "  4. Re-run this script after each US DST transition date to\n"
            "     reconfirm the NY+7 relationship still holds. If it breaks,\n"
            "     escalate immediately — do not patch with a new hardcoded offset.\n"
        )

    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
