"""
operator_input.py
-------------------
Manual fault-entry path for live demos. An operator (playing TMS/SMMS/TDMS
control-room staff) fills in a form modeled on the official Block Demand
application (Rly/Div/Section/Line/Direction, Block Demand Date, Is Block
Integrated, Enforcement Condition, Use Corridor, Reason Code -- see
Block_Details.xlsx); this module turns that into exactly the same row shape
data_generator.py produces, and appends it to the same CSVs -- so nothing
downstream (spatial_layer -> priority_model -> scheduler -> rolling_planner)
needs to know or care whether a row came from Faker, a live API sync, or a
human typing it in during a demo. Every Block Demand field is required here
too, not just the defect fields -- scheduler.py hard-matches on Section +
Line + Block Demand Date now, so a row missing any of these would silently
never find a corridor.

This is deliberately decoupled from sync.py / the live-API adapter: a
judge-facing demo shouldn't depend on network access or a resync interval --
an operator submits, the row lands on disk immediately, and the next
recompute (triggered by clearing app.py's cache) picks it straight up.

Every manually-logged fault is tagged `is_emergency=True`; Block Demand Date
defaults to the start of whichever planning week the operator selects
(they're reporting AND demanding the block "now", for that week) -- so
submitting a fault into "Week 2" is exactly the live version of the "new
emergency defect -> recalculate -> re-run solver" story from the pitch deck.
"""

import os
import uuid
from datetime import timedelta, datetime, timezone

import pandas as pd

from data_generator import (
    MONTH_START, SLA_DAYS, SECTIONS, LINES,
    RAILWAY, DIVISION, DIRECTION_BY_LINE, ENFORCEMENT_CONDITIONS,
    REASON_CODES, OTHER_DEPTS,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OP_LOG_PATH = os.path.join(DATA_DIR, "_operator_log.csv")

TMS_DEFECT_TYPES = ["Rail Fracture", "Ballast Fouling", "Weld Failure", "Sleeper Crack", "Fishplate Loose"]
SMMS_GEAR_TYPES = ["Point Machine", "Track Circuit", "Signal", "Level Crossing Gate"]
TDMS_ISSUES = ["Cantilever Insulator Flash", "Dropper Wire Snapped", "OHE Tension Low", "Bird Nest Removal"]
SEVERITIES = ["CRITICAL", "URGENT", "ROUTINE"]


def _new_id(prefix: str) -> str:
    return f"{prefix}-OP-{uuid.uuid4().hex[:5].upper()}"


def _week_start_date(week: int):
    return MONTH_START + timedelta(days=(int(week) - 1) * 7)


def _due_date(reported, severity: str):
    return reported + timedelta(days=SLA_DAYS.get(severity, 14))


def _append_row(filename: str, row: dict, key: str):
    path = os.path.join(DATA_DIR, filename)
    new_df = pd.DataFrame([row])
    if os.path.exists(path):
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=key, keep="last")
    else:
        combined = new_df
    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(path, index=False)


def _log_to_audit_trail(dept: str, task_id: str, summary: str, week: int):
    entry = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "dept": dept,
        "task_id": task_id,
        "summary": summary,
        "week": week,
    }
    _append_row_no_upsert(OP_LOG_PATH, entry)


def _append_row_no_upsert(path: str, row: dict):
    new_df = pd.DataFrame([row])
    if os.path.exists(path):
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    os.makedirs(DATA_DIR, exist_ok=True)
    combined.to_csv(path, index=False)


def _block_demand_row(dept: str, line: str, week: int, is_block_integrated: bool,
                       integrated_department: str, enforcement_condition: str,
                       use_corridor: bool, reason_code: str, demand_date=None) -> dict:
    """Shared Block Demand fields for a manually-logged request. `demand_date`
    defaults to the start of the selected week -- an operator logging a fault
    is, in effect, demanding the block for that week right now."""
    if demand_date is None:
        demand_date = _week_start_date(week)
    return {
        "rly": RAILWAY,
        "div": DIVISION,
        "direction": DIRECTION_BY_LINE.get(line, "UP"),
        "block_demand_date": demand_date.isoformat(),
        "is_block_integrated": bool(is_block_integrated),
        "integrated_department": integrated_department if is_block_integrated else "",
        "enforcement_condition": enforcement_condition,
        "use_corridor": bool(use_corridor),
        "reason_code": reason_code,
    }


