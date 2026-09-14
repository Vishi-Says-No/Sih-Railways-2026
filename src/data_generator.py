"""
data_generator.py
------------------
Generates synthetic data mimicking the siloed railway systems:

    TMS   - Track Management System        (continuous KM chainage)
    SMMS  - Signal Maintenance Mgmt System (discrete station/asset IDs)
    TDMS  - Traction Distribution Mgmt Sys (OHE masts / sub-sectors)
    COA   - Control Office Application     (train timetable + goods forecast)

Two upgrades over a "flat" synthetic dataset:

1. Every defect carries `date_reported` + a severity-based SLA -> `due_date`,
   so "overdue maintenance" is a real, computable fact (not a label).
2. Defects are split into a BACKLOG (available from week 1) and a handful of
   EMERGENCY defects that only "appear" in later weeks -- this is what lets
   the rolling planner demonstrate re-prioritisation when new defects show up
   mid-month, per the "Dynamic Railway Operations" requirement.

COA is no longer a hand-picked list of empty windows. Instead we generate a
real passenger Train Time Table + a Goods Train Forecast (src/data_generator
below), and `corridor_availability.py` *derives* block windows from the gaps
between those movements -- i.e. block availability is computed from the
timetable + freight forecast, not asserted.

Run:
    python src/data_generator.py
Outputs CSVs into ../data/
"""

import os
import random
from datetime import datetime, timedelta, date, time

import pandas as pd
from faker import Faker

