# Project Chronos — Database Schema Document

> **Audience note:** This document assumes zero prior context. It defines
> every table, field, relationship, and security policy in the database —
> the single source of truth for data structure. Read this after `01-PRD.md`,
> `02-TRD.md`, and `03-APP-FLOW.md`. Any code that reads/writes to the
> database (bot engine or dashboard) must conform exactly to what's defined
> here. If a field is needed that doesn't exist here, this document must be
> updated FIRST, before code is written — never add undocumented columns
> ad hoc.

## 1. Purpose of This Document

This document defines the **Supabase (Postgres) schema** for Project
Chronos: table structures, data types, constraints, relationships between
tables, indexes for performance, and Row Level Security (RLS) policies for
every table. It is written to be directly translatable into SQL migration
files during implementation.

## 2. Database Platform

- **Platform:** Supabase (hosted Postgres)
- **Why:** Free tier, built-in Auth, native Row Level Security support — see
  `02-TRD.md` Section 4.2 for full rationale
- **Naming convention reminder (from TRD Section 10):** tables use
  `snake_case`, plural nouns (e.g., `trade_logs`, not `TradeLog` or
  `trade_log`)

## 3. Entity Relationship Overview (Plain-Language Summary)

Before diving into table-by-table detail, here's how the data connects:

- Every **window evaluation** (one of the 21 daily time slots, on a given
  date) produces exactly one record in `window_evaluations`
- If a window evaluation results in a confirmed setup that gets traded, it
  links to exactly one record in `trades` (one-to-one relationship — a
  window either produces zero or one trade, never more, per PRD rules)
- If something goes wrong at the system level (connectivity failure, order
  execution failure, missing evaluation due to downtime), it's recorded in
  `system_events`, optionally linked back to the relevant window evaluation
  if applicable
- `instruments` is a small reference table holding instrument specs (tick
  size, point value) so this isn't hardcoded in application logic and can
  be confirmed/adjusted in one place
- All tables include a `user_id` field tying records to the authenticated
  Supabase user — even though this is a single-user system (per PRD
  Section 4), this pattern is used from day one for correct RLS hygiene
  and to avoid costly retrofitting if the system ever expands
auth.users (Supabase built-in)
│
│ user_id (foreign key, on every table below)
│
├──▶ instruments
│
├──▶ window_evaluations ──▶ trades (one-to-one, optional)
│
└──▶ system_events (optionally references window_evaluations)

text


## 4. Table Definitions

### 4.1 `instruments`

**Purpose:** Reference table holding specs for each tradeable instrument.
Exists so tick size, point value, and symbol name are stored in one place,
not hardcoded across bot code and dashboard code separately.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` | Unique identifier |
| `user_id` | `uuid` | Foreign key → `auth.users.id`, not null | Owner of this record (RLS scope) |
| `symbol` | `text` | Not null, unique per user | Exact symbol name as it appears in MT5 (e.g., `"US100"`, `"NAS100"` — exact value confirmed during implementation per TRD Section 14 open items) |
| `tick_size` | `numeric` | Not null | Smallest price increment for this instrument |
| `point_value` | `numeric` | Not null | Monetary value of one point move, per contract |
| `handle_size_in_ticks` | `integer` | Not null, default `4` | Confirms the strategy's "1 handle = 4 ticks" definition explicitly per instrument, in case this ever varies |
| `is_active` | `boolean` | Not null, default `true` | Allows disabling an instrument without deleting historical data tied to it |
| `created_at` | `timestamptz` | Not null, default `now()` | Record creation timestamp |
| `updated_at` | `timestamptz` | Not null, default `now()` | Last modified timestamp (updated via trigger, see Section 6) |

**Notes:**
- This table will typically have very few rows (likely just 1, for the
  single instrument this system trades), but exists as a proper reference
  table rather than hardcoded constants, per good data design practice.

---

### 4.2 `window_evaluations`

**Purpose:** One record per time window, per trading day. This is the core
analytics table — every single one of the 21 daily windows must produce
exactly one row here, per PRD Section 6.10 and App Flow Section 2.3.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` | Unique identifier |
| `user_id` | `uuid` | Foreign key → `auth.users.id`, not null | Owner (RLS scope) |
| `instrument_id` | `uuid` | Foreign key → `instruments.id`, not null | Which instrument this evaluation is for |
| `trading_date` | `date` | Not null | The calendar date this window belongs to (see Section 7 for midnight-crossing window convention) |
| `window_label` | `text` | Not null | Human-readable window identifier, e.g., `"01:45-02:15"` — must match one of the 21 defined values exactly (see PRD 6.1) |
| `candle_a_open` | `numeric` | Nullable | Candle A OHLC — nullable until captured; should be fully populated for any non-error evaluation |
| `candle_a_high` | `numeric` | Nullable | " |
| `candle_a_low` | `numeric` | Nullable | " |
| `candle_a_close` | `numeric` | Nullable | " |
| `candle_a_mid` | `numeric` | Nullable | Calculated midpoint, `(high + low) / 2` |
| `candle_b_open` | `numeric` | Nullable | Candle B's open price — the value used for context classification |
| `context` | `text` | Not null, check constraint: `IN ('PREMIUM', 'DISCOUNT', 'VOID')` | Classification result per PRD 6.4 |
| `outcome` | `text` | Not null, check constraint: `IN ('WIN', 'LOSS', 'NO_SETUP', 'EXECUTION_FAILED', 'MISSING_EVALUATION')` | Final outcome of this window |
| `reason` | `text` | Not null | Specific explanation for the outcome (e.g., `"invalid open location"`, `"no sweep"`, `"sweep not reversed"`) — must always be populated, never blank, per PRD 6.10 |
| `trade_id` | `uuid` | Foreign key → `trades.id`, nullable | Links to the trade record if this window resulted in a confirmed, executed trade; null for NO_SETUP/VOID outcomes |
| `evaluated_at` | `timestamptz` | Not null, default `now()` | When this evaluation was finalized/recorded |
| `created_at` | `timestamptz` | Not null, default `now()` | Record creation timestamp |

