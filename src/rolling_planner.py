"""
rolling_planner.py
-------------------
Runs the Weekly Micro Plan / Monthly Macro Plan described in the pitch:

  - Weekly Micro Plan: each of the 4 weeks in the horizon gets its own
    CP-SAT run, using only that week's derived corridors and whatever tasks
    are (a) already known/backlog or newly "reported" as of that week, and
    (b) not already scheduled in a previous week. Anything left unscheduled
    rolls forward into next week's candidate pool -- genuine rolling
    re-planning, not four independent snapshots.

  - Monthly Macro Plan: the roll-up of all 4 weekly runs -- tasks scheduled,
    blocks opened vs. the uncoordinated baseline, backlog burn-down, and how
    many blocks combined 2+ departments, week over week.

This is also where the "Dynamic Railway Operations" story shows up: TMS/SMMS/
TDMS emergency defects tagged `available_from_week` > 1 simply don't exist in
the candidate pool until their week arrives, at which point they compete for
priority alongside whatever backlog is left -- exactly the "new emergency
defect -> recalculate -> re-run solver" loop from the pitch, driven by real
data instead of a slide animation.

Run directly for a demo:
    python src/rolling_planner.py
"""

import os
from datetime import timedelta

import pandas as pd

from data_generator import MONTH_START
from spatial_layer import build_unified_tasks
from priority_model import score_tasks_df
from scheduler import build_schedule, load_corridors

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def run_rolling(weeks: int = 4):
    all_tasks = build_unified_tasks()
    corridors_all = pd.read_csv(os.path.join(DATA_DIR, "coa_corridors.csv"))

    remaining = all_tasks.copy()
    weekly_schedules = []
    weekly_summaries = []

    for wk in range(1, weeks + 1):
        current_date = MONTH_START + timedelta(days=(wk - 1) * 7)
        candidates = remaining[remaining["available_from_week"] <= wk]

        wk_corridors = load_corridors(corridors_all[corridors_all["week_number"] == wk])

        if candidates.empty:
            scored = candidates
        else:
            scored = score_tasks_df(candidates, current_date=current_date, corridors_df=corridors_all)

        sched, summary = build_schedule(scored, wk_corridors)
        sched["week"] = wk
        summary["week"] = wk

        scheduled_ids = set(sched.loc[sched["status"].str.startswith("SCHEDULED"), "task_id"]) if len(sched) else set()
        remaining = remaining[~remaining["task_id"].isin(scheduled_ids)]

        outstanding_now = len(remaining[remaining["available_from_week"] <= wk])
        summary["backlog_remaining"] = outstanding_now
        summary["new_emergencies_this_week"] = int((candidates["is_emergency"]).sum()) if len(candidates) else 0

        weekly_schedules.append(sched)
        weekly_summaries.append(summary)

    rolling_schedule = pd.concat(weekly_schedules, ignore_index=True) if weekly_schedules else pd.DataFrame()
    monthly_summary = pd.DataFrame(weekly_summaries)

    rolling_schedule.to_csv(os.path.join(DATA_DIR, "rolling_schedule.csv"), index=False)
    monthly_summary.to_csv(os.path.join(DATA_DIR, "monthly_summary.csv"), index=False)
    return rolling_schedule, monthly_summary


if __name__ == "__main__":
    rolling_schedule, monthly_summary = run_rolling()
    print("Weekly Micro Plan summary:\n")
    print(monthly_summary.to_string(index=False))

    total_scheduled = monthly_summary["tasks_scheduled"].sum()
    total_baseline = monthly_summary["baseline_corridors"].sum()
    total_actual = monthly_summary["corridors_used"].sum()
    reduction = round((total_baseline - total_actual) / total_baseline * 100, 1) if total_baseline else 0.0
    print(f"\nMonthly Macro Plan: {total_scheduled} tasks scheduled across the horizon, "
          f"{total_actual} Shadow Blocks opened (vs {total_baseline} if uncoordinated) "
          f"-> {reduction}% fewer blocks / less track downtime overall.")
    print(f"Backlog remaining at month end: {monthly_summary['backlog_remaining'].iloc[-1]} tasks.")
