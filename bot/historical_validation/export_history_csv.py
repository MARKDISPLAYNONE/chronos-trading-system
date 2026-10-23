"""
Project Chronos — Historical Data Export Script (Phase 1)
============================================================
Exports NAS100 M1 (1-minute) historical bars from the MT5 demo account
into a CSV file, for use as the Phase 1 backtest input (Coding Agent's
adapter-driven CSV contract, Q3).

Raw timestamps are stored as Unix epoch, interpreted as UTC per the
convention already confirmed and used in verify_broker_timezone.py.
No broker-offset math is applied here — this script just extracts raw
data. The backtest adapter converts to America/New_York at run time.

HOW TO SET YOUR DATE RANGE:
Edit START_DATE and END_DATE below. Requirement: must span at least
one US DST transition date (2nd Sunday March, or 1st Sunday November)
so Phase 1 validates window logic across both EDT and EST periods.
"""

import csv
import os
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

SYMBOL = "NAS100"
TIMEFRAME = mt5.TIMEFRAME_M1
OUTPUT_PATH = "data/nas100_m1_history.csv"

# --------------------------------------------------------------- 
#Picked a ~6 month range that includes a US DST transition date.
# ---------------------------------------------------------------
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 10, 23, tzinfo=timezone.utc)


def main():
    if not mt5.initialize():
        print("ERROR: MT5 initialize() failed:", mt5.last_error())
        return

    if not mt5.symbol_select(SYMBOL, True):
        print(f"ERROR: could not select symbol {SYMBOL} in Market Watch.")
        mt5.shutdown()
        return

    # Widen query slightly to avoid edge truncation, filter precisely after.
    query_start = START_DATE - timedelta(days=2)
    query_end = END_DATE + timedelta(days=2)

    print(f"Requesting {SYMBOL} M1 bars from {query_start} to {query_end} ...")
    rates = mt5.copy_rates_range(SYMBOL, TIMEFRAME, query_start, query_end)

    if rates is None or len(rates) == 0:
        print("ERROR: no rates returned. Check symbol name, date range, and MT5 connection.")
        mt5.shutdown()
        return

    os.makedirs("data", exist_ok=True)

    count = 0
    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_utc", "open", "high", "low", "close", "volume"])
        for bar in rates:
            ts = int(bar["time"])
            bar_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            if bar_dt < START_DATE or bar_dt > END_DATE:
                continue
            writer.writerow([
                ts,
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["tick_volume"],
            ])
            count += 1

    print(f"\nDONE. Exported {count} M1 bars to {OUTPUT_PATH}")
    print(f"Requested range: {START_DATE} to {END_DATE} (UTC)")

    mt5.shutdown()


if __name__ == "__main__":
    main()