fake = Faker()
random.seed(42)
Faker.seed(42)

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(OUT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Shared "world"
# ---------------------------------------------------------------------------
SECTIONS = ["STN_A-STN_B", "STN_B-STN_C", "STN_C-STN_D"]
LINES = ["UP_MAIN", "DN_MAIN"]

MONTH_START = date(2026, 9, 1)   # rolling horizon start (week 1, day 0)
HORIZON_DAYS = 28                # 4 weeks -> supports weekly + monthly planning

SLA_DAYS = {"CRITICAL": 3, "URGENT": 7, "ROUTINE": 21}

# Emergency defects that appear mid-horizon (dept, week they appear in).
# This is what the "dynamic / rolling" behaviour re-plans around.
EMERGENCIES = [
    ("TMS", 2), ("SMMS", 2),
    ("TDMS", 3),
    ("TMS", 4), ("SMMS", 4), ("TDMS", 4),
]

MASTER_ASSET_MAP = {
    "SMMS_Assets": {
        "104A": {"km": 142.100, "section": "STN_A-STN_B", "line": "UP_MAIN"},
        "TC-204": {"km": 142.120, "section": "STN_A-STN_B", "line": "UP_MAIN"},
        "S-12": {"km": 155.000, "section": "STN_B-STN_C", "line": "DN_MAIN"},
        "106B": {"km": 168.300, "section": "STN_C-STN_D", "line": "UP_MAIN"},
        "TC-310": {"km": 169.050, "section": "STN_C-STN_D", "line": "UP_MAIN"},
    },
    "TDMS_Masts": {
        "142/18": {"km": 142.180, "section": "STN_A-STN_B", "line": "UP_MAIN"},
        "143/04": {"km": 143.040, "section": "STN_A-STN_B", "line": "UP_MAIN"},
        "158/10": {"km": 158.100, "section": "STN_B-STN_C", "line": "DN_MAIN"},
        "170/02": {"km": 170.020, "section": "STN_C-STN_D", "line": "UP_MAIN"},
    },
}

# ---------------------------------------------------------------------------
# Block Demand fields -- modeled on Railways' official "Block Details By
# Applications" form (see Block_Details.xlsx): Application, Rly, Div,
# Section, Line, Direction, Block Demand Date, Is Block Integrated, If
# integrated Block then select department, Enforcement Condition, Use
# Corridor, Reason Code. "Application" == our `dept`; Section/Line already
# existed. Everything below is new and is what the CP-SAT scheduler in
# scheduler.py now actually reads and enforces -- not display-only fields.
# ---------------------------------------------------------------------------
RAILWAY = "NR"    # Northern Railway -- demo constant (single zone/division scope)
DIVISION = "DLI"  # Delhi Division

DIRECTION_BY_LINE = {"UP_MAIN": "UP", "DN_MAIN": "DOWN"}

ENFORCEMENT_CONDITIONS = ["Flexible", "Fixed Time Slot"]
USE_CORRIDOR_OPTIONS = ["Yes", "No"]

# Representative reason-code set (not a claim to reproduce an exact official
# IR code table -- see README for this caveat).
REASON_CODES = ["EMERGENCY_REPAIR", "STATUTORY_SAFETY_INSPECTION", "PLANNED_MAINTENANCE",
                 "PERIODIC_INSPECTION", "RENEWAL_WORK"]

OTHER_DEPTS = {"TMS": ["SMMS", "TDMS"], "SMMS": ["TMS", "TDMS"], "TDMS": ["TMS", "SMMS"]}


def _reason_code(severity_key: str, is_emergency: bool, asset_age_years: int) -> str:
    if is_emergency:
        return "EMERGENCY_REPAIR"
    if severity_key == "CRITICAL":
        return "STATUTORY_SAFETY_INSPECTION"
    if severity_key == "URGENT":
        return "PLANNED_MAINTENANCE"
    if asset_age_years >= 30 and random.random() < 0.4:
        return "RENEWAL_WORK"
    return "PERIODIC_INSPECTION"


def _block_demand_fields(dept: str, line: str, severity_key: str, due: date, is_emergency: bool,
                          asset_age_years: int) -> dict:
    """Everything from the Block Demand form beyond Application/Section/Line,
    which the caller already has. `due` is the SLA due date (a date object)
    -- Block Demand Date is anchored on it (clipped into the horizon), since
    that's what a requester would actually ask for: "I want this block by
    when it's due." This field is what the scheduler now hard-matches
    against a specific day's derived corridor, instead of any day in the
    task's available week."""
    direction = DIRECTION_BY_LINE.get(line, "UP")

    horizon_end = MONTH_START + timedelta(days=HORIZON_DAYS - 1)
    demand_date = due
    if demand_date < MONTH_START:
        demand_date = MONTH_START
    elif demand_date > horizon_end:
        demand_date = horizon_end

    is_integrated = random.random() < 0.45
    integrated_department = random.choice(OTHER_DEPTS[dept]) if is_integrated else ""

    enforcement_condition = "Fixed Time Slot" if (is_emergency or random.random() < 0.12) else "Flexible"
    use_corridor = random.random() >= 0.10  # ~10% are standalone/dedicated possessions, outside the shared pool

    return {
        "rly": RAILWAY,
        "div": DIVISION,
        "direction": direction,
        "block_demand_date": demand_date.isoformat(),
        "is_block_integrated": is_integrated,
        "integrated_department": integrated_department,
        "enforcement_condition": enforcement_condition,
        "use_corridor": use_corridor,
        "reason_code": _reason_code(severity_key, is_emergency, asset_age_years),
    }


def _due_date(reported: date, severity: str) -> date:
    return reported + timedelta(days=SLA_DAYS.get(severity, 14))


def _backlog_report_date() -> date:
    """Backlog defects already exist before the planning horizon opens."""
    return MONTH_START - timedelta(days=random.randint(3, 25))


def _emergency_report_date(week: int) -> date:
    return MONTH_START + timedelta(days=(week - 1) * 7)


def _n_emergencies(dept: str):
    """Weeks in which `dept` gets a fresh emergency defect."""
    return [wk for d, wk in EMERGENCIES if d == dept]


# ---------------------------------------------------------------------------
# TMS — Track defects
# ---------------------------------------------------------------------------
def gen_tms(n_backlog=34):
    rows = []
    severities = ["CRITICAL", "URGENT", "ROUTINE"]
    defect_types = ["Rail Fracture", "Ballast Fouling", "Weld Failure", "Sleeper Crack", "Fishplate Loose"]
    hotspots = [142.10, 142.15, 158.05, 169.00]

    emergency_weeks = _n_emergencies("TMS")
    total = n_backlog + len(emergency_weeks)

    for i in range(total):
        is_emergency = i >= n_backlog
        km = random.choice(hotspots) if i < len(hotspots) else round(random.uniform(140, 175), 3)
        end_km = round(km + random.choice([0.0, 0.05, 0.1, 0.3]), 3)
        severity = random.choice(severities) if not is_emergency else "CRITICAL"
        if is_emergency:
            wk = emergency_weeks[i - n_backlog]
            reported = _emergency_report_date(wk)
            avail_week = wk
        else:
            reported = _backlog_report_date()
            avail_week = 1
        due = _due_date(reported, severity)
        line = random.choice(LINES)
        age = random.randint(1, 40)
        row = {
            "defect_id": f"TMS-{8000+i}",
            "date_reported": reported.isoformat(),
            "section": random.choice(SECTIONS),
            "line_type": line,
            "start_km": km,
            "end_km": end_km,
            "defect_type": random.choice(defect_types) if not is_emergency else "Rail Fracture",
            "severity": severity,
            "asset_age_years": age,
            "est_duration_mins": random.choice([60, 90, 120, 150, 180]),
            "due_date": due.isoformat(),
            "available_from_week": avail_week,
            "is_emergency": is_emergency,
        }
        row.update(_block_demand_fields("TMS", line, severity, due, is_emergency, age))
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, "tms_defects.csv"), index=False)
    return df


