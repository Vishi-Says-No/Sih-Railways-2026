"""
corridor_availability.py
-------------------------
Derives maintenance block availability from the Train Time Table (passenger)
and the Goods Train Forecast (freight) supplied by the Control Office
Application -- instead of asserting a hand-picked list of empty windows.

For every (date, section, line) we:
  1. Merge passenger + goods movements into a sorted list of "busy" intervals.
  2. Find the gaps between them -- these are the only physically possible
     maintenance block windows.
  3. Keep the single largest gap per (date, section, line) as that day's
     candidate corridor (keeps the corridor count demo-sized).
  4. Label each corridor's traffic_density (HIGH/MEDIUM/LOW) from how busy
     that section was that day -- this becomes the "operational impact"
     signal the ML priority scorer uses.

Each corridor is also tagged with its ISO week (1-4) so the scheduler /
rolling planner can run week-by-week (Weekly Micro Plan) or across the whole
horizon at once (Monthly Macro Plan).

Run directly for a demo:
    python src/corridor_availability.py
"""

import os
from datetime import datetime, timedelta, time

import pandas as pd

from data_generator import SECTIONS, LINES, MONTH_START, HORIZON_DAYS

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

MIN_BLOCK_MINS = 60   # shortest window worth offering as a maintenance block
MAX_BLOCK_MINS = 240  # cap a single block so it stays operationally realistic


def _load_feeds():
    tt = pd.read_csv(os.path.join(DATA_DIR, "train_timetable.csv"))
    gf = pd.read_csv(os.path.join(DATA_DIR, "goods_forecast.csv"))
    tt = tt.rename(columns={"dep_time": "start", "arr_time": "end"})[["date", "section", "line", "start", "end"]]
    gf = gf.rename(columns={"window_start": "start", "window_end": "end"})[["date", "section", "line", "start", "end"]]
    busy = pd.concat([tt, gf], ignore_index=True)
    busy["start"] = pd.to_datetime(busy["start"])
    busy["end"] = pd.to_datetime(busy["end"])
    return busy


def _merge_intervals(intervals):
    intervals = sorted(intervals)
    merged = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _largest_gap(day, busy_pairs):
    day_start = datetime.combine(day, time(0, 0))
    day_end = day_start + timedelta(days=1)
    merged = _merge_intervals(busy_pairs)

    gaps = []
    cursor = day_start
    for s, e in merged:
        if s > cursor:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if day_end > cursor:
        gaps.append((cursor, day_end))

    gaps = [(s, e) for s, e in gaps if (e - s).total_seconds() / 60 >= MIN_BLOCK_MINS]
    if not gaps:
        return None
    best = max(gaps, key=lambda g: (g[1] - g[0]))
    s, e = best
    e = min(e, s + timedelta(minutes=MAX_BLOCK_MINS))
    return s, e


def compute_corridors(days=HORIZON_DAYS) -> pd.DataFrame:
    busy = _load_feeds()
    rows = []
    corridor_i = 1

    # movement-count per (date, section) -> feeds the traffic_density label
    counts = busy.groupby(["date", "section"]).size().rename("movements").reset_index()
    q_lo, q_hi = counts["movements"].quantile([0.33, 0.67])

    def _density(n):
        if n >= q_hi:
            return "HIGH"
        if n >= q_lo:
            return "MEDIUM"
        return "LOW"

    counts["traffic_density"] = counts["movements"].apply(_density)
    density_map = counts.set_index(["date", "section"])["traffic_density"].to_dict()
    move_map = counts.set_index(["date", "section"])["movements"].to_dict()

    for d in range(days):
        day = MONTH_START + timedelta(days=d)
        date_str = day.isoformat()
        week_number = min((d // 7) + 1, 4)
        for section in SECTIONS:
            for line in LINES:
                subset = busy[(busy["date"] == date_str) & (busy["section"] == section) & (busy["line"] == line)]
                pairs = list(zip(subset["start"], subset["end"]))
                gap = _largest_gap(day, pairs)
                if gap is None:
                    continue
                s, e = gap
                rows.append({
                    "corridor_id": f"COA-{corridor_i}",
                    "date": date_str,
                    "week_number": week_number,
                    "block_section": section,
                    "line": line,
                    "window_start": s.strftime("%Y-%m-%d %H:%M"),
                    "window_end": e.strftime("%Y-%m-%d %H:%M"),
                    "duration_mins": int((e - s).total_seconds() / 60),
                    "traffic_density": density_map.get((date_str, section), "LOW"),
                    "movements_that_day": int(move_map.get((date_str, section), 0)),
                })
                corridor_i += 1
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(DATA_DIR, "coa_corridors.csv"), index=False)
    return df


if __name__ == "__main__":
    corridors = compute_corridors()
    print(f"Derived {len(corridors)} candidate maintenance blocks from the timetable + goods forecast "
          f"across {HORIZON_DAYS} days.\n")
    print(corridors.groupby("week_number")["duration_mins"].agg(["count", "mean"]).round(1))
    print("\nTraffic density mix:")
    print(corridors["traffic_density"].value_counts())
