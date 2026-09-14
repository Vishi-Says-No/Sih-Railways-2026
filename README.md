# Railway Automatic Block Planning System — Prototype

SIH prototype for multi-department maintenance scheduling ("Shadow Blocks"):
ML priority scoring + OR-Tools constraint scheduling on top of a Linear
Referencing System that unifies TMS, SMMS, and TDMS data against COA's real
train timetable and goods forecast — with weekly and monthly planning
horizons.

## Block Demand schema (Block_Details.xlsx)

The assignment engine now runs directly against Railways' official Block
Demand application fields — **Application, Rly, Div, Section, Line,
Direction, Block Demand Date, Is Block Integrated, If integrated Block then
select department, Enforcement Condition, Use Corridor, Reason Code** — not
a simplified stand-in schema. `data_generator.py`'s `_block_demand_fields()`
generates them; `operator_input.py` collects them from a human; both feed
the exact same columns into `spatial_layer.py`, so every task — synthetic,
live-synced, or manually logged — carries the full form.

What each field actually *does* in `scheduler.py` (not just display):

| Field | Effect |
|---|---|
| Application (`dept`), Section (`coa_section`), Line, **Block Demand Date** | Hard-matched, all three, against a corridor's `(block_section, line, date)` — a request only competes for the corridor covering the exact day/line it asked for, not "any day this week". This is tighter than the old section-only match. |
| **Use Corridor = No** | Pulled out of the shared-corridor CP-SAT model entirely. Granted a dedicated standalone possession on its requested date instead — doesn't consume shared capacity, isn't bundled with anyone. Shown in purple on the Shadow Block Gantt. |
| **Is Block Integrated = Yes** + department | A strong reward (`INTEGRATION_BONUS`), not a hard constraint, for landing in the same corridor as a task from the named department — the requester's own stated intent to coordinate, on top of the scheduler's general bundling incentive. Whether it was actually achieved is tracked as `integration_fulfilled` and shown in the Shadow Block Gantt's detail table (✅/❌). |
| **Enforcement Condition = Fixed Time Slot** | A very heavy scheduling priority (`FIXED_SLOT_BONUS`) — soft-guaranteed rather than a hard constraint, so one bad request can't make the whole solve infeasible. |
| **Reason Code** | Feeds the ML priority explanation text (`priority_reason`) — not a separate scoring weight, to avoid double-counting severity/urgency it's already correlated with. |
| Rly, Div, Direction | Carried through and displayed for traceability; Direction is derived from Line (`UP_MAIN`→UP, `DN_MAIN`→DOWN) rather than asked separately, to avoid contradictory combinations. Rly/Div are fixed constants (`NR` / `DLI`) since this prototype models a single control area. |

Reason Code values used here (`EMERGENCY_REPAIR`, `STATUTORY_SAFETY_INSPECTION`,
`PLANNED_MAINTENANCE`, `PERIODIC_INSPECTION`, `RENEWAL_WORK`) are a
representative set for the demo, not a claim to reproduce an exact official
IR code table — swap `REASON_CODES` in `data_generator.py` for the real list
if/when it's available.

## What each requirement maps to in the code

1. **Integration of TMS/SMMS/TDMS maintenance data with corridor block
   availability from the Train Time Table + Goods Forecast**
   `src/data_generator.py` generates a passenger `train_timetable.csv` and a
   `goods_forecast.csv`. `src/corridor_availability.py` derives maintenance
   block windows from the *gaps* between those movements — availability is
   computed, not asserted. `src/spatial_layer.py` unifies the three defect
   feeds (with real `date_reported` / SLA `due_date`) onto one schema via the
   `MASTER_ASSET_MAP` linear reference.

2. **AI/ML prioritization by criticality, urgency, and asset-availability
   impact** — `src/priority_model.py` scores every task 0–100 from four real
   signals: severity, urgency/overdue (days to/since the SLA `due_date`),
   operational impact (corridor traffic density + power-block isolation
   need), and asset age. Uses XGBoost when there's enough data, otherwise a
   transparent rule-based fallback — either way every score comes with a
   plain-English `priority_reason`.

3. **Optimize block scheduling to maximize asset uptime / minimize downtime
   with multi-department coordination** — `src/scheduler.py`'s CP-SAT
   objective explicitly rewards bundling 2+ departments into one corridor
   (`BUNDLE_BONUS`) and penalizes every extra corridor opened
   (`CORRIDOR_PENALTY`), on top of maximizing scheduled criticality. It also
   computes the real "uncoordinated baseline" (one block per task) vs. the
   actual blocks used, so the downtime-reduction % shown in the dashboard is
   computed from data, not a fixed slide claim.

