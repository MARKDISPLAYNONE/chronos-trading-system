# Project Chronos — Design Brief

> **Audience note:** This document assumes zero prior context. It covers the
> visual/UX design of the dashboard only — not the bot engine (which has no
> UI). Read this after `01-PRD.md` (what/why), `02-TRD.md` (tech stack), and
> `03-APP-FLOW.md` (behavioral flow). This doc answers: "what should this
> look like, and why?" Implementation detail (component structure, file
> organization) belongs in `06-IMPLEMENTATION-PLAN.md`, not here.

## 1. Purpose of This Document

This is a **single-user, metrics-first, analytics dashboard** — not a
consumer product, not a multi-tenant SaaS UI, not a trading terminal with
manual order entry. Every design decision in this document optimizes for
one person (the Handler/strategy owner) quickly understanding "how is my
strategy performing, and why did specific outcomes happen?"

If a design decision doesn't serve that goal, it doesn't belong in this
build. Resist the urge to add features common to consumer trading apps
(social feeds, watchlists, news panels, etc.) — they are explicitly out of
scope per the PRD.

## 2. Design Principles

1. **Clarity over decoration.** This is a working tool used daily by a
   trader to make real decisions. Every screen should answer a question
   fast, not look impressive.
2. **Data density is acceptable, clutter is not.** The Handler wants to see
   meaningful numbers (win rate, R-multiple, per-window breakdowns) without
   excessive scrolling or hunting — but every number shown must be
   labeled unambiguously.
3. **Read-only by design (for now).** No button, control, or interaction in
   this phase should place, modify, or cancel a real trade. This is a
   hard constraint from the PRD, not a style preference — a misplaced
   "confirm" button in a future iteration must never be confused with a
   live trading action.
4. **Explain the "why," not just the "what."** Per PRD 6.10, the core value
   of this system is understanding WHY an outcome occurred. The design
   must surface reasons prominently (e.g., NO_SETUP reason text), not bury
   them in a tooltip or a secondary click.
5. **Dark mode by default.** Trading-tool convention, easier on the eyes
   for a tool checked frequently throughout the day/night (strategy runs
   across nearly 24 hours of windows). Light mode is a nice-to-have, not a
   requirement.
6. **Mobile-responsive, not mobile-first.** The dashboard should be usable
   on a phone (e.g., quick glance at daily performance), but it is
   primarily a desktop/laptop tool for serious review sessions — don't
   compromise the desktop data-table experience to chase mobile elegance.

## 3. Information Architecture (Site Map)
/ (login screen, if not authenticated)
│
▼
/dashboard (main view — default landing page after login)
│
├── /dashboard/trades (Trade Log view — full detail table)
│
├── /dashboard/windows (Per-Window-Time Performance view)
│
└── /dashboard/settings (minimal — account/logout only, NOT strategy
config in this phase; strategy parameters live in the bot's config,
not the dashboard, per PRD scope)

text


No other pages/sections exist in this phase. Do not add navigation items
not listed here without updating this doc first.

## 4. Screen-by-Screen Breakdown

### 4.1 Login Screen (`/`)

**Purpose:** Gate access to all trading data.

**Contents:**
- Project name/logo ("Chronos")
- Email + password fields (Supabase Auth standard flow)
- No "sign up" flow needed — this is single-user; the account is
  provisioned manually by the Handler/Senior Dev during setup, not through
  public self-registration
- Minimal, centered, no marketing copy — this is a tool, not a product
  landing page

### 4.2 Main Dashboard (`/dashboard`)

**Purpose:** Answer "how is the strategy doing?" in under 10 seconds of
looking at the screen.

**Layout (top to bottom):**

1. **Header bar**
   - Project name
   - Date range selector (default: "This Week"; options: Today, This Week,
     This Month, All Time, Custom Range)
   - User menu (logout)

2. **Key Metrics Row** (prominent, top of page, large numbers)
   - Win Rate (%) — of all TRADED windows (excludes NO_SETUP from the
     denominator; this distinction must be visually clear, e.g., a
     secondary smaller stat showing "X trades / Y windows evaluated")
   - Net Result (in R-multiples AND in account currency, both shown)
   - Total Trades Taken (count)
   - Total NO_SETUP Windows (count) — this is NOT a "failure" metric, it's
     informational; styling should avoid implying NO_SETUP is bad (it's
     the strategy correctly avoiding invalid setups)

3. **Equity Curve Chart**
   - Line chart, cumulative R-multiple (or account balance) over time
     within the selected date range
   - X-axis: time/date; Y-axis: cumulative result
   - Should visually distinguish winning vs. losing periods if possible
     (e.g., color fill above/below zero line)

4. **NO_SETUP Reason Breakdown**
   - Simple bar chart or horizontal list: count of NO_SETUP occurrences
     grouped by reason (e.g., "invalid open location: 34", "no sweep: 51",
     "sweep not reversed: 22")
   - Purpose: helps the Handler see which rule condition is most often the
     blocker — directly serves the PRD's "understand why" goal

5. **Quick Links**
   - Button/link to full Trade Log view
   - Button/link to Per-Window-Time Performance view

### 4.3 Trade Log View (`/dashboard/trades`)

**Purpose:** Full forensic detail, trade by trade.

**Layout:**
- Filter bar at top:
  - Outcome type filter (All / WIN / LOSS / NO_SETUP / EXECUTION_FAILED)
  - Reason filter (dropdown populated from distinct reason values present
    in the data)
  - Date range filter
  - Window time-slot filter (dropdown of the 21 windows)