def log_tms_defect(section, line_type, start_km, end_km, defect_type, severity,
                    asset_age_years, est_duration_mins, week, reported_by="",
                    is_block_integrated=False, integrated_department="",
                    enforcement_condition="Flexible", use_corridor=True, reason_code="EMERGENCY_REPAIR"):
    reported = _week_start_date(week)
    task_id = _new_id("TMS")
    row = {
        "defect_id": task_id,
        "date_reported": reported.isoformat(),
        "section": section,
        "line_type": line_type,
        "start_km": start_km,
        "end_km": end_km if end_km is not None else start_km,
        "defect_type": defect_type,
        "severity": severity,
        "asset_age_years": asset_age_years,
        "est_duration_mins": est_duration_mins,
        "due_date": _due_date(reported, severity).isoformat(),
        "available_from_week": int(week),
        "is_emergency": True,
        "reported_by": reported_by,
    }
    row.update(_block_demand_row("TMS", line_type, week, is_block_integrated, integrated_department,
                                  enforcement_condition, use_corridor, reason_code))
    _append_row("tms_defects.csv", row, key="defect_id")
    _log_to_audit_trail("TMS", task_id, f"{defect_type} @ KM {start_km} ({severity})", week)
    return task_id


def log_smms_defect(station_id, gear_type, asset_id, fault_description, priority,
                     asset_age_years, req_clearance_mins, week, reported_by="",
                     line_type="UP_MAIN", is_block_integrated=False, integrated_department="",
                     enforcement_condition="Flexible", use_corridor=True, reason_code="EMERGENCY_REPAIR"):
    reported = _week_start_date(week)
    task_id = _new_id("SMM")
    row = {
        "fault_id": task_id,
        "date_reported": reported.isoformat(),
        "station_id": station_id,
        "gear_type": gear_type,
        "asset_id": asset_id,
        "fault_description": fault_description,
        "priority": priority,
        "asset_age_years": asset_age_years,
        "req_clearance_mins": req_clearance_mins,
        "due_date": _due_date(reported, priority).isoformat(),
        "available_from_week": int(week),
        "is_emergency": True,
        "reported_by": reported_by,
    }
    row.update(_block_demand_row("SMMS", line_type, week, is_block_integrated, integrated_department,
                                  enforcement_condition, use_corridor, reason_code))
    _append_row("smms_defects.csv", row, key="fault_id")
    _log_to_audit_trail("SMMS", task_id, f"{gear_type} @ {asset_id} ({priority})", week)
    return task_id


def log_tdms_defect(power_sector, elementary_sec, mast_id, issue, isolation_req,
                     asset_age_years, duration_mins, week, reported_by="",
                     line_type="UP_MAIN", is_block_integrated=False, integrated_department="",
                     enforcement_condition="Flexible", use_corridor=True, reason_code="EMERGENCY_REPAIR"):
    reported = _week_start_date(week)
    task_id = _new_id("TDM")
    severity_proxy = "CRITICAL" if isolation_req == "POWER_BLOCK" else "ROUTINE"
    row = {
        "ticket_id": task_id,
        "date_reported": reported.isoformat(),
        "power_sector": power_sector,
        "elementary_sec": elementary_sec,
        "mast_id": mast_id,
        "issue": issue,
        "isolation_req": isolation_req,
        "asset_age_years": asset_age_years,
        "duration_mins": duration_mins,
        "due_date": _due_date(reported, severity_proxy).isoformat(),
        "available_from_week": int(week),
        "is_emergency": True,
        "reported_by": reported_by,
    }
    row.update(_block_demand_row("TDMS", line_type, week, is_block_integrated, integrated_department,
                                  enforcement_condition, use_corridor, reason_code))
    _append_row("tdms_defects.csv", row, key="ticket_id")
    _log_to_audit_trail("TDMS", task_id, f"{issue} @ {mast_id} ({isolation_req})", week)
    return task_id


def recent_manual_entries(limit: int = 20) -> pd.DataFrame:
    if not os.path.exists(OP_LOG_PATH):
        return pd.DataFrame(columns=["logged_at", "dept", "task_id", "summary", "week"])
    df = pd.read_csv(OP_LOG_PATH)
    return df.sort_values("logged_at", ascending=False).head(limit).reset_index(drop=True)
