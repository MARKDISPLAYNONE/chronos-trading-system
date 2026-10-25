# Project Chronos — Implementation Plan

> **Audience note:** This document assumes zero prior context. It translates
> everything defined in docs 01–05 into a concrete, ordered build sequence —
> what gets built, in what order, with what dependencies, and how to verify
> each step works before moving to the next. This is the Agent's primary
> execution reference once a phase begins (per `07-PHASE-PLAN.md` for the
> higher-level phase gating). Read this after all prior docs.

## 1. Purpose of This Document

This document does not repeat WHAT the system does (see PRD) or WHY
technical choices were made (see TRD) — it defines the **build sequence**:
concrete, ordered implementation steps, folder structure, required
environment variables, and verification checkpoints for each step. Every
step here must trace back to a requirement in an earlier doc — if it
doesn't, flag it rather than building it.

## 2. Repository Structure
chronos-trading-system/
│
├── docs/ # All 8 project documents (this doc, PRD, TRD, etc.)
│
├── bot/ # Python bot engine (separate from dashboard)
│ ├── src/
│ │ ├── config.py # Loads .env, exposes config constants
│ │ ├── mt5_client.py # MT5 connection wrapper
│ │ ├── supabase_client.py # Supabase connection wrapper (service role)
│ │ ├── time_windows.py # Window definitions + NY timezone logic
│ │ ├── candle_tracker.py # Candle A / Candle B tracking logic
│ │ ├── context_classifier.py # Premium/Discount/Void classification
│ │ ├── sweep_detector.py # Sweep + reversal detection (intrabar)
│ │ ├── trade_calculator.py # Entry/SL/TP/handle calculations
│ │ ├── order_executor.py # Order submission via MT5
│ │ ├── trade_manager.py # Partial exit + breakeven monitoring loop
│ │ ├── logger.py # Writes window_evaluations/trades/system_events records
│ │ └── main.py # Entry point — orchestrates the main monitoring loop
│ ├── tests/
│ │ ├── test_time_windows.py
│ │ ├── test_context_classifier.py
│ │ ├── test_sweep_detector.py
│ │ └── test_trade_calculator.py
│ ├── historical_validation/ # Phase 1 scripts — run strategy against historical data
│ │ └── validate_against_history.py
│ ├── requirements.txt
│ └── .env.example # Template showing required variable NAMES only, no real values
│
├── dashboard/ # Next.js dashboard application
│ ├── app/ # Next.js app directory
│ │ ├── page.tsx # Login screen
│ │ ├── dashboard/
│ │ │ ├── page.tsx # Main dashboard view
│ │ │ ├── trades/page.tsx # Trade Log view
│ │ │ ├── windows/page.tsx # Per-Window-Time Performance view
│ │ │ └── settings/page.tsx # Minimal settings view
│ ├── components/ # Reusable UI components
│ ├── lib/
│ │ └── supabaseClient.ts # Supabase client (anon key, client-side)
│ ├── package.json
│ └── .env.local.example # Template showing required variable NAMES only
│
├── supabase/
│ └── migrations/ # SQL migration files, in order, per 05-DATABASE-SCHEMA.md
│ ├── 0001_create_instruments.sql
│ ├── 0002_create_window_evaluations.sql
│ ├── 0003_create_trades.sql
│ ├── 0004_create_system_events.sql
│ ├── 0005_enable_rls_policies.sql
│ ├── 0006_create_triggers.sql
│ └── 0007_create_indexes.sql
│
├── .github/
│ └── workflows/ # GitHub Actions CI/CD definitions
│ ├── dashboard-deploy.yml
│ └── bot-tests.yml
│
├── .gitignore # Must include .env, .env.local, pycache, node_modules, etc.
└── README.md # Short project overview + links into /docs

text


## 3. Required Environment Variables (Reference)

**Never commit actual values. These are the variable NAMES only, to be
documented in `.env.example` / `.env.local.example` files.**