4. **Weekly and monthly planning horizons** — `src/rolling_planner.py` runs
   the scheduler week-by-week across a 4-week horizon: unscheduled tasks
   roll forward, new "emergency" defects only enter the candidate pool once
   their week arrives (the dynamic re-planning story), and results roll up
   into a Monthly Macro Plan. The dashboard exposes both as separate tabs.

## Project structure

```
railway-block-planner/
├── data/                       # generated CSVs (created on first run)
├── src/
│   ├── data_generator.py       # synthetic TMS/SMMS/TDMS defects + COA train timetable/goods forecast (Faker)
│   ├── corridor_availability.py# derives block windows from timetable+forecast gaps
│   ├── spatial_layer.py        # LRS unification -> unified_tasks.csv
│   ├── priority_model.py       # Engine 1: ML criticality scoring (XGBoost) + explainability
│   ├── scheduler.py            # Engine 2: OR-Tools CP-SAT Shadow Block scheduler
│   ├── rolling_planner.py      # Weekly Micro Plan + Monthly Macro Plan orchestration
│   ├── operator_input.py       # live demo: manual TMS/SMMS/TDMS fault entry
│   ├── sync.py                 # pulls from whichever DataSourceAdapter is configured
│   └── data_sources/           # swappable data source: synthetic (demo) vs. live Railways API
├── app.py                      # Streamlit dashboard (5 tabs)
├── .env.example                 # DATA_SOURCE + live API config template
├── requirements.txt
└── README.md
```

## Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Run the pipeline (no UI — for debugging each stage)

```bash
cd src
python data_generator.py        # -> tms/smms/tdms_defects.csv, train_timetable.csv, goods_forecast.csv
python corridor_availability.py # -> coa_corridors.csv (derived block windows)
python spatial_layer.py         # -> unified_tasks.csv
python priority_model.py        # -> scored_tasks.csv
python scheduler.py             # -> shadow_block_schedule.csv (single full-horizon run)
python rolling_planner.py       # -> rolling_schedule.csv, monthly_summary.csv (weekly + monthly)
```

## Run the dashboard

```bash
streamlit run app.py
```

Regenerates fresh synthetic data and runs the full pipeline automatically,
then shows four tabs: **Ingestion** (unified inbox with SLA/overdue status),
**Priority Queue** (ranked + explainable), **Weekly Micro Plan** (pick a
week, see its Shadow Block Gantt + real coordination KPIs), and **Monthly
Macro Plan** (backlog burn-down + blocks-opened-vs-baseline across the
horizon).

## How each piece maps to the pitch deck

| Slide | Code |
|---|---|
| Data Unification Layer | `src/spatial_layer.py`, `MASTER_ASSET_MAP` in `data_generator.py` |
| Block availability from timetable + goods forecast | `src/corridor_availability.py` |
| ML Priority Scorer | `src/priority_model.py` |
| OR-Tools Scheduler + multi-department bundling | `src/scheduler.py` |
| Weekly / Monthly rolling planning | `src/rolling_planner.py` |
| Dashboard (Streamlit/Plotly) | `app.py` |
| Buffer Time Heuristic | `BUFFER_MINS = 15` in `scheduler.py` |

## Live Railways API integration

By default the pipeline runs on synthetic (Faker-generated) data. To point it
at real TMS/SMMS/TDMS/COA systems instead, nothing in `spatial_layer.py`,
`priority_model.py`, `scheduler.py`, or `rolling_planner.py` needs to
change — they only ever see the unified schema. The integration seam is
`src/data_sources/`:

```
src/data_sources/
├── base.py              # DataSourceAdapter interface every source must satisfy
├── synthetic_source.py  # wraps the existing Faker generators (default)
├── api_source.py        # template for the real TMS/SMMS/TDMS/COA REST APIs
└── config.py             # reads DATA_SOURCE env var, builds the right adapter
src/sync.py               # pulls from whichever adapter is configured, upserts into data/
```

Switch modes via environment variables (see `.env.example`):

```bash
DATA_SOURCE=api
RAILWAY_API_KEY=...
TMS_API_BASE_URL=https://.../tms
SMMS_API_BASE_URL=https://.../smms
TDMS_API_BASE_URL=https://.../tdms
COA_API_BASE_URL=https://.../coa
```

**What's real vs. what's a template:** `base.py`, `config.py`, and `sync.py`
are finished — they're the swappable seam and don't depend on any
Railways-specific detail. `api_source.py` is a *structural template*: the
retry/backoff/pagination/auth plumbing is real, but the exact endpoint paths
and JSON field names (`_map_tms_row`, `_map_smms_row`, etc.) are placeholders
until Railways/CRIS provides the actual API contract. Updating those mapping
functions is the only code change needed to go live — everything downstream
already speaks the unified schema.