**Constraints:**
- Unique constraint on (`user_id`, `instrument_id`, `trading_date`,
  `window_label`) — prevents duplicate evaluations for the same window on
  the same day (guards against bot bugs causing double-writes)

**Notes:**
- `reason` is a free-text field, but implementation should use a
  consistent, limited set of standard strings (see Section 8: Standard
  Reason Values) rather than arbitrary ad hoc text, so dashboard filtering
  and grouping (per Design Brief Section 4.2.4) works reliably.
- `outcome = 'MISSING_EVALUATION'` corresponds to App Flow Section 3's
  end-of-day check — used when the bot was down/unavailable during a
  window and the slot needs to be explicitly flagged as unevaluated rather
  than silently absent.

---

### 4.3 `trades`

**Purpose:** Full detail of every executed trade. One-to-one with a
`window_evaluations` record that resulted in a confirmed setup.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` | Unique identifier |
| `user_id` | `uuid` | Foreign key → `auth.users.id`, not null | Owner (RLS scope) |
| `window_evaluation_id` | `uuid` | Foreign key → `window_evaluations.id`, not null, unique | The window this trade originated from (unique ensures true one-to-one) |
| `instrument_id` | `uuid` | Foreign key → `instruments.id`, not null | Instrument traded |
| `direction` | `text` | Not null, check constraint: `IN ('LONG', 'SHORT')` | Trade direction |
| `entry_price` | `numeric` | Not null | Actual fill price at entry |
| `stop_loss_price` | `numeric` | Not null | Initial SL price (per PRD 6.7) |
| `take_profit_price` | `numeric` | Not null | Initial TP price (per PRD 6.7) |
| `initial_contracts` | `integer` | Not null, default `10` | Starting position size |
| `partial_exit_triggered` | `boolean` | Not null, default `false` | Whether the 80%-to-TP partial rule fired (PRD 6.8) |
| `partial_exit_price` | `numeric` | Nullable | Price at which the 9-contract partial closed, if triggered |
| `breakeven_sl_price` | `numeric` | Nullable | The breakeven SL price set on the remaining contract, if partial triggered |
| `final_exit_price` | `numeric` | Nullable | Price at which the position was FULLY closed (either remaining contract's exit, or full SL hit if no partial occurred) |
| `final_outcome` | `text` | Not null, check constraint: `IN ('WIN', 'LOSS')` | Final result classification (matches `window_evaluations.outcome` for this trade) |
| `result_r_multiple` | `numeric` | Nullable | Result expressed as R-multiple |
| `result_currency_amount` | `numeric` | Nullable | Result expressed in account currency |
| `opened_at` | `timestamptz` | Not null | Timestamp of entry fill |
| `closed_at` | `timestamptz` | Nullable | Timestamp of full position close |
| `created_at` | `timestamptz` | Not null, default `now()` | Record creation timestamp |
| `updated_at` | `timestamptz` | Not null, default `now()` | Last modified (updated as trade progresses from open → partial → closed) |

**Notes:**
- A trade record is created the moment an order is successfully submitted
  (per App Flow Section 2.4), and UPDATED as it progresses through partial
  exit and final close (App Flow Section 2.5) — it is not a single
  write-once record, it has a lifecycle.
- `final_outcome = 'WIN'` includes the scenario where the partial exit was
  profitable even if the final remaining contract scratched at breakeven
  — net trade result determines WIN/LOSS, consistent with App Flow Section
  2.5 step 2.

---

### 4.4 `system_events`

**Purpose:** Captures critical system-level issues that are NOT strategy
outcomes — connectivity failures, order execution failures, and other
events requiring Handler attention, per App Flow Section 5.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `uuid` | Primary key, default `gen_random_uuid()` | Unique identifier |
| `user_id` | `uuid` | Foreign key → `auth.users.id`, not null | Owner (RLS scope) |
| `event_type` | `text` | Not null, check constraint: `IN ('MT5_DISCONNECT', 'DB_UNREACHABLE', 'ORDER_REJECTED', 'PARTIAL_EXIT_FAILED', 'SL_MODIFICATION_FAILED', 'BOT_CRASH', 'OTHER')` | Category of the event |
| `severity` | `text` | Not null, check constraint: `IN ('WARNING', 'CRITICAL')` | How urgently this needs attention |
| `message` | `text` | Not null | Full detail/error message, including any broker rejection text |
| `window_evaluation_id` | `uuid` | Foreign key → `window_evaluations.id`, nullable | Linked window if the event relates to a specific evaluation/trade attempt |
| `resolved` | `boolean` | Not null, default `false` | Whether the Handler has acknowledged/resolved this event |
| `occurred_at` | `timestamptz` | Not null, default `now()` | When the event happened |
| `created_at` | `timestamptz` | Not null, default `now()` | Record creation timestamp |

**Notes:**
- This table directly implements App Flow Section 5's error/exception
  flows — nothing in that section should ever fail silently; it must
  produce a row here.
- The dashboard does not currently have a dedicated view for this table
  per the Design Brief (Section 7 lists "no notification/alert
  configuration UI" as out of scope for v1) — but the TABLE still exists
  from day one so events are captured and queryable even before a UI is
  built for them. A future dashboard view surfacing this table is a
  reasonable near-term enhancement, not a structural change.

## 5. Row Level Security (RLS) Policies

**Non-negotiable per TRD Section 8:** RLS must be enabled on every table
from the moment it's created.

**General pattern applied to ALL four tables** (`instruments`,
`window_evaluations`, `trades`, `system_events`):

```sql
-- Enable RLS (example shown for one table, repeat for all four)
ALTER TABLE window_evaluations ENABLE ROW LEVEL SECURITY;