### Bot (`bot/.env`)
| Variable | Purpose |
|---|---|
| `MT5_LOGIN` | MT5 account login number |
| `MT5_PASSWORD` | MT5 account password |
| `MT5_SERVER` | MT5 broker server name |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key (bypasses RLS — bot trusted access, per 05-DATABASE-SCHEMA.md Section 5) |
| `INSTRUMENT_SYMBOL` | Exact symbol name as it appears in MT5 (confirmed per TRD Section 14 open item) |
| `ENVIRONMENT` | `"demo"` or `"live"` — explicit flag the bot checks before allowing any live execution (see Section 9 safeguard below) |

### Dashboard (`dashboard/.env.local`)
| Variable | Purpose |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL (public, safe to expose client-side) |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/public key (RLS-protected, safe for client-side use) |

## 4. Build Sequence (Ordered, with Dependencies)

Each step below lists: what to build, what it depends on, and how to
verify it's correct before moving on. **Do not skip verification steps** —
this is how an autonomous system avoids silent bugs.

---

### Step 1: Repository & Documentation Setup
**Depends on:** Nothing (first step)
**Build:**
- Initialize git repo, connect to GitHub remote
- Create folder structure per Section 2
- Ensure all 8 docs are committed to `docs/`
- Create `.gitignore` (Section 2) before anything else is added, to avoid
  ever accidentally committing secrets
**Verify:** `git status` shows a clean working tree after initial commit;
`.env` files are confirmed to NOT appear in `git status` even if created
locally (proves `.gitignore` is working)

---

### Step 2: Supabase Project Setup
**Depends on:** Step 1
**Build:**
- Create Supabase project (free tier)
- Run migrations in order (`0001` through `0007`) exactly as specified in
  `05-DATABASE-SCHEMA.md`
- Manually create the single user account (Handler) via Supabase Auth —
  no public sign-up flow needed (per Design Brief Section 4.1)
- Insert one row into `instruments` with confirmed symbol/tick
  size/point value (resolving TRD Section 14 open item — this requires
  checking the MT5 demo account directly first)
**Verify:** Query each table directly in Supabase's SQL editor to confirm
structure matches schema doc exactly; confirm RLS is enabled (attempt a
query as a different/anonymous user and confirm it returns no rows)

---

### Step 3: Bot Engine — Core Utilities (No Trading Logic Yet)
**Depends on:** Step 2
**Build:**
- `config.py` — loads and validates all required env vars, fails loudly
  and immediately if anything is missing (don't let the bot start in a
  half-configured state)
- `mt5_client.py` — connects to MT5 terminal, confirms symbol exists and
  is tradeable (per App Flow Section 2.1, startup steps 2 and 4)
- `supabase_client.py` — connects using service role key, confirms
  connectivity (App Flow Section 2.1, startup step 3)
- `time_windows.py` — defines the 21 windows (PRD Section 6.1) and
  provides a function to check "is current NY time the start of window X?"
  using DST-aware timezone logic (TRD Section 4.1)
**Verify:**
- Unit tests for `time_windows.py` covering: a normal window start, a
  non-window time, both midnight-crossing windows, and at least one test
  case specifically crossing a DST transition date (TRD Section 12 testing
  strategy)
- Manual run confirms MT5 and Supabase connections both succeed against
  real demo credentials

---

### Step 4: Bot Engine — Candle Tracking & Context Classification
**Depends on:** Step 3
**Build:**
- `candle_tracker.py` — builds Candle A and Candle B from live/historical
  1-minute data (PRD Section 6.2, 6.3)
- `context_classifier.py` — implements PRD Section 6.4 exactly (Premium/
  Discount/Void logic)
**Verify:**
- Unit tests with hand-calculated sample candle data covering: clear
  premium case, clear discount case, open exactly at midpoint (void),
  open above high (void), open below low (void)
- Cross-check at least 3 real historical windows by manually tracing the
  rules by hand (e.g., in a spreadsheet) and confirming code output
  matches exactly

---

### Step 5: Bot Engine — Sweep & Reversal Detection
**Depends on:** Step 4
**Build:**
- `sweep_detector.py` — implements PRD Section 6.5 exactly, using 1-minute
  (or tick, if available) data within Candle B's window, sequence-agnostic
  per the confirmed strategy rule
**Verify:**
- This is the highest-risk module for bugs (per earlier discussion on
  intrabar sequencing) — unit tests must cover: sweep-then-reverse
  (valid), sweep-no-reverse (NO_SETUP), no-sweep-at-all (NO_SETUP),
  opposite-direction movement before correct sweep (must NOT invalidate,
  per the confirmed sequence-agnostic rule), and a case with a sweep by
  exactly 1 tick (boundary condition)
- Manually trace at least 5 real historical windows end-to-end (context +
  sweep/reversal) and confirm code matches hand-calculated expectations
  before proceeding — this is the core strategy logic; do not rush
  verification here

---

### Step 6: Bot Engine — Trade Calculation Logic
**Depends on:** Step 5
**Build:**
- `trade_calculator.py` — implements PRD Section 6.6, 6.7, 6.8: entry
  price, SL, TP, handle-based distance calculation, and the 80%-to-TP
  partial-exit trigger price, all calculated in advance per trade
**Verify:**
- Unit tests with hand-calculated examples for both LONG and SHORT cases,
  confirming exact SL/TP/partial-trigger price values match manual
  calculation (use the worked example numbers from the original strategy
  description as test fixtures where possible)

---

### Step 7: Historical Validation Run (Phase 1 Gate)
**Depends on:** Step 6
**Build:**
- `historical_validation/validate_against_history.py` — runs the complete
  logic chain (Steps 3–6 combined) against historical 1-minute/tick data
  for a meaningful sample period (at least several weeks, ideally months,
  of data covering various market conditions)
- Outputs a report: every window's classification, outcome, and reason,
  without placing any real/demo orders
**Verify:**
- Manually spot-check a reasonable sample (e.g., 15–20 windows across
  different days/contexts) against the report output
- **This step is a hard gate per `07-PHASE-PLAN.md` Phase 1** — do not
  proceed to live/demo order execution code until this validation is
  reviewed and approved by the Handler/Senior Dev

---

### Step 8: Bot Engine — Order Execution (Dry-Run First)
**Depends on:** Step 7 (approved)
**Build:**
- `order_executor.py` — initially built with a `DRY_RUN` mode flag (env
  var or config constant) that logs what order WOULD be submitted,
  without actually calling MT5's order functions
- Only after dry-run is verified: implement real order submission via
  MT5's `order_send()` function, with the `EXECUTION_FAILED` handling
  path per App Flow Section 5.2
**Verify:**
- Dry-run mode produces correctly calculated order parameters matching
  Step 6's calculator output, logged clearly
- Real order submission tested ONLY against the demo account, confirming
  successful fills and correct handling of a deliberately-forced rejection
  scenario (e.g., testing with an invalid parameter) to confirm the
  `EXECUTION_FAILED` path works, per App Flow Section 5.2

---

### Step 9: Bot Engine — Trade Management (Partial Exit + Breakeven)
**Depends on:** Step 8
**Build:**
- `trade_manager.py` — implements App Flow Section 2.5's full monitoring
  loop: tracks open position, triggers partial exit + breakeven move at
  the pre-calculated 80% level, handles direct SL/TP hit scenarios
**Verify:**
- Test against demo account with real (small/test) trades if possible, or
  simulated price feeds, confirming: partial exit fires at the correct
  price, breakeven SL is correctly placed, final outcome (WIN/LOSS) is
  correctly determined per App Flow Section 2.5 step 2's net-result logic

---

### Step 10: Bot Engine — Logging Layer
**Depends on:** Step 9 (can be developed in parallel with Steps 4–9, since
logging is called throughout, but final integration testing happens here)
**Build:**
- `logger.py` — writes to `window_evaluations`, `trades`, and
  `system_events` tables exactly per `05-DATABASE-SCHEMA.md` structure and
  the controlled reason vocabulary (Section 8 of that doc)
**Verify:**
- Every outcome path identified in App Flow (NO_SETUP variants, WIN, LOSS,
  EXECUTION_FAILED, MISSING_EVALUATION) produces a correctly structured
  database record; confirm no path exists that fails to log anything
  (cross-check against App Flow Section 2.3–2.5 and Section 5 exhaustively)

---

### Step 11: Bot Engine — Main Orchestration Loop
**Depends on:** Steps 3–10 all complete
**Build:**
- `main.py` — ties everything together per App Flow Section 2.1 and 2.2:
  startup sequence, main monitoring loop, window evaluation trigger
