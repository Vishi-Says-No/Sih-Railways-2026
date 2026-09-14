"""
spatial_layer.py
-----------------
The Data Unification Layer (Linear Referencing System).

Takes the three siloed defect feeds (TMS, SMMS, TDMS) -- each speaking a
different "location language" -- and translates every row into one common
schema, carrying through the fields the rest of the pipeline needs:

    task_id, dept, severity, duration_mins, coa_section, line,
    start_km, end_km, date_reported, due_date, available_from_week,
    is_emergency, power_block_required, rly, div, direction,
    block_demand_date, is_block_integrated, integrated_department,
    enforcement_condition, use_corridor, reason_code

TMS already reports KM directly, so it just needs a section lookup.
SMMS and TDMS report discrete asset IDs, so they're resolved against the
MASTER_ASSET_MAP (in a real system this would be a GIS/spatial DB; here it
is a static Python dict acting as the "master reference map").

Run directly for a quick demo:
    python src/spatial_layer.py
"""

import os
import json
import pandas as pd

from data_generator import MASTER_ASSET_MAP as _SYNTHETIC_ASSET_MAP

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_ASSET_MASTER_PATH = os.path.join(DATA_DIR, "asset_master.json")


def _load_asset_master() -> dict:
    """Prefer the asset master synced from a live source
    (data/asset_master.json, written by sync.py); fall back to the
    synthetic MASTER_ASSET_MAP when running in demo mode."""
    if os.path.exists(_ASSET_MASTER_PATH):
        with open(_ASSET_MASTER_PATH) as f:
            return json.load(f)
    return _SYNTHETIC_ASSET_MAP


MASTER_ASSET_MAP = _load_asset_master()

SECTION_BOUNDARIES = [
    ("STN_A-STN_B", 140.0, 150.0),
    ("STN_B-STN_C", 150.0, 165.0),
    ("STN_C-STN_D", 165.0, 180.0),
]


def _section_for_km(km: float) -> str:
    for name, lo, hi in SECTION_BOUNDARIES:
        if lo <= km < hi:
            return name
    return "UNKNOWN_SECTION"


def unify_tms(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "task_id": df["defect_id"],
        "dept": "TMS",
        "severity": df["severity"],
        "duration_mins": df["est_duration_mins"],
        "asset_age_years": df["asset_age_years"],
        "coa_section": df["start_km"].apply(_section_for_km),
        "line": df["line_type"],
        "start_km": df["start_km"],
        "end_km": df["end_km"],
        "date_reported": df["date_reported"],
        "due_date": df["due_date"],
        "available_from_week": df["available_from_week"],
        "is_emergency": df["is_emergency"],
        "power_block_required": False,
        "defect_type": df["defect_type"],
        "rly": df["rly"],
        "div": df["div"],
        "direction": df["direction"],
        "block_demand_date": df["block_demand_date"],
        "is_block_integrated": df["is_block_integrated"],
        "integrated_department": df["integrated_department"],
        "enforcement_condition": df["enforcement_condition"],
        "use_corridor": df["use_corridor"],
        "reason_code": df["reason_code"],
    })


def unify_smms(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        info = MASTER_ASSET_MAP["SMMS_Assets"].get(r["asset_id"])
        km = info["km"] if info else None
        section = info["section"] if info else "UNKNOWN_SECTION"
        line = info["line"] if info else "UNKNOWN_LINE"
        rows.append({
            "task_id": r["fault_id"],
            "dept": "SMMS",
            "severity": r["priority"],
            "duration_mins": r["req_clearance_mins"],
            "asset_age_years": r["asset_age_years"],
            "coa_section": section,
            "line": line,
            "start_km": km,
            "end_km": km,
            "date_reported": r["date_reported"],
            "due_date": r["due_date"],
            "available_from_week": r["available_from_week"],
            "is_emergency": r["is_emergency"],
            "power_block_required": False,
            "defect_type": r["gear_type"],
            "rly": r["rly"],
            "div": r["div"],
            "direction": r["direction"],
            "block_demand_date": r["block_demand_date"],
            "is_block_integrated": r["is_block_integrated"],
            "integrated_department": r["integrated_department"],
            "enforcement_condition": r["enforcement_condition"],
            "use_corridor": r["use_corridor"],
            "reason_code": r["reason_code"],
        })
    return pd.DataFrame(rows)


def unify_tdms(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        info = MASTER_ASSET_MAP["TDMS_Masts"].get(r["mast_id"])
        km = info["km"] if info else None
        section = info["section"] if info else "UNKNOWN_SECTION"
        line = info["line"] if info else "UNKNOWN_LINE"
        # TDMS has no native "severity" column -> isolation_req doubles as urgency
        severity = "CRITICAL" if r["isolation_req"] == "POWER_BLOCK" else "ROUTINE"
        rows.append({
            "task_id": r["ticket_id"],
            "dept": "TDMS",
            "severity": severity,
            "duration_mins": r["duration_mins"],
            "asset_age_years": r["asset_age_years"],
            "coa_section": section,
            "line": line,
            "start_km": km,
            "end_km": km,
            "date_reported": r["date_reported"],
            "due_date": r["due_date"],
            "available_from_week": r["available_from_week"],
            "is_emergency": r["is_emergency"],
            "power_block_required": r["isolation_req"] == "POWER_BLOCK",
            "defect_type": r["issue"],
            "rly": r["rly"],
            "div": r["div"],
            "direction": r["direction"],
            "block_demand_date": r["block_demand_date"],
            "is_block_integrated": r["is_block_integrated"],
            "integrated_department": r["integrated_department"],
            "enforcement_condition": r["enforcement_condition"],
            "use_corridor": r["use_corridor"],
            "reason_code": r["reason_code"],
        })
    return pd.DataFrame(rows)


def build_unified_tasks() -> pd.DataFrame:
    """Load the 3 raw CSVs and return one unified DataFrame."""
    tms = pd.read_csv(os.path.join(DATA_DIR, "tms_defects.csv"))
    smms = pd.read_csv(os.path.join(DATA_DIR, "smms_defects.csv"))
    tdms = pd.read_csv(os.path.join(DATA_DIR, "tdms_defects.csv"))

    unified = pd.concat(
        [unify_tms(tms), unify_smms(smms), unify_tdms(tdms)],
        ignore_index=True,
    )
    unified.to_csv(os.path.join(DATA_DIR, "unified_tasks.csv"), index=False)
    return unified


if __name__ == "__main__":
    unified = build_unified_tasks()
    print(f"Unified {len(unified)} tasks across TMS/SMMS/TDMS into a single schema "
          f"({unified['is_emergency'].sum()} are mid-horizon emergency defects).\n")
    print(unified[["task_id", "dept", "severity", "coa_section", "start_km", "available_from_week"]].to_string(index=False))

    print("\nSections with cross-department overlap (Shadow Block candidates):")
    overlap = unified.groupby("coa_section")["dept"].nunique()
    print(overlap[overlap > 1])