-- Policy: users can only SELECT their own records
CREATE POLICY "select_own_records"
  ON window_evaluations FOR SELECT
  USING (auth.uid() = user_id);

-- Policy: users can only INSERT records tied to their own user_id
CREATE POLICY "insert_own_records"
  ON window_evaluations FOR INSERT
  WITH CHECK (auth.uid() = user_id);

-- Policy: users can only UPDATE their own records
CREATE POLICY "update_own_records"
  ON window_evaluations FOR UPDATE
  USING (auth.uid() = user_id);

-- No DELETE policy is created intentionally — see note below
Important design decision: no DELETE policy on any table. This system
is built for auditability (PRD Section 11: "Auditability" non-functional
requirement). Trade and evaluation history should never be deletable
through normal application operation — not even by the owning user — to
preserve a complete, trustworthy historical record. If a genuine need to
remove bad test data arises during development, this should be done via
direct database access (Supabase dashboard SQL editor with service role),
not through an application-level DELETE capability.

Bot engine's database access: The bot engine writes to the database
using a Supabase service role key (which bypasses RLS entirely,
operating as a trusted backend process), NOT the same authenticated user
session the dashboard uses. This is standard Supabase practice: RLS
protects client-facing (dashboard) access; the bot, as a trusted backend
process, uses the elevated service role key. This service role key is
highly sensitive and must follow the Security Requirements in TRD
Section 8 — stored only in the bot's .env file, never in the dashboard's
environment (which should only ever use the public anon key), never
committed, never pasted anywhere.

