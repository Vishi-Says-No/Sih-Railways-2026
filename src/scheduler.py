"""
scheduler.py
------------
Engine 2: OR-Tools CP-SAT Scheduler.

Takes ML-scored tasks and the derived corridor windows, then decides which
tasks get bundled into which corridor -- the deterministic, constraint-based
counterpart to the ML scorer. This produces the "Shadow Block" (a.k.a. Mega
Block): multiple departments doing overlapping work inside one shared
possession of the track.

Assignment is now driven by the Block Demand fields (see Block_Details.xlsx
/ data_generator.py's `_block_demand_fields`), not just section+criticality:

  - Section + Line + Block Demand Date are ALL hard-matched against a
    corridor (block_section, line, date) -- a request only competes for the
    corridor actually covering the day/line it asked for, not "any day this
    week". In practice this usually leaves exactly one candidate corridor
    per task.
  - Use Corridor = "No" pulls a request out of the shared-corridor pool
    entirely -- it's granted a dedicated standalone possession on its
    requested date instead of competing for shared capacity.
  - Is Block Integrated = "Yes" + a named department is a strong reward (not
    a hard requirement, to avoid making the whole solve infeasible) for
    landing in the same corridor as a task from that department -- the
    requester's own stated intent to coordinate, on top of the scheduler's
    general bundling incentive.
  - Enforcement Condition = "Fixed Time Slot" is a very heavy scheduling
    priority -- these requests are treated as effectively non-negotiable,
    same spirit as "Is Block Integrated" being a strong-not-hard constraint
    so a single bad request can't blow up the whole solve.

Objective:
  + maximize total criticality score scheduled
  + BUNDLE_BONUS for every department present in a corridor that already has
    another task in it -> rewards combining Track+Signal+Traction work into
    ONE possession instead of three
  + INTEGRATION_BONUS when a task's own requested integration department
    actually shows up in the same corridor
  + FIXED_SLOT_BONUS for every "Fixed Time Slot" request that gets scheduled
  - CORRIDOR_PENALTY for every corridor actually opened -> rewards fitting
    the same work into fewer distinct blocks (i.e. less total track downtime)

After solving we also compute the "uncoordinated baseline" (what it would
have cost if every task got its own separate block) so the dashboard can
show the real, computed block-reduction / downtime-reduction percentage.

Run directly for a demo:
    python src/scheduler.py
"""

import os
import pandas as pd
from ortools.sat.python import cp_model

from priority_model import score_tasks

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

BUFFER_MINS = 15         # safety padding per corridor, absorbs freight timing shifts
BUNDLE_BONUS = 120        # reward per extra department coordinated into one corridor
CORRIDOR_PENALTY = 40     # cost per corridor actually opened (drives consolidation)
INTEGRATION_BONUS = 180   # reward for fulfilling a task's own requested department integration
FIXED_SLOT_BONUS = 5000   # heavy priority for "Fixed Time Slot" requests (soft-guarantee, not a hard constraint)

RESULT_COLUMNS = [
    "task_id", "dept", "criticality_score", "duration_mins",
    "corridor_id", "block_section", "line", "window_start", "window_end", "status",
    "rly", "div", "direction", "block_demand_date",
    "is_block_integrated", "integrated_department", "integration_fulfilled",
    "enforcement_condition", "use_corridor", "reason_code",
]


def load_corridors(corridors_df: pd.DataFrame = None) -> pd.DataFrame:
    if corridors_df is None:
        corridors_df = pd.read_csv(os.path.join(DATA_DIR, "coa_corridors.csv"))
    corridors_df = corridors_df.copy()
    corridors_df["usable_mins"] = corridors_df["duration_mins"] - BUFFER_MINS
    corridors_df = corridors_df[corridors_df["usable_mins"] > 0].reset_index(drop=True)
    return corridors_df


def _base_row(task) -> dict:
    integrated_department = task.get("integrated_department")
    if pd.isna(integrated_department):
        integrated_department = ""
    return {
        "task_id": task["task_id"],
        "dept": task["dept"],
        "criticality_score": task["criticality_score"],
        "duration_mins": task["duration_mins"],
        "rly": task.get("rly"),
        "div": task.get("div"),
        "direction": task.get("direction"),
        "block_demand_date": task.get("block_demand_date"),
        "is_block_integrated": bool(task.get("is_block_integrated")),
        "integrated_department": integrated_department,
        "enforcement_condition": task.get("enforcement_condition"),
        "use_corridor": bool(task.get("use_corridor", True)),
        "reason_code": task.get("reason_code"),
    }


def _standalone_rows(tasks: pd.DataFrame) -> list:
    """Use Corridor = 'No' -> a dedicated possession outside the shared
    corridor pool. Granted directly (no CP-SAT competition), starting at
    00:00 on the requested date -- a full/exclusive block, not squeezed
    into a timetable gap."""
    rows = []
    for _, task in tasks.iterrows():
        row = _base_row(task)
        start = f'{task["block_demand_date"]} 00:00'
        end = (pd.Timestamp(task["block_demand_date"]) + pd.Timedelta(minutes=int(task["duration_mins"]))).strftime("%Y-%m-%d %H:%M")
        row.update({
            "corridor_id": f"STANDALONE-{task['task_id']}",
            "block_section": task["coa_section"],
            "line": task["line"],
            "window_start": start,
            "window_end": end,
            "status": "SCHEDULED (Standalone — dedicated possession)",
            "integration_fulfilled": False,
        })
        rows.append(row)
    return rows


def build_schedule(tasks: pd.DataFrame, corridors: pd.DataFrame):
    """Returns (schedule_df, summary_dict)."""
    tasks = tasks.reset_index(drop=True)
    corridors = corridors.reset_index(drop=True)

    if tasks.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS), {
            "tasks_scheduled": 0, "tasks_unscheduled": 0, "corridors_used": 0,
            "baseline_corridors": 0, "downtime_reduction_pct": 0.0, "multi_dept_corridors": 0,
            "standalone_blocks": 0, "integration_requests_fulfilled": 0,
        }

    # Use Corridor = "No" tasks never enter the shared pool / CP-SAT model at all.
    if "use_corridor" in tasks.columns:
        use_corridor_mask = tasks["use_corridor"]
        if use_corridor_mask.dtype != bool:
            use_corridor_mask = use_corridor_mask.astype(bool)
    else:
        use_corridor_mask = pd.Series(True, index=tasks.index)
    standalone_tasks = tasks[~use_corridor_mask]
    pool_tasks = tasks[use_corridor_mask].reset_index(drop=True)

    standalone_results = _standalone_rows(standalone_tasks)

    if pool_tasks.empty or corridors.empty:
        results = standalone_results + [
            dict(_base_row(t), corridor_id=None, block_section=t["coa_section"], line=t["line"],
                 window_start=None, window_end=None, status="UNSCHEDULED (no corridor this window)",
                 integration_fulfilled=False)
            for _, t in pool_tasks.iterrows()
        ]
        schedule = pd.DataFrame(results, columns=RESULT_COLUMNS)
        return schedule, _summarize(schedule)

    model = cp_model.CpModel()
    depts = sorted(pool_tasks["dept"].unique())

    assign = {}
    for i in pool_tasks.index:
        for j in corridors.index:
            assign[i, j] = model.NewBoolVar(f"assign_t{i}_c{j}")

    # Constraint 1: a task goes into at most one corridor -- matching its own
    # section and line always. Date matching depends on how firm the request is:
    #   - "Fixed Time Slot"  -> exact date only (genuinely non-negotiable).
    #   - everything else    -> the requested date or any LATER date. A Block
    #     Demand Date means "I need this by X", not "only on X" -- and this is
    #     what makes rolling re-planning actually work: a task that misses its
    #     original date (whole-backlog overdue items very often share one
    #     early date) rolls into next week's pool and can still match that
    #     week's corridors, instead of being permanently stranded because its
    #     stale requested date no longer exists in a later week's slice.
    for i, task in pool_tasks.iterrows():
        exact_date_only = task.get("enforcement_condition") == "Fixed Time Slot"
        req_date = str(task["block_demand_date"])
        matching_js = [
            j for j in corridors.index
            if corridors.loc[j, "block_section"] == task["coa_section"]
            and corridors.loc[j, "line"] == task["line"]
            and (str(corridors.loc[j, "date"]) == req_date if exact_date_only
                 else str(corridors.loc[j, "date"]) >= req_date)
        ]
        non_matching_js = [j for j in corridors.index if j not in matching_js]
        model.Add(sum(assign[i, j] for j in corridors.index) <= 1)
        for j in non_matching_js:
            model.Add(assign[i, j] == 0)

    # Constraint 2: total duration in a corridor <= usable window.
    for j, corridor in corridors.iterrows():
        model.Add(
            sum(assign[i, j] * int(pool_tasks.loc[i, "duration_mins"]) for i in pool_tasks.index)
            <= int(corridor["usable_mins"])
        )

    # dept_used[j,d]: solver may only claim the bundling bonus if truly justified.
    dept_used = {}
    for j in corridors.index:
        for d in depts:
            dept_used[j, d] = model.NewBoolVar(f"dept_used_c{j}_{d}")
            dept_rows = [i for i in pool_tasks.index if pool_tasks.loc[i, "dept"] == d]
            model.Add(sum(assign[i, j] for i in dept_rows) >= dept_used[j, d])

    # corridor_used[j]: forced to 1 the moment any task lands there, so the
    # consolidation penalty can't be dodged.
    corridor_used = {}
    for j in corridors.index:
        corridor_used[j] = model.NewBoolVar(f"corridor_used_{j}")
        for i in pool_tasks.index:
            model.Add(corridor_used[j] >= assign[i, j])

    # integrated_ok[i,j]: a task that explicitly requested integration with
    # department D gets a bonus, but ONLY when it truly lands alongside a
    # task from D in the same corridor (AND-linearization: capped by both
    # assign[i,j] and dept_used[j,D]).
    integrated_ok = {}
    for i, task in pool_tasks.iterrows():
        target_dept = task.get("integrated_department")
        if pd.isna(target_dept):
            target_dept = ""
        if not task.get("is_block_integrated") or not target_dept:
            continue
        if target_dept not in depts:
            continue
        for j in corridors.index:
            if corridors.loc[j, "block_section"] != task["coa_section"] or corridors.loc[j, "line"] != task["line"]:
                continue
            integrated_ok[i, j] = model.NewBoolVar(f"integrated_ok_t{i}_c{j}")
            model.Add(integrated_ok[i, j] <= assign[i, j])
            model.Add(integrated_ok[i, j] <= dept_used[j, target_dept])

    fixed_slot_weight = {
        i: FIXED_SLOT_BONUS for i, task in pool_tasks.iterrows()
        if task.get("enforcement_condition") == "Fixed Time Slot"
    }

    model.Maximize(
        sum(assign[i, j] * int(pool_tasks.loc[i, "criticality_score"] * 10 + fixed_slot_weight.get(i, 0))
            for i in pool_tasks.index for j in corridors.index)
        + BUNDLE_BONUS * sum(dept_used[j, d] for j in corridors.index for d in depts)
        + INTEGRATION_BONUS * sum(integrated_ok.values())
        - CORRIDOR_PENALTY * sum(corridor_used[j] for j in corridors.index)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 8
    status = solver.Solve(model)

    results = list(standalone_results)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for i, task in pool_tasks.iterrows():
            placed = False
            for j, corridor in corridors.iterrows():
                if solver.Value(assign[i, j]) == 1:
                    fulfilled = bool(
                        task.get("is_block_integrated") and task.get("integrated_department")
                        and (i, j) in integrated_ok and solver.Value(integrated_ok[i, j]) == 1
                    )
                    row = _base_row(task)
                    row.update({
                        "corridor_id": corridor["corridor_id"],
                        "block_section": corridor["block_section"],
                        "line": corridor["line"],
                        "window_start": corridor["window_start"],
                        "window_end": corridor["window_end"],
                        "status": "SCHEDULED (Shadow Block)",
                        "integration_fulfilled": fulfilled,
                    })
                    results.append(row)
                    placed = True
                    break
            if not placed:
                exact_date_only = task.get("enforcement_condition") == "Fixed Time Slot"
                req_date = str(task["block_demand_date"])
                has_matching_corridor = any(
                    corridors.loc[j, "block_section"] == task["coa_section"]
                    and corridors.loc[j, "line"] == task["line"]
                    and (str(corridors.loc[j, "date"]) == req_date if exact_date_only
                         else str(corridors.loc[j, "date"]) >= req_date)
                    for j in corridors.index
                )
                reason = "no capacity left this window" if has_matching_corridor else "no corridor derived for the requested date"
                row = _base_row(task)
                row.update({
                    "corridor_id": None, "block_section": task["coa_section"], "line": task["line"],
                    "window_start": None, "window_end": None,
                    "status": f"UNSCHEDULED ({reason})", "integration_fulfilled": False,
                })
                results.append(row)
    else:
        raise RuntimeError("CP-SAT solver found no feasible solution.")

    schedule = pd.DataFrame(results, columns=RESULT_COLUMNS)
    return schedule, _summarize(schedule)


def _summarize(schedule: pd.DataFrame) -> dict:
    scheduled = schedule[schedule["status"].str.startswith("SCHEDULED")]
    pool_scheduled = scheduled[~scheduled["corridor_id"].astype(str).str.startswith("STANDALONE")]
    standalone_scheduled = scheduled[scheduled["corridor_id"].astype(str).str.startswith("STANDALONE")]

    baseline_corridors = len(pool_scheduled)  # uncoordinated: 1 block per pooled task
    actual_corridors = pool_scheduled["corridor_id"].nunique() if len(pool_scheduled) else 0
    downtime_reduction_pct = (
        round((baseline_corridors - actual_corridors) / baseline_corridors * 100, 1)
        if baseline_corridors else 0.0
    )
    multi_dept_corridors = (
        pool_scheduled.groupby("corridor_id")["dept"].nunique().gt(1).sum() if len(pool_scheduled) else 0
    )
    integration_requests = scheduled[scheduled["is_block_integrated"]]
    integration_fulfilled = int(integration_requests["integration_fulfilled"].sum()) if len(integration_requests) else 0

    return {
        "tasks_scheduled": len(scheduled),
        "tasks_unscheduled": len(schedule) - len(scheduled),
        "corridors_used": int(actual_corridors),
        "baseline_corridors": int(baseline_corridors),
        "downtime_reduction_pct": downtime_reduction_pct,
        "multi_dept_corridors": int(multi_dept_corridors),
        "standalone_blocks": len(standalone_scheduled),
        "integration_requests_fulfilled": integration_fulfilled,
        "integration_requests_total": int(len(integration_requests)),
    }


def run():
    """Backward-compatible single-shot entry point: scores + schedules the
    whole backlog against the whole month's corridors in one go."""
    tasks = score_tasks()
    corridors = load_corridors()
    schedule, summary = build_schedule(tasks, corridors)
    schedule.to_csv(os.path.join(DATA_DIR, "shadow_block_schedule.csv"), index=False)
    return schedule, summary


if __name__ == "__main__":
    schedule, summary = run()
    print(f"Scheduled {summary['tasks_scheduled']}/{summary['tasks_scheduled']+summary['tasks_unscheduled']} "
          f"tasks into {summary['corridors_used']} Shadow Blocks + {summary['standalone_blocks']} standalone possessions "
          f"(would have needed {summary['baseline_corridors']} separate blocks without coordination -> "
          f"{summary['downtime_reduction_pct']}% fewer blocks; "
          f"{summary['multi_dept_corridors']} blocks combine 2+ departments; "
          f"{summary['integration_requests_fulfilled']}/{summary['integration_requests_total']} requested integrations fulfilled).\n")

    scheduled = schedule[schedule["status"].str.startswith("SCHEDULED")]
    pool_scheduled = scheduled[~scheduled["corridor_id"].astype(str).str.startswith("STANDALONE")]
    for corridor_id, grp in pool_scheduled.groupby("corridor_id"):
        depts = ", ".join(sorted(grp["dept"].unique()))
        total_time = grp["duration_mins"].sum()
        print(f"{corridor_id} [{grp['block_section'].iloc[0]} · {grp['line'].iloc[0]}] "
              f"({grp['window_start'].iloc[0]} - {grp['window_end'].iloc[0]}): "
              f"{len(grp)} tasks from [{depts}], {total_time} mins used")

    unscheduled = schedule[~schedule["status"].str.startswith("SCHEDULED")]
    if len(unscheduled):
        print(f"\n{len(unscheduled)} tasks unscheduled this run (carried to next micro-schedule):")
        print(unscheduled[["task_id", "dept", "criticality_score", "status"]].to_string(index=False))