**Verify:**
- Run the full bot in dry-run mode continuously for at least one full
  trading day, confirming all 21 windows produce exactly one logged
  outcome each, with no crashes, no missed windows, no duplicate records

---

### Step 12: Dashboard — Supabase Connection & Auth
**Depends on:** Step 2 (can be built in parallel with bot engine steps,
since dashboard and bot are decoupled per TRD Section 3)
**Build:**
- `supabaseClient.ts` — client-side Supabase connection using anon key
- Login screen (`app/page.tsx`) per Design Brief Section 4.1
**Verify:** Handler can log in with real credentials; unauthenticated
access to `/dashboard` redirects to login

---

### Step 13: Dashboard — Main Dashboard View
**Depends on:** Step 12, and requires at least some real data in the
database (from Step 7's historical validation run, or Step 11's dry-run)
**Build:**
- Main dashboard page per Design Brief Section 4.2: key metrics row,
  equity curve chart, NO_SETUP reason breakdown, date range selector
**Verify:** Numbers displayed match manual calculation from raw database
records for a known date range

---

### Step 14: Dashboard — Trade Log View
**Depends on:** Step 13
**Build:** Per Design Brief Section 4.3 — filterable/sortable table
**Verify:** Filters correctly narrow results; all documented columns
display correctly, blank fields handled gracefully for NO_SETUP rows

---

### Step 15: Dashboard — Per-Window-Time Performance View
**Depends on:** Step 13
**Build:** Per Design Brief Section 4.4
**Verify:** Sort order correct (net R-multiple descending by default);
numbers match manual aggregation from raw data

---

### Step 16: Dashboard — Settings & Deployment
**Depends on:** Steps 12–15
**Build:**
- Minimal settings page (Design Brief Section 4.5)
- Deploy to Vercel, connected to GitHub for automatic deployments
**Verify:** Production URL loads correctly, auth works in production,
environment variables correctly configured in Vercel's dashboard (not
committed to repo)

---

### Step 17: CI/CD Setup
**Depends on:** Steps 1–16 substantially complete
**Build:**
- `.github/workflows/bot-tests.yml` — runs Python unit tests (Steps 3–6's
  test suites) on every push/PR
- `.github/workflows/dashboard-deploy.yml` — or rely on Vercel's native
  GitHub integration for automatic deploys (simpler, often sufficient —
  evaluate whether a custom workflow is even needed here)
**Verify:** Push a test commit, confirm Actions run and pass/fail
correctly; confirm dashboard auto-deploys on push to main

## 5. Critical Safeguard: Demo vs. Live Environment Flag

Per TRD Section 8 and App Flow's phase gating, the bot must NEVER connect
to a live account without explicit, deliberate action. Implementation must
include:

```python
# Pseudocode illustration — exact implementation detail for the Agent
if config.ENVIRONMENT == "live":
    require_explicit_confirmation_flag()  # e.g., a second, separate env
                                            # var like CONFIRM_LIVE_TRADING=yes
                                            # that must ALSO be set
```

This is a deliberate double-check, not paranoia — an autonomous system
placing real orders must have a structural barrier against accidentally
running live due to a misconfigured .env file.

6. Definition of Done (Per Step)
A step is considered DONE only when:

Code is written and committed to a feature branch (not main)
The verification criteria listed for that step have been met and
confirmed (not assumed)
handover/SESSION-HANDOVER.md is updated reflecting the step's completion
The Handler has reviewed the change in VS Code before it is merged
7. Open Items / Unresolved Implementation Questions
Exact MT5 historical data export process/format for Step 7's validation
dataset (vs. using Dukascopy export instead — decide during Step 7)
Exact polling interval for the main monitoring loop (App Flow Section
2.2) — to be decided during Step 11, balancing responsiveness vs.
resource usage
Whether GitHub Actions is needed for the bot at all in early phases
(since it doesn't auto-deploy anywhere yet, only runs locally) — tests
can run in Actions regardless, but deployment automation for the bot
itself is deferred until the MetaApi.cloud migration (per TRD Section
4.1 and Phase Plan)
text


---

That's the full Implementation Plan — a complete, ordered, dependency-mapped build sequence with verification gates at every step, so neither the Agent nor a new dev can skip ahead or silently assume something works.