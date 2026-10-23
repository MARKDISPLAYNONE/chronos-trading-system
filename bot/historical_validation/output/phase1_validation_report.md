# Project Chronos — Phase 1 Validation Report

**Generated:** 2026-10-23 18:36:21 (local)  
**Runtime:** 14.1s  
**Input:** `data/nas100_m1_history.csv`  
**Instrument:** NAS100 (tick 0.1)  
**Scope:** Phase 1 — logic validation on historical M1 data. **No orders were placed, no broker was contacted, no database was used.**

---

## 1. Critical finding — the timestamp convention in the export

`docs/02-TRD.md` §4.1.1 states that MT5's raw epoch is true UTC and that the NY+7 broker offset is a display artifact only. **That is not what this export contains.** The CSV stores broker server time encoded as if it were UTC, i.e. `NY wall clock = utc_label(epoch) - 7h`.

Evidence — each candidate convention scored against physical session invariants. A wrong convention cannot satisfy them, because an exchange does not move its session when a timezone changes:

| Convention | Check | Result |
|---|---|---|
| `broker` | no_saturday_bars | PASS — 0 Saturday bars |
| `broker` | daily_break_1700_1800_ny | PASS — 0 Mon-Thu bars inside 17:00-18:00 NY |
| `broker` | friday_close_1659_ny | PASS — 39/42 Fridays close 16:59, 0 bars after 16:59 |
| `broker` | sunday_open_1800_ny | PASS — 42/42 Sundays open 18:00, 0 bars before 18:00 |
| `utc` | no_saturday_bars | PASS — 0 Saturday bars |
| `utc` | daily_break_1700_1800_ny | **FAIL** — 9840 Mon-Thu bars inside 17:00-18:00 NY |
| `utc` | friday_close_1659_ny | **FAIL** — 0/42 Fridays close 16:59, 6420 bars after 16:59 |
| `utc` | sunday_open_1800_ny | **FAIL** — 0/42 Sundays open 18:00, 0 bars before 18:00 |

Selected by the adapter: **`broker`**. Load report: 287,746 bars, 2026-01-01 18:00:00 → 2026-10-22 16:59:00 (**New York wall clock**).

Corroborating signals, all independent of the above:

1. **Intraday volume.** In July (unambiguously EDT) average tick-volume peaks at raw labels 16:00–22:00. The US cash session (09:30–16:00 ET) is raw 16:30–23:00 under NY+7 — and raw 13:00–14:00, where the UTC reading would put the open, is one of the quietest hours in the file.
2. **The strategy's own skip set.** The three excluded windows (15:45–18:15) bracket a 17:00–18:00 break. Under NY+7 that break is exactly 17:00–18:00 every day; under the UTC reading it would sit at 19:00–20:00 and the skip set would be unexplained.
3. **Holiday reopen.** The file's first bar is 2026-01-01 18:00 NY (the New Year's Day reopen) and Fridays end 16:59 NY year-round.

**Status: RESOLVED.** The finding above was escalated to the Senior Dev, who instructed the correction. `docs/02-TRD.md` §4.1.1 has been rewritten to state the constant-offset rule, and `bot/historical_validation/verify_broker_timezone.py` now implements it and reproduces the invariant checks below offline (`--csv ../data/nas100_m1_history.csv`). The adapter still validates the convention at load time and refuses to run if the data disagrees, so this cannot silently produce a plausible-looking but shifted backtest.

---

## 2. Assumptions and limitations (read before using any number below)

Phase 1 answers one question: **is the logic correct?** It does not model execution. Every simplification is stated here so these R-multiples are never mistaken for live expectations.

| # | Assumption | Source |
|---|---|---|
| 1 | Zero spread, zero slippage, zero commission. Entry fills exactly at `High_A − 1 tick` / `Low_A + 1 tick`; exits fill exactly at SL / TP / partial-trigger price. | Q8 |
| 2 | No gap fills. A weekend gap through the stop is scored at the stop price. Trades that were open across a session break are counted and dated in §10. | Q8 |
| 3 | Intrabar ordering is pessimistic: SL → TP → partial, in that order, every bar. One bar containing both the 80% trigger and the SL is a LOSS. | Q7 |
| 4 | Within a single M1 bar, sweep+reversal is only confirmed if the bar's close is back on the reversal side; otherwise the window waits for a later bar, and if none comes it is NO_SETUP (never assumed favourable). | Q5 |
| 5 | Each window is evaluated independently. Two trades may be open at once if an earlier one has not closed; this is a reporting simplification, not a claim about portfolio risk. | derived |
| 6 | R only, no currency. `result_currency_amount` is NULL. | Q9 |
| 7 | Monday–Friday only, New York calendar. Sunday evening sessions in the data are not evaluated (PRD §6.1 as written). | PRD §6.1 / Q11 |

