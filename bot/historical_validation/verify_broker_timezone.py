import MetaTrader5 as mt5
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

if not mt5.initialize():
    print("MT5 initialize failed:", mt5.last_error())
    quit()

# IMPORTANT: use the EXACT symbol string as it appears in MT5's Market Watch
# (confirm this matches your broker's API symbol — may differ from display name)
SYMBOL = "NAS100"  # adjust if your API symbol string differs

rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 5)
if rates is None:
    print("Failed to get rates:", mt5.last_error())
    quit()

print(f"{'Raw Epoch':<12} {'As UTC':<20} {'As Nairobi':<20}")
print("-" * 55)
for r in rates:
    raw_ts = r['time']
    as_utc = datetime.fromtimestamp(raw_ts, tz=ZoneInfo("UTC"))
    as_nairobi = as_utc.astimezone(ZoneInfo("Africa/Nairobi"))
    print(f"{raw_ts:<12} {as_utc.strftime('%Y-%m-%d %H:%M'):<20} {as_nairobi.strftime('%Y-%m-%d %H:%M'):<20}")

print("\nNow open the MT5 terminal, look at the SAME bars on a NAS100 H1")
print("chart, and note what time MT5 displays for the most recent bar.")
print("Compare that displayed time against the 'As UTC' and 'As Nairobi'")
print("columns above. Whichever one MATCHES is your broker's timezone basis.")

mt5.shutdown()