- Data table, sortable by column, with these columns:
  - Date
  - Window (e.g., "01:45–02:15")
  - Context (PREMIUM / DISCOUNT / VOID)
  - Outcome (WIN / LOSS / NO_SETUP / EXECUTION_FAILED)
  - Reason (specific text, always visible, not truncated/hidden)
  - Entry Price (blank if NO_SETUP)
  - SL Price (blank if NO_SETUP)
  - TP Price (blank if NO_SETUP)
  - Exit Price (blank if NO_SETUP)
  - R-Multiple / Result (blank if NO_SETUP)
  - Partial Exit Triggered? (Yes/No, blank if NO_SETUP)
- Pagination or infinite scroll for large date ranges (exact mechanism
  decided in implementation — not a design-critical decision)
- Row click → optional expanded detail view (not required in first build,
  acceptable to defer — all data already visible in table columns)

### 4.4 Per-Window-Time Performance View (`/dashboard/windows`)

**Purpose:** Answer "which of my 21 time slots are actually worth trading,
and which aren't?" — directly supports strategy refinement decisions.

**Layout:**
- Table with one row per window time slot (21 rows total), columns:
  - Window (e.g., "14:45–15:15")
  - Total times evaluated (across selected date range)
  - Number of times traded (sweep+reversal confirmed)
  - Win rate (of trades taken in this slot)
  - Net R-multiple contributed by this slot
  - Most common NO_SETUP reason for this slot (if NO_SETUP rate is high)
- Default sort: by net R-multiple contributed, descending — surfaces best
  performing windows at the top immediately
- Optional (nice-to-have, not required for first build): small sparkline
  per row showing trend over time

### 4.5 Settings (`/dashboard/settings`)

**Purpose:** Minimal account management only.

**Contents:**
- Display logged-in user email
- Logout button
- (Nothing else — strategy parameters, bot configuration, and MT5
  credentials are NOT managed through this UI in this phase; they live in
  the bot's own `.env`/config files per the TRD. This is a deliberate
  scope boundary, not an oversight.)

## 5. Visual Design Guidelines

### 5.1 Color System

| Purpose | Color direction | Notes |
|---|---|---|
| Background (dark mode default) | Near-black / dark gray | Reduce eye strain for frequent checking |
| Positive values (wins, positive R) | Green | Standard trading convention |
| Negative values (losses, negative R) | Red | Standard trading convention |
| Neutral/informational (NO_SETUP) | Gray or blue (NOT red) | Must not visually read as "bad" — it's a correct non-action, not a failure |
| Critical alerts (EXECUTION_FAILED, missing evaluations) | Amber/orange | Distinct from both loss-red and neutral-gray — this needs the Handler's attention but isn't a trade loss |
| Primary accent/branding | Designer's/Handler's choice — suggest something time/clock-related given "Chronos" naming, e.g., a muted gold or deep blue | Not critical to function, low priority decision |

### 5.2 Typography

- Use a clean, highly legible sans-serif (system font stack is acceptable —
  no need to license/import custom fonts for a single-user internal tool)
- Numerical data (prices, percentages, R-multiples) should use a
  monospace or tabular-number font variant where possible, so columns of
  numbers align visually — this meaningfully improves scan-ability in the
  Trade Log table

### 5.3 Layout & Spacing

- Generous whitespace around the Key Metrics Row (Section 4.2.2) — these
  are the most important numbers on the page and should not compete
  visually with dense table data below
- Tables should use subtle row striping or hover-highlighting for
  readability across many rows
- Responsive breakpoints: standard desktop (1280px+), tablet (768px+),
  mobile (below 768px) — at mobile width, the Key Metrics Row stacks
  vertically, and tables become horizontally scrollable rather than
  attempting to cram all columns

## 6. Accessibility Considerations

- Color must never be the ONLY indicator of meaning (e.g., WIN/LOSS should
  also have text labels, not just green/red coloring, for colorblind
  accessibility)
- Sufficient contrast ratios in dark mode (avoid pure gray-on-gray text
  that fails WCAG contrast guidelines)
- All interactive elements (filters, sort controls, date pickers) must be
  keyboard-navigable at a basic level

## 7. What This Dashboard Is NOT (Explicit Non-Goals)

To prevent scope creep during implementation, the following are explicitly
**out of scope** for the design in this phase:

- No manual trade entry, modification, or cancellation controls
- No live/real-time streaming price chart of the instrument itself (this
  is a post-trade analytics tool, not a charting platform — the Handler
  already has MT5/TradingView for live charts)
- No social features, sharing, or multi-user views
- No notification/alert configuration UI (alerting mechanism, per App
  Flow Section 5.3, is a backend/bot concern in this phase, not a
  dashboard feature)
- No strategy parameter editing UI (changing SL/TP logic, window times,
  etc. happens in code/config, not through a settings screen, in this
  phase)

## 8. Open Items / Unresolved Design Questions

To be resolved during Implementation Planning or by Handler preference
before/during build:

- Exact accent color choice (low priority, can be decided ad hoc during
  frontend build)
- Whether row-level expanded detail view in Trade Log is built in v1 or
  deferred (currently leaning toward deferred, since all data is already
  visible in table columns)
- Exact pagination vs. infinite scroll decision for Trade Log at scale
- Whether sparkline trend indicators in Per-Window-Time view are included
  in v1 or deferred as a future enhancement
That's the full Design Brief — every screen, every data point, every explicit non-goal spelled out so no one accidentally builds scope creep (like live order entry) into what must stay a read-only analytics tool.