R is `|entry − SL| = 0.5·Range + 1 tick`. Note `TP − entry = 0.5·Range`, i.e. **a clean run to TP is worth slightly less than 1R** — a property of the spec, not an error. The realised values depend on how large Candle A's range is relative to one tick: partial-then-breakeven is `0.9 × 0.8 × Range / (0.5 × Range + 1 tick)`, which approaches +0.72R for wide candles (~+0.70R at this dataset's median range) and +0.60R for a 1.0-point range. Full stop is exactly −1R; a clean TP run is `0.5·Range / (0.5·Range + 1 tick)` (0.81–0.99R in this sample).

---

## 3. Input data quality

| Property | Value |
|---|---|
| Bars | 287,746 |
| Timestamp format | `unix_epoch` |
| Raw range | 2026-01-02 01:00:00 → 2026-10-22 23:59:00 |
| NY range (after conversion) | 2026-01-01 18:00:00 → 2026-10-22 16:59:00 |
| Duplicate timestamps | 0 |
| Non-monotonic timestamps | 0 |
| OHLC ordering violations | 0 |
| Prices off the 0.1 tick grid | 0 |
| Bars with non-positive volume | 0 |

Session invariants after conversion (these are the gate the adapter enforces at load time):

| Check | Result |
|---|---|
| no_saturday_bars | PASS — 0 Saturday bars |
| daily_break_1700_1800_ny | PASS — 0 Mon-Thu bars inside 17:00-18:00 NY |
| friday_close_1659_ny | PASS — 39/42 Fridays close 16:59, 0 bars after 16:59 |
| sunday_open_1800_ny | PASS — 42/42 Sundays open 18:00, 0 bars before 18:00 |

---

## 4. Coverage — every window, exactly once

| Property | Value |
|---|---|
| Trading dates evaluated (Mon–Fri) | 211 |
| Date range | 2026-01-01 → 2026-10-22 |
| Windows per date | 21 |
| Expected rows | 4,431 |
| Rows produced | 4,431 |
| Dates with wrong row count | 0 |
| Duplicate (date, window) pairs | 0 |
| **Coverage invariant** | **PASS** |

The evaluation range is derived from the bars that exist, not from the calendar: the file's last bar is 2026-10-22 16:59:00 NY, so any later date is excluded rather than fabricated as `market_closed_no_data`. Windows on the final date that fall after the file's last bar are labelled `outside_data_range` (see §5).

---

## 5. Outcome distribution

| Outcome | Count | Share of rows |
|---|---|---|
| WIN | 1,718 | 38.8% |
| LOSS | 913 | 20.6% |
| NO_SETUP | 1,800 | 40.6% |
| **Total** | **4,431** | 100.0% |

### Why windows did not trade (NO_SETUP reasons only)

| Reason | Count | Share of rows |
|---|---|---|
| `no sweep` | 1,051 | 23.7% |
| `ambiguous_same_bar_insufficient_granularity` | 342 | 7.7% |
| `market_closed_no_data` | 292 | 6.6% |
| `invalid open location` | 103 | 2.3% |
| `missing_market_data` | 6 | 0.1% |
| `outside_data_range` | 6 | 0.1% |

What each one means:

- `invalid open location` — Candle B opened exactly at `Mid_A`, at/above `High_A`, or at/below `Low_A` (PRD §6.4). Since the market is continuous, this is the case where Candle A closed exactly on one of its own extremes or exactly at its midpoint.
- `no sweep` — no touch of `High_A + 1 tick` (short) / `Low_A − 1 tick` (long) inside Candle B before it closed.
- `sweep not reversed` — the level was swept, but price never came back through the opposite threshold before the window closed.
- `ambiguous_same_bar_insufficient_granularity` — one M1 bar straddled both thresholds without closing back on the reversal side, and no later bar resolved it. Deliberately scored as NO_SETUP, never as a trade (Q5).
- `market_closed_no_data` — no market data for the window. Per the Q10 ruling this is a NO_SETUP, never MISSING_EVALUATION: the bot was available, the market was shut (Friday evenings after 17:00 NY, exchange holidays, half-days, and the 13:00–18:00 closure on US holiday Mondays).
- `missing_market_data` — bars are absent while the market was demonstrably still trading. A feed-quality artefact, not a strategy result.
- `outside_data_range` — the input file stops before this window. A property of the export, not of the market or the strategy.

Two results here deserve an explicit comment:

1. `sweep not reversed` fired **0 times**. That is not a coding gap: the sweep and reversal levels are only two ticks apart (`High_A + 1t` versus `High_A − 1t`), so almost any M1 bar that reaches the sweep level also trades through the reversal level inside the same minute. The genuinely unreachable-reversal case is therefore vanishingly rare, and those windows are captured by the ambiguity rule instead.
2. `ambiguous_same_bar_insufficient_granularity` accounts for **342 windows (22.7% of all non-market-closure NO_SETUPs)**. Every one of them was verified as a true Q5 case — a single bar holding both thresholds without closing back on the reversal side, and no later bar resolving it. Zero windows in this sample had a later bar that reversed cleanly, i.e. no valid trigger was discarded. This is the measurable cost of 1-minute granularity on a 2-tick-apart rule, and it is the single number to watch if Phase 2 ever moves to tick data: a lower `ambiguous...` count with everything else stable would confirm the M1 approximation is conservative rather than distorting.

---

## 6. Trading results (R-multiples, zero-cost fills)

| Metric | Value |
|---|---|
| Trades taken | 2,631 |
| Wins | 1,718 |
| Losses | 913 |
| Win rate | 65.3% |
| Net R | +575.135 |
| Average R per trade | +0.219 |
| Gross win R | +1488.135 |
| Gross loss R | -913.000 |
| Profit factor | 1.63 |
| Partial exit fired | 943 (35.8% of trades) |

### By direction

| Direction | Trades | Wins | Win rate | Net R |
|---|---|---|---|---|
| LONG | 1312 | 863 | 65.8% | +304.551 |
| SHORT | 1319 | 855 | 64.8% | +270.583 |

### By exit path

| Exit path | Trades | Share | Net contribution |
|---|---|---|---|
| stop loss hit (no partial) | 913 | 34.7% | -913.000 |
| take profit hit (no partial) | 775 | 29.5% | +768.867 |
| breakeven stop hit after partial | 496 | 18.9% | +354.611 |
| target hit after partial | 447 | 17.0% | +364.657 |

---

## 7. Per-window-time performance

Sorted by net R. `rows` is every evaluation for that slot; `traded` is the subset that produced an order.

| Window (NY) | Rows | Traded | Wins | Win rate | Net R | NO_SETUP |
|---|---|---|---|---|---|---|
| `04:45-05:15` | 211 | 136 | 95 | 69.9% | +40.844 | 75 |
| `06:45-07:15` | 211 | 126 | 89 | 70.6% | +40.672 | 85 |
| `07:45-08:15` | 211 | 144 | 98 | 68.1% | +39.959 | 67 |
| `05:45-06:15` | 211 | 137 | 94 | 68.6% | +37.505 | 74 |
| `14:45-15:15` | 211 | 129 | 89 | 69.0% | +37.319 | 82 |
| `13:45-14:15` | 211 | 113 | 80 | 70.8% | +36.961 | 98 |
| `21:45-22:15` | 211 | 110 | 78 | 70.9% | +35.362 | 101 |
| `01:45-02:15` | 211 | 153 | 100 | 65.4% | +31.872 | 58 |
| `03:45-04:15` | 211 | 140 | 89 | 63.6% | +29.643 | 71 |
| `09:45-10:15` | 211 | 130 | 86 | 66.2% | +28.366 | 81 |
| `08:45-09:15` | 211 | 133 | 85 | 63.9% | +26.002 | 78 |
| `02:45-03:15` | 211 | 145 | 91 | 62.8% | +25.819 | 66 |
| `20:45-21:15` | 211 | 112 | 74 | 66.1% | +25.374 | 99 |
| `10:45-11:15` | 211 | 135 | 87 | 64.4% | +25.061 | 76 |
| `23:45-00:15` | 211 | 95 | 63 | 66.3% | +22.665 | 116 |
| `00:45-01:15` | 211 | 136 | 84 | 61.8% | +20.300 | 75 |
| `22:45-23:15` | 211 | 101 | 64 | 63.4% | +18.434 | 110 |
| `11:45-12:15` | 211 | 126 | 77 | 61.1% | +15.962 | 85 |
| `19:45-20:15` | 211 | 106 | 62 | 58.5% | +12.625 | 105 |
| `12:45-13:15` | 211 | 119 | 71 | 59.7% | +12.484 | 92 |
| `18:45-19:15` | 211 | 105 | 62 | 59.0% | +11.909 | 106 |

---

## 8. Monthly breakdown

| Month | Rows | Trades | Wins | Win rate | Net R |
|---|---|---|---|---|---|
| 2026-01 | 462 | 262 | 170 | 64.9% | +54.886 |
| 2026-02 | 420 | 249 | 166 | 66.7% | +59.393 |
| 2026-03 | 462 | 287 | 203 | 70.7% | +93.985 |
| 2026-04 | 462 | 246 | 159 | 64.6% | +51.289 |
| 2026-05 | 441 | 241 | 153 | 63.5% | +43.869 |
| 2026-06 | 462 | 280 | 177 | 63.2% | +48.609 |
| 2026-07 | 483 | 310 | 205 | 66.1% | +69.739 |
| 2026-08 | 441 | 272 | 185 | 68.0% | +73.262 |
| 2026-09 | 462 | 267 | 160 | 59.9% | +34.434 |
| 2026-10 | 336 | 217 | 140 | 64.5% | +45.667 |

---

## 9. DST validation

| Regime | Rows | Trades | Wins | Win rate | Net R |
|---|---|---|---|---|---|
| EST (UTC-5) | 987 | 575 | 382 | 66.4% | +135.806 |
| EDT (UTC-4) | 3444 | 2056 | 1336 | 65.0% | +439.329 |

Both US DST transitions happen at 02:00 on a **Sunday**. Because the strategy only evaluates Monday–Friday, no window's local start time can ever fall inside the spring-forward gap (02:00–02:59 does not exist) or the fall-back repetition. `time_windows.localize()` still detects non-existent local times and reports them rather than silently resolving them, so the day rule could be changed without introducing a silent bug. Occurrences in this run: **0**.

**Coverage limit, stated plainly:** the dataset spans 2026-01-01 → 2026-10-22, so it contains the **March 8 spring-forward** transition but **not** the **November 1 fall-back**. Fall-back behaviour is therefore reasoned about (Sunday-only, so out of scope for Mon–Fri windows) but not empirically exercised. Re-running on a dataset that crosses Nov 1 is the remaining DST evidence gap.

---

## 10. Data-integrity flags raised during evaluation

| Flag | Count | Meaning |
|---|---|---|
| `no_bars_in_window` | 292 | window had no market data at all |
| `runner_stopped_in_partial_bar` | 265 | the 1-contract runner was stopped at breakeven inside the same bar that fired the partial (pessimistic Q7 reading) |
| `incomplete_candle` | 12 | window partially covered by the feed |
| `open_through_session_break` | 3 | a position was still open when the market broke — no gap fill is modelled, so its exit price is optimistic |

Trades that were still open across a session break, and so are scored with an optimistic exit price (no gap fill is modelled):

| Date | Window | Direction | Entry | Final exit | R |
|---|---|---|---|---|---|
| 2026-01-02 | `10:45-11:15` | LONG | 25167.7 | 25283.3 | +0.819 |
| 2026-04-03 | `08:45-09:15` | LONG | 23943.2 | 23943.2 | +0.717 |
| 2026-04-23 | `13:45-14:15` | SHORT | 26745.0 | 26850.5 | -1.000 |

---

## 11. Manual spot-check set

24 windows selected to cover every outcome and every reason, spread across the calendar. The full derivation of each — Candle A OHLC, Mid_A, Range, Candle B open, sweep and reversal timestamps, entry/SL/TP, partial trigger, exit and R — is in `spot_check.csv`.

Verification recipe for each row: `High_A`/`Low_A` must equal the max/min of the 15 M1 bars in the HH:45–HH:00 window; `Mid_A = (High_A+Low_A)/2`; `Range = High_A−Low_A`; context is Candle B's open against those three levels; sweep is `High_A+1t` (short) or `Low_A−1t` (long) touched in Candle B; reversal is the opposite side by 1 tick; `SL = High_A+0.5·Range` (short) or `Low_A−0.5·Range` (long); `TP = Mid_A∓1t`.

| Date | Window | Ctx | A: O/H/L/Mid | B open | Sweep @ | Rev @ | Entry | SL | TP | 80% | Outcome | Reason | R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-01-01 | `19:45-20:15` | PREMIUM | 25301.3/25315.5/25298.6/25307.05 | 25312.8 | 25315.6 @20:00 | — | — | — | — | — | NO_SETUP | ambiguous_same_bar_insufficient_granularity | — |
| 2026-01-01 | `23:45-00:15` | DISCOUNT | 25434.1/25435.3/25425.6/25430.45 | 25427.2 | 25425.5 @00:01 | 25425.7 @00:01 | 25425.7 | 25420.8 | 25430.5 | 25429.6 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-01-02 | `05:45-06:15` | PREMIUM | 25524.3/25538.1/25523.2/25530.65 | 25532.1 | 25538.2 @06:02 | 25538.0 @06:03 | 25538.0 | 25545.5 | 25530.6 | 25532.0 | WIN (tp) | target hit after partial | +0.809 |
| 2026-01-05 | `23:45-00:15` | PREMIUM | 25455.5/25464.0/25453.2/25458.60 | 25462.9 | 25464.1 @00:00 | 25463.9 @00:00 | 25463.9 | 25469.4 | 25458.5 | 25459.6 | WIN (breakeven) | breakeven stop hit after partial | +0.707 |
| 2026-01-06 | `01:45-02:15` | VOID | 25489.5/25489.9/25480.9/25485.40 | 25485.4 | — | — | — | — | — | — | NO_SETUP | invalid open location | — |
| 2026-01-07 | `23:45-00:15` | PREMIUM | 25595.7/25601.1/25593.1/25597.10 | 25598.3 | 25601.2 @00:00 | 25601.0 @00:01 | 25601.0 | 25605.1 | 25597.0 | 25597.8 | WIN (tp) | take profit hit | +0.976 |
| 2026-01-08 | `12:45-13:15` | DISCOUNT | 25498.0/25527.5/25497.5/25512.50 | 25505.0 | 25497.4 @13:02 | 25497.6 @13:04 | 25497.6 | 25482.5 | 25512.6 | 25509.6 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-01-09 | `02:45-03:15` | DISCOUNT | 25499.6/25510.6/25498.2/25504.40 | 25501.6 | — | — | — | — | — | — | NO_SETUP | no sweep | — |
| 2026-01-14 | `23:45-00:15` | PREMIUM | 25420.7/25423.0/25405.7/25414.35 | 25414.5 | 25423.1 @00:02 | 25422.9 @00:03 | 25422.9 | 25431.7 | 25414.2 | 25416.0 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-01-15 | `12:45-13:15` | VOID | 25713.8/25728.8/25684.0/25706.40 | 25685.4 | — | — | — | — | — | — | NO_SETUP | missing_market_data | — |
| 2026-01-19 | `12:45-13:15` | VOID | 25254.9/25262.9/25242.4/25252.65 | 25243.3 | — | — | — | — | — | — | NO_SETUP | market_closed_no_data | — |
| 2026-01-26 | `13:45-14:15` | PREMIUM | 25761.6/25770.0/25751.4/25760.70 | 25764.4 | 25770.1 @14:01 | 25769.9 @14:01 | 25769.9 | 25779.3 | 25760.6 | 25762.5 | WIN (tp) | take profit hit | +0.989 |
| 2026-02-09 | `01:45-02:15` | DISCOUNT | 25092.8/25108.9/25067.1/25088.00 | 25076.8 | 25067.0 @02:13 | 25067.2 @02:13 | 25067.2 | 25046.2 | 25088.1 | 25083.9 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-02-20 | `00:45-01:15` | PREMIUM | 24877.4/24888.2/24873.3/24880.75 | 24883.4 | 24888.3 @01:01 | 24888.1 @01:01 | 24888.1 | 24895.7 | 24880.7 | 24882.1 | WIN (breakeven) | breakeven stop hit after partial | +0.710 |
| 2026-03-04 | `11:45-12:15` | DISCOUNT | 25121.8/25131.9/25101.4/25116.65 | 25109.3 | 25101.3 @12:00 | 25101.5 @12:00 | 25101.5 | 25086.2 | 25116.8 | 25113.7 | WIN (tp) | take profit hit | +0.993 |
| 2026-03-17 | `03:45-04:15` | DISCOUNT | 24557.2/24558.9/24534.9/24546.90 | 24542.0 | 24534.8 @04:10 | 24535.0 @04:11 | 24535.0 | 24522.9 | 24547.0 | 24544.6 | WIN (tp) | take profit hit | +0.992 |
| 2026-03-27 | `09:45-10:15` | DISCOUNT | 23398.9/23457.9/23283.6/23370.75 | 23294.9 | 23283.5 @10:00 | 23283.7 @10:00 | 23283.7 | 23196.4 | 23370.8 | 23353.4 | WIN (breakeven) | breakeven stop hit after partial | +0.719 |
| 2026-04-09 | `10:45-11:15` | PREMIUM | 24811.8/24879.9/24811.8/24845.85 | 24868.2 | 24880.0 @11:12 | 24879.8 @11:13 | 24879.8 | 24914.0 | 24845.8 | 24852.6 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-04-23 | `09:45-10:15` | DISCOUNT | 26908.0/26952.1/26865.6/26908.85 | 26867.9 | 26865.5 @10:00 | 26865.7 @10:03 | 26865.7 | 26822.3 | 26908.9 | 26900.3 | WIN (breakeven) | breakeven stop hit after partial | +0.718 |
| 2026-05-07 | `09:45-10:15` | PREMIUM | 28623.6/28684.4/28590.6/28637.50 | 28663.2 | 28684.5 @10:04 | 28684.3 @10:05 | 28684.3 | 28731.3 | 28637.4 | 28646.8 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-05-20 | `13:45-14:15` | PREMIUM | 29210.7/29218.4/29190.3/29204.35 | 29209.9 | 29218.5 @14:00 | 29218.3 @14:01 | 29218.3 | 29232.5 | 29204.2 | 29207.1 | WIN (breakeven) | breakeven stop hit after partial | +0.715 |
| 2026-06-02 | `08:45-09:15` | DISCOUNT | 30498.8/30499.4/30470.3/30484.85 | 30474.6 | 30470.2 @09:00 | 30470.4 @09:01 | 30470.4 | 30455.8 | 30484.9 | 30482.0 | LOSS (sl) | stop loss hit | -1.000 |
| 2026-06-15 | `03:45-04:15` | DISCOUNT | 30225.8/30240.1/30215.2/30227.65 | 30217.3 | 30215.1 @04:00 | 30215.3 @04:00 | 30215.3 | 30202.8 | 30227.8 | 30225.3 | WIN (breakeven) | breakeven stop hit after partial | +0.714 |
| 2026-06-25 | `22:45-23:15` | DISCOUNT | 29049.5/29049.8/28932.9/28991.35 | 28966.8 | 28932.8 @23:01 | 28933.0 @23:02 | 28933.0 | 28874.5 | 28991.4 | 28979.8 | LOSS (sl) | stop loss hit | -1.000 |

---

## 12. Reproducing this report

```bash
cd bot
python3 -m unittest discover -s tests          # full logic suite
python3 historical_validation/validate_against_history.py \
    --csv ../data/nas100_m1_history.csv
```

Runs on the Python standard library alone: no third-party runtime dependency, no credentials, no network, no MT5 terminal. `pytest` also works if installed. Pass `--convention utc` to reproduce the rejected reading in §1, or `--out-dir` to write elsewhere.