6. Triggers & Automation
updated_at auto-update trigger — applied to every table with an
updated_at column (instruments, trades):

SQL

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_set_updated_at
  BEFORE UPDATE ON trades
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();
-- (repeat for instruments table)
7. Midnight-Crossing Window Convention (Resolving TRD Open Item)
Per TRD Section 14's open item, here is the resolved convention for date-
tagging the two midnight-crossing windows:

trading_date = the calendar date on which the window's Candle A
(HH:45 start) begins, in America/New_York time
Example: the window "23:45-00:15" starting on, say, March 3rd, is
tagged trading_date = 2024-03-03, even though it closes at 00:15 on
March 4th
Example: the window "00:45-01:15" starting on March 4th is tagged
trading_date = 2024-03-04
This convention must be applied consistently by the bot engine when
writing window_evaluations records, and documented clearly in code
comments at the point of implementation
8. Standard Reason Values (Controlled Vocabulary)
To keep the reason field in window_evaluations consistent and reliably
filterable on the dashboard (Design Brief Section 4.2.4 and 4.3), use ONLY
these standard strings wherever applicable:

Reason string	Used when
"invalid open location"	Candle B opens at Mid_A or outside [Low_A, High_A]
"no sweep"	Window closed with no sweep of the relevant level
"sweep not reversed"	Sweep occurred but reversal never completed before window close
"take profit hit"	Trade closed via TP
"stop loss hit"	Trade closed via full SL (no partial occurred)
"breakeven stop hit after partial"	Remaining 1 contract stopped at breakeven after partial exit
"target hit after partial"	Remaining 1 contract reached original TP after partial exit
"order rejected by broker"	Used in system_events, paired with broker's specific message
"bot unavailable during window"	Used when outcome = 'MISSING_EVALUATION'
If a genuinely new scenario arises during implementation that doesn't fit
this list, add it to this table first (update this doc), then use it
in code — don't invent ad hoc reason strings that aren't documented here.

9. Indexes (Performance Considerations)
Given expected data volume (21 windows/day × 5 days/week ≈ 105 rows/week
in window_evaluations, far fewer in trades), performance is not a near-
term concern. Still, apply these indexes from the start as good practice:

SQL

CREATE INDEX idx_window_evaluations_user_date
  ON window_evaluations (user_id, trading_date);

CREATE INDEX idx_window_evaluations_outcome
  ON window_evaluations (outcome);

CREATE INDEX idx_trades_user_opened_at
  ON trades (user_id, opened_at);

CREATE INDEX idx_system_events_resolved
  ON system_events (resolved) WHERE resolved = false;
10. Sample Queries (For Dashboard Reference)
These illustrate how the Design Brief's screens map to actual queries —
useful reference during implementation, not exhaustive:

Main Dashboard key metrics (Design Brief 4.2.2), for a date range:

SQL

SELECT
  outcome,
  COUNT(*) AS count
FROM window_evaluations
WHERE user_id = auth.uid()
  AND trading_date BETWEEN :start_date AND :end_date
GROUP BY outcome;
Per-Window-Time Performance (Design Brief 4.4):

SQL

SELECT
  window_label,
  COUNT(*) AS total_evaluated,
  COUNT(*) FILTER (WHERE outcome IN ('WIN', 'LOSS')) AS total_traded,
  COUNT(*) FILTER (WHERE outcome = 'WIN') AS wins,
  SUM(t.result_r_multiple) AS net_r_multiple
FROM window_evaluations we
LEFT JOIN trades t ON t.window_evaluation_id = we.id
WHERE we.user_id = auth.uid()
  AND we.trading_date BETWEEN :start_date AND :end_date
GROUP BY window_label
ORDER BY net_r_multiple DESC NULLS LAST;
11. Open Items / Unresolved Schema Questions
To be resolved during Implementation Planning:

Confirm final symbol value in instruments once the exact MT5 demo
account instrument is verified (per TRD Section 14)
Decide whether system_events needs a dedicated dashboard view sooner
rather than later (currently deferred per Design Brief Section 7, but
flagged here as a likely near-term addition given its operational
importance)
Confirm whether trading_date convention in Section 7 needs any
adjustment once real DST-transition testing occurs (per TRD Section 11)
text


---

That's the full database schema — every table, every field, RLS policies, the midnight-crossing convention resolved, and a controlled vocabulary for reasons so the dashboard filtering actually works reliably.