# ---------------------------------------------------------------------------
# SMMS — Signal defects
# ---------------------------------------------------------------------------
def gen_smms(n_backlog=36):
    rows = []
    gear_types = ["Point Machine", "Track Circuit", "Signal", "Level Crossing Gate"]
    priorities = ["CRITICAL", "URGENT", "ROUTINE"]
    asset_ids = list(MASTER_ASSET_MAP["SMMS_Assets"].keys())

    emergency_weeks = _n_emergencies("SMMS")
    total = n_backlog + len(emergency_weeks)

    for i in range(total):
        is_emergency = i >= n_backlog
        asset_id = asset_ids[i % len(asset_ids)]
        asset_info = MASTER_ASSET_MAP["SMMS_Assets"].get(asset_id, {})
        station = asset_info.get("section", random.choice(SECTIONS)).split("-")[0]
        line = asset_info.get("line", random.choice(LINES))
        priority = random.choice(priorities) if not is_emergency else "CRITICAL"
        if is_emergency:
            wk = emergency_weeks[i - n_backlog]
            reported = _emergency_report_date(wk)
            avail_week = wk
        else:
            reported = _backlog_report_date()
            avail_week = 1
        due = _due_date(reported, priority)
        age = random.randint(1, 25)
        row = {
            "fault_id": f"SMM-{440+i}",
            "date_reported": reported.isoformat(),
            "station_id": station,
            "gear_type": random.choice(gear_types),
            "asset_id": asset_id,
            "fault_description": fake.sentence(nb_words=5),
            "priority": priority,
            "asset_age_years": age,
            "req_clearance_mins": random.choice([30, 45, 60, 90]),
            "due_date": due.isoformat(),
            "available_from_week": avail_week,
            "is_emergency": is_emergency,
        }
        row.update(_block_demand_fields("SMMS", line, priority, due, is_emergency, age))
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, "smms_defects.csv"), index=False)
    return df


# ---------------------------------------------------------------------------
# TDMS — Traction / OHE defects
# ---------------------------------------------------------------------------
def gen_tdms(n_backlog=25):
    rows = []
    issues = ["Cantilever Insulator Flash", "Dropper Wire Snapped", "OHE Tension Low", "Bird Nest Removal"]
    mast_ids = list(MASTER_ASSET_MAP["TDMS_Masts"].keys())

    emergency_weeks = _n_emergencies("TDMS")
    total = n_backlog + len(emergency_weeks)

    for i in range(total):
        is_emergency = i >= n_backlog
        mast_id = mast_ids[i % len(mast_ids)]
        info = MASTER_ASSET_MAP["TDMS_Masts"].get(mast_id, {"section": random.choice(SECTIONS)})
        line = info.get("line", random.choice(LINES))
        isolation = "POWER_BLOCK" if (is_emergency or random.random() < 0.4) else "NONE"
        severity_proxy = "CRITICAL" if isolation == "POWER_BLOCK" else "ROUTINE"
        if is_emergency:
            wk = emergency_weeks[i - n_backlog]
            reported = _emergency_report_date(wk)
            avail_week = wk
        else:
            reported = _backlog_report_date()
            avail_week = 1
        due = _due_date(reported, severity_proxy)
        age = random.randint(1, 30)
        row = {
            "ticket_id": f"TDM-{1020+i}",
            "date_reported": reported.isoformat(),
            "power_sector": "SUB_NORTH" if "A-STN_B" in info["section"] else "SUB_SOUTH",
            "elementary_sec": f"ES-{random.randint(10,20)}",
            "mast_id": mast_id,
            "issue": random.choice(issues),
            "isolation_req": isolation,
            "asset_age_years": age,
            "duration_mins": random.choice([45, 60, 90, 120]),
            "due_date": due.isoformat(),
            "available_from_week": avail_week,
            "is_emergency": is_emergency,
        }
        row.update(_block_demand_fields("TDMS", line, severity_proxy, due, is_emergency, age))
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, "tdms_defects.csv"), index=False)
    return df


# ---------------------------------------------------------------------------
# COA — Train Time Table (passenger) + Goods Train Forecast
# These are the two feeds `corridor_availability.py` mines for free windows.
# ---------------------------------------------------------------------------
def gen_train_timetable(days=HORIZON_DAYS):
    rows = []
    tid = 1
    for d in range(days):
        day = MONTH_START + timedelta(days=d)
        for section in SECTIONS:
            for line in LINES:
                n_trains = random.randint(6, 12)
                for _ in range(n_trains):
                    dep = datetime.combine(day, time(0, 0)) + timedelta(minutes=random.randint(0, 24 * 60 - 1))
                    occ = random.choice([8, 10, 12, 15])
                    arr = dep + timedelta(minutes=occ)
                    rows.append({
                        "train_id": f"PSG-{tid}",
                        "date": day.isoformat(),
                        "section": section,
                        "line": line,
                        "dep_time": dep.strftime("%Y-%m-%d %H:%M"),
                        "arr_time": arr.strftime("%Y-%m-%d %H:%M"),
                        "train_type": "PASSENGER",
                    })
                    tid += 1
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, "train_timetable.csv"), index=False)
    return df


def gen_goods_forecast(days=HORIZON_DAYS):
    rows = []
    fid = 1
    for d in range(days):
        day = MONTH_START + timedelta(days=d)
        for section in SECTIONS:
            for line in LINES:
                n_goods = random.randint(2, 5)
                for _ in range(n_goods):
                    start = datetime.combine(day, time(0, 0)) + timedelta(minutes=random.randint(0, 24 * 60 - 1))
                    occ = random.choice([20, 25, 30, 45])
                    end = start + timedelta(minutes=occ)
                    rows.append({
                        "forecast_id": f"GDS-{fid}",
                        "date": day.isoformat(),
                        "section": section,
                        "line": line,
                        "window_start": start.strftime("%Y-%m-%d %H:%M"),
                        "window_end": end.strftime("%Y-%m-%d %H:%M"),
                        "train_type": "GOODS",
                        "forecast_priority": random.choice(["HIGH", "MEDIUM"]),
                    })
                    fid += 1
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, "goods_forecast.csv"), index=False)
    return df


if __name__ == "__main__":
    tms = gen_tms()
    smms = gen_smms()
    tdms = gen_tdms()
    timetable = gen_train_timetable()
    goods = gen_goods_forecast()
    print(f"Generated: tms={len(tms)}, smms={len(smms)}, tdms={len(tdms)} defects "
          f"(incl. {len(EMERGENCIES)} emergency defects appearing in later weeks)")
    print(f"Generated: {len(timetable)} passenger movements, {len(goods)} goods forecast entries "
          f"across {HORIZON_DAYS} days")
    print(f"Saved to: {os.path.abspath(OUT_DIR)}")