**Why polling is gated, not automatic on every page load:** `app.py` calls
`needs_resync()` before syncing, so a live integration only re-polls once
every `SYNC_INTERVAL_MINUTES` (default 15), not on every dashboard refresh.
Defects are fetched incrementally (`since=<last sync>`) and upserted by ID;
timetable/goods-forecast are refreshed as a full horizon snapshot each sync;
the asset master (GIS/chainage lookup) only refreshes weekly since it rarely
changes.

**What this build can't do for you:** get the actual base URLs, auth
mechanism (API key vs. OAuth2 vs. mTLS), and field-level API contract from
Railways/CRIS, and network access to Railnet (or whatever gateway they
expose) — those are organizational prerequisites, not something fixable in
code.

## Live demo: Operator Input tab

For showing judges the system actually working end-to-end (not just a
pre-baked dataset), the dashboard's first tab is **🛠️ Operator Input** —
a form-based entry point where someone plays TMS/SMMS/TDMS field staff and
logs a fault live:

1. Pick a department, fill in the form (section/KM or asset ID, severity,
   duration, which planning week it's reported into), submit.
2. It's written straight into `data/{tms,smms,tdms}_defects.csv` — the exact
   same schema `data_generator.py` produces — tagged `is_emergency=True`.
3. The dashboard cache is cleared and the page reruns: the new fault is
   immediately ML-scored (with a real `priority_reason`) and CP-SAT
   scheduled into whichever week it was reported into.
4. Jump to **Priority Queue** or **Weekly Micro Plan** right after to show
   it ranked and placed into a Shadow Block alongside the existing backlog.

A **session log** at the bottom of the tab shows everything logged so far
(department, task ID, summary, week, timestamp) — a running audit trail for
the demo. A **"Reset demo data"** control wipes everything and regenerates a
clean synthetic baseline, for repeatable runs between judge groups.

`src/operator_input.py` is the module behind this — intentionally
independent of `sync.py`/the live-API adapter, so the demo never depends on
network access or the resync timer.

**Why this matters for the sync/live-API design above:** the synthetic
generators (`gen_tms()` etc.) overwrite their CSVs as a side effect when
called. `app.py` now only calls `sync_all()` once, to bootstrap a fresh
dataset (or after an explicit reset) — never again on the resync timer in
synthetic/demo mode — specifically so a periodic resync can never silently
wipe faults an operator just logged mid-demo. (Live API mode is unaffected:
`RailwayAPIAdapter` never writes CSVs itself, so it keeps polling normally.)

## Shadow Block Gantt (operator-facing view)

The **📊 Shadow Block Gantt** tab is a dedicated, day-level chart — the view
a section controller would actually work from, separate from the Weekly
Micro Plan's week-wide summary chart:

- One row per track section + line (6 fixed rows, same every day), not per
  week — a week-wide time axis squeezed 60–240 minute blocks into invisible
  slivers, which is what this tab fixes.
- Pick a week, then a specific day within it.
- Each Shadow Block draws as two layers: a faint bar for the *full* derived
  block window (including the safety buffer), and a solid bar for the
  *utilized* portion sized to the tasks actually bundled in — so the buffer
  is visible, not just a number in the code.
- **Gold bars = multiple departments sharing one possession** — the single
  clearest visual for "this is what coordination looks like" in a demo.
- A detail table underneath lists every task in every block for that day.

This tab is purely additive — the Weekly Micro Plan tab's own chart is
unchanged.

## Known simplifications (be upfront about these if asked)

- `MASTER_ASSET_MAP` is a static dict standing in for a real spatial/GIS
  database — fine for a prototype, but call it out as a production gap.
- The ML label is a proxy (derived from the rule-based score) since there's
  no historical failure dataset — mention this honestly if a judge asks
  "what is the model actually trained on?"
- With very small task counts the code automatically falls back to the
  transparent rule-based score instead of XGBoost (XGBoost needs more rows
  to do anything meaningful) — this is intentional, not a bug.
- Train timetable / goods forecast are synthetic (Faker-seeded random
  movements), standing in for a live COA feed.
- Block Demand Date is generated (or entered) rather than negotiated
  back-and-forth with Control Office — real block demand approval is
  typically an interactive process, not a single-shot request.
- "Is Block Integrated" and "Enforcement Condition = Fixed Time Slot" are
  modeled as strong objective weights, not hard CP-SAT constraints — a
  deliberate choice so one bad/conflicting request can't make the whole
  weekly solve infeasible, but it means neither is a 100% guarantee in an
  extreme-contention scenario.
