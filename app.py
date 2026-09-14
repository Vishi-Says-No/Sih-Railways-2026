"""
app.py
------
Control-panel dashboard for the Automatic Block Planning & Multi-Department
Scheduling Engine.

Six tabs:
  0. Operator Input   -- log a live fault (full Block Demand form) for a demo
  1. Ingestion        -- unified TMS/SMMS/TDMS inbox, overdue + Block Demand flagged
  2. Priority Queue    -- ML-ranked backlog with an explainable "why" per task
  3. Weekly Micro Plan  -- pick any of the 4 weeks, see that week's real
                          coordination/downtime KPIs
  4. Shadow Block Gantt -- day-level operator view: one row per section+line,
                          gold = multi-department, purple = standalone
  5. Monthly Macro Plan -- roll-up across the whole horizon: backlog burn-down,
                          blocks opened vs. the uncoordinated baseline

Run:
    streamlit run app.py
"""

import os
import sys

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.corridor_availability import compute_corridors
from src.priority_model import score_tasks
from src.rolling_planner import run_rolling
from src.sync import sync_all, needs_resync, data_bootstrapped, is_live_mode
from src.data_sources.config import sync_interval_minutes
from src.data_generator import SECTIONS, LINES
import src.operator_input as opin

st.set_page_config(page_title="Block Planning — Control Panel", page_icon="🚆", layout="wide")

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
INK = "#0A0E17"      # page background
PANEL = "#121826"    # panel surface
LINE = "#232B3D"     # hairline borders
FOG = "#7C8797"      # secondary text
PAPER = "#E9EDF3"    # primary text

RED, AMBER, GREEN = "#E14B3C", "#E4A23A", "#3FAE71"
SEV_COLOR = {"CRITICAL": RED, "URGENT": AMBER, "ROUTINE": GREEN}

DEPT_COLOR = {"TMS": "#4A90D9", "SMMS": "#4FBF7A", "TDMS": "#E08A47"}
DEPT_LABEL = {"TMS": "Track (TMS)", "SMMS": "Signals (SMMS)", "TDMS": "Traction (TDMS)"}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {{ font-family: 'IBM Plex Sans', sans-serif; }}
.mono {{ font-family: 'IBM Plex Mono', monospace; }}

#MainMenu, footer, header {{ visibility: hidden; }}
.block-container {{ padding-top: 1.5rem; max-width: 1200px; }}

body, .stApp {{
    background-color: {INK};
    background-image:
        repeating-linear-gradient(0deg, {LINE}22 0px, {LINE}22 1px, transparent 1px, transparent 64px),
        repeating-linear-gradient(90deg, {LINE}22 0px, {LINE}22 1px, transparent 1px, transparent 64px);
}}

.ctl-header {{
    display: flex; justify-content: space-between; align-items: flex-end;
    border-bottom: 1px solid {LINE}; padding-bottom: 14px; margin-bottom: 6px;
}}
.ctl-title {{ color: {PAPER}; font-size: 26px; font-weight: 600; margin: 0; }}
.ctl-subtitle {{ color: {FOG}; font-size: 13.5px; margin-top: 4px; }}
.ctl-status {{ display: flex; gap: 18px; }}
.ctl-status-item {{ display: flex; align-items: center; gap: 7px; color: {FOG}; font-size: 12.5px; }}
.ctl-dot {{ width: 8px; height: 8px; border-radius: 50%; box-shadow: 0 0 6px 1px currentColor; }}

button[data-baseweb="tab"] {{
    font-family: 'IBM Plex Sans', sans-serif !important;
    color: {FOG} !important; font-size: 13.5px !important; font-weight: 500 !important;
    padding: 10px 4px !important;
}}
button[data-baseweb="tab"][aria-selected="true"] {{ color: {PAPER} !important; }}
div[data-baseweb="tab-highlight"] {{ background-color: {DEPT_COLOR['TMS']} !important; height: 2px !important; }}
div[data-baseweb="tab-border"] {{ background-color: {LINE} !important; }}

.panel {{
    background: {PANEL}; border: 1px solid {LINE}; border-radius: 3px;
    padding: 20px 22px; margin-top: 14px; position: relative;
}}
.panel::before, .panel::after {{
    content: ""; position: absolute; width: 9px; height: 9px; top: -1px;
    border-top: 1.5px solid {DEPT_COLOR['TMS']};
}}
.panel::before {{ left: -1px; border-left: 1.5px solid {DEPT_COLOR['TMS']}; }}
.panel::after {{ right: -1px; border-right: 1.5px solid {DEPT_COLOR['TMS']}; }}
.panel-title {{ color: {PAPER}; font-size: 15px; font-weight: 600; margin: 0 0 2px 0; }}
.panel-sub {{ color: {FOG}; font-size: 12.5px; margin-bottom: 16px; }}

.readout-row {{ display: flex; gap: 14px; margin-top: 14px; flex-wrap: wrap; }}
.readout {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 3px; padding: 14px 18px; flex: 1; min-width: 150px; }}
.readout-val {{ font-family: 'IBM Plex Mono', monospace; color: {PAPER}; font-size: 24px; font-weight: 500; }}
.readout-val.good {{ color: {GREEN}; }}
.readout-label {{ color: {FOG}; font-size: 12px; margin-top: 2px; }}

table.ctl-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
table.ctl-table th {{
    text-align: left; color: {FOG}; font-weight: 500; font-size: 12px;
    padding: 8px 10px; border-bottom: 1px solid {LINE};
}}
table.ctl-table td {{ padding: 9px 10px; border-bottom: 1px solid {LINE}22; color: {PAPER}; }}
table.ctl-table tr:last-child td {{ border-bottom: none; }}
td.sev-cell, td.dept-cell {{ position: relative; padding-left: 16px !important; }}
td.sev-cell::before, td.dept-cell::before {{
    content: ""; position: absolute; left: 2px; top: 4px; bottom: 4px; width: 3px; border-radius: 2px;
}}
.badge-overdue {{ color: {RED}; font-weight: 600; }}
.badge-ok {{ color: {FOG}; }}
.badge-emergency {{ display:inline-block; background:{AMBER}22; color:{AMBER}; border:1px solid {AMBER}55;
    border-radius: 3px; font-size: 10.5px; padding: 1px 6px; margin-left: 6px; }}
.reason-text {{ color: {FOG}; font-size: 11.5px; }}

[data-testid="stMultiSelect"] label {{ color: {FOG} !important; font-size: 12.5px !important; }}
[data-testid="stSelectbox"] label {{ color: {FOG} !important; font-size: 12.5px !important; }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


@st.cache_data(ttl=60)  # re-checked every minute; sync_all() itself is further gated below
def load_pipeline():
    if not data_bootstrapped():
        sync_all(incremental=False)             # first run (or after a reset): build the dataset fresh
    elif is_live_mode() and needs_resync(sync_interval_minutes()):
        sync_all(incremental=True)               # live API: poll for new/updated rows on a timer
    # NOTE: in synthetic/demo mode we deliberately do NOT re-run sync_all() here once bootstrapped --
    # gen_tms()/gen_smms()/gen_tdms() overwrite their CSVs as a side effect, which would silently wipe
    # anything an operator just logged via the Operator Input tab. Only the explicit "Reset demo data"
    # button (which deletes the CSVs first) triggers a fresh bootstrap in demo mode.
    compute_corridors()                            # block availability derived from timetable + goods forecast
    scored = score_tasks()                          # full-horizon ML priority view (Ingestion / Priority Queue)
    rolling_schedule, monthly_summary = run_rolling(weeks=4)  # Weekly Micro / Monthly Macro plans
    return scored, rolling_schedule, monthly_summary


scored_tasks, rolling_schedule, monthly_summary = load_pipeline()

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
dept_counts = scored_tasks["dept"].value_counts().to_dict()
overdue_count = int(scored_tasks["is_overdue"].sum())
status_html = "".join(
    f'<div class="ctl-status-item"><span class="ctl-dot" style="background:{DEPT_COLOR[d]};color:{DEPT_COLOR[d]}"></span>'
    f'{DEPT_LABEL[d]} · {dept_counts.get(d, 0)}</div>'
    for d in ["TMS", "SMMS", "TDMS"]
)
status_html += (
    f'<div class="ctl-status-item"><span class="ctl-dot" style="background:{RED};color:{RED}"></span>'
    f'Overdue · {overdue_count}</div>'
)
st.markdown(f"""
<div class="ctl-header">
    <div>
        <p class="ctl-title">Block Planning Control Panel</p>
        <p class="ctl-subtitle">TMS + SMMS + TDMS defects, matched against COA's train timetable &amp; goods forecast — ML priority scoring + OR-Tools CP-SAT</p>
    </div>
    <div class="ctl-status">{status_html}</div>
</div>
""", unsafe_allow_html=True)

tab0, tab1, tab2, tab3, tab_gantt, tab4 = st.tabs(
    ["🛠️ Operator Input", "Ingestion", "Priority Queue", "Weekly Micro Plan",
     "📊 Shadow Block Gantt", "Monthly Macro Plan"]
)

# ---------------------------------------------------------------------------
# TAB 0 — Operator Input (live demo entry point)
# ---------------------------------------------------------------------------
with tab0:
    st.markdown("""
    <div class="panel">
        <p class="panel-title">Live fault entry</p>
        <p class="panel-sub">Log a defect the way a TMS/SMMS/TDMS field engineer would — pick a department below, fill in the form, and submit. It's scored and scheduled immediately; check the Priority Queue and Weekly Micro Plan tabs right after.</p>
    </div>
    """, unsafe_allow_html=True)

    dept_choice = st.radio("Department reporting this fault", ["TMS — Track", "SMMS — Signal", "TDMS — Traction"],
                            horizontal=True, label_visibility="visible")

    from src.data_generator import SECTIONS, LINES, RAILWAY, DIVISION, ENFORCEMENT_CONDITIONS, REASON_CODES, OTHER_DEPTS
    from src.spatial_layer import MASTER_ASSET_MAP

    st.caption(f"Rly: {RAILWAY} · Div: {DIVISION} — fixed for this control area")

    def _block_demand_widgets(prefix: str, dept: str, col_a, col_b, col_c):
        integ_choice = col_a.selectbox("Is Block Integrated", ["No", "Yes"], key=f"{prefix}_integ")
        integ_dept = col_b.selectbox("If integrated, with department", OTHER_DEPTS[dept], key=f"{prefix}_integ_dept")
        enforcement = col_c.selectbox("Enforcement Condition", ENFORCEMENT_CONDITIONS, key=f"{prefix}_enf")
        return integ_choice == "Yes", integ_dept, enforcement

    if dept_choice.startswith("TMS"):
        with st.form("tms_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            section = c1.selectbox("Section", SECTIONS)
            line_type = c2.selectbox("Line", LINES)
            severity = c3.selectbox("Severity", opin.SEVERITIES)
            c4, c5, c6 = st.columns(3)
            start_km = c4.number_input("Start KM", min_value=100.0, max_value=200.0, value=150.0, step=0.1)
            end_km = c5.number_input("End KM", min_value=100.0, max_value=200.0, value=150.0, step=0.1)
            asset_age_years = c6.number_input("Asset age (yrs)", min_value=0, max_value=60, value=10)
            c7, c8 = st.columns(2)
            defect_type = c7.selectbox("Defect type", opin.TMS_DEFECT_TYPES)
            est_duration_mins = c8.number_input("Estimated repair duration (mins)", min_value=15, max_value=480, value=90, step=15)
            c9, c10 = st.columns(2)
            week = c9.selectbox("Report into week", [1, 2, 3, 4], format_func=lambda w: f"Week {w}")
            reported_by = c10.text_input("Reported by (optional)", placeholder="e.g. Section Engineer, STN_B")
            st.markdown('<p class="panel-sub" style="margin-top:6px">Block Demand</p>', unsafe_allow_html=True)
            c11, c12, c13 = st.columns(3)
            is_integrated, integ_dept, enforcement = _block_demand_widgets("tms", "TMS", c11, c12, c13)
            c14, c15 = st.columns(2)
            use_corridor_choice = c14.selectbox("Use Corridor", ["Yes", "No"], key="tms_use_corridor")
            reason_code_choice = c15.selectbox("Reason Code", REASON_CODES, key="tms_reason")
            if st.form_submit_button("Log TMS defect", type="primary"):
                task_id = opin.log_tms_defect(section, line_type, start_km, end_km, defect_type, severity,
                                               asset_age_years, est_duration_mins, week, reported_by,
                                               is_block_integrated=is_integrated, integrated_department=integ_dept,
                                               enforcement_condition=enforcement,
                                               use_corridor=(use_corridor_choice == "Yes"),
                                               reason_code=reason_code_choice)
                load_pipeline.clear()
                st.session_state["last_logged"] = f"{task_id} logged into Week {week} — recomputing plan…"
                st.rerun()

    elif dept_choice.startswith("SMMS"):
        smms_assets = list(MASTER_ASSET_MAP.get("SMMS_Assets", {}).keys())
        with st.form("smms_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            station_id = c1.selectbox("Station", ["STN_A", "STN_B", "STN_C", "STN_D"])
            gear_type = c2.selectbox("Gear type", opin.SMMS_GEAR_TYPES)
            priority = c3.selectbox("Priority", opin.SEVERITIES)
            c4, c5 = st.columns(2)
            asset_id = c4.selectbox("Asset ID", smms_assets + ["(unlisted asset — type below)"])
            if asset_id == "(unlisted asset — type below)":
                asset_id = c4.text_input("New asset ID", value="")
            asset_age_years = c5.number_input("Asset age (yrs)", min_value=0, max_value=40, value=8)
            fault_description = st.text_area("Fault description", placeholder="e.g. Point machine failing to lock on reverse")
            c6, c7 = st.columns(2)
            req_clearance_mins = c6.number_input("Required clearance (mins)", min_value=15, max_value=240, value=45, step=15)
            week = c7.selectbox("Report into week", [1, 2, 3, 4], format_func=lambda w: f"Week {w}", key="smms_week")
            reported_by = st.text_input("Reported by (optional)", placeholder="e.g. Signal Maintainer, STN_B", key="smms_by")
            st.markdown('<p class="panel-sub" style="margin-top:6px">Block Demand</p>', unsafe_allow_html=True)
            c11, c12, c13 = st.columns(3)
            is_integrated, integ_dept, enforcement = _block_demand_widgets("smms", "SMMS", c11, c12, c13)
            c14, c15 = st.columns(2)
            use_corridor_choice = c14.selectbox("Use Corridor", ["Yes", "No"], key="smms_use_corridor")
            reason_code_choice = c15.selectbox("Reason Code", REASON_CODES, key="smms_reason")
            if st.form_submit_button("Log SMMS fault", type="primary"):
                resolved_line = MASTER_ASSET_MAP.get("SMMS_Assets", {}).get(asset_id, {}).get("line", "UP_MAIN")
                task_id = opin.log_smms_defect(station_id, gear_type, asset_id, fault_description, priority,
                                                asset_age_years, req_clearance_mins, week, reported_by,
                                                line_type=resolved_line,
                                                is_block_integrated=is_integrated, integrated_department=integ_dept,
                                                enforcement_condition=enforcement,
                                                use_corridor=(use_corridor_choice == "Yes"),
                                                reason_code=reason_code_choice)
                load_pipeline.clear()
                st.session_state["last_logged"] = f"{task_id} logged into Week {week} — recomputing plan…"
                st.rerun()

    else:
        tdms_masts = list(MASTER_ASSET_MAP.get("TDMS_Masts", {}).keys())
        with st.form("tdms_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            power_sector = c1.selectbox("Power sector", ["SUB_NORTH", "SUB_SOUTH"])
            elementary_sec = c2.text_input("Elementary section", value="ES-14")
            isolation_req = c3.selectbox("Isolation required", ["POWER_BLOCK", "NONE"])
            c4, c5 = st.columns(2)
            mast_id = c4.selectbox("Mast ID", tdms_masts + ["(unlisted mast — type below)"])
            if mast_id == "(unlisted mast — type below)":
                mast_id = c4.text_input("New mast ID", value="")
            asset_age_years = c5.number_input("Asset age (yrs)", min_value=0, max_value=40, value=12)
            issue = st.selectbox("Issue", opin.TDMS_ISSUES)
            c6, c7 = st.columns(2)
            duration_mins = c6.number_input("Estimated repair duration (mins)", min_value=15, max_value=240, value=60, step=15)
            week = c7.selectbox("Report into week", [1, 2, 3, 4], format_func=lambda w: f"Week {w}", key="tdms_week")
            reported_by = st.text_input("Reported by (optional)", placeholder="e.g. Traction Sub-Inspector", key="tdms_by")
            st.markdown('<p class="panel-sub" style="margin-top:6px">Block Demand</p>', unsafe_allow_html=True)
            c11, c12, c13 = st.columns(3)
            is_integrated, integ_dept, enforcement = _block_demand_widgets("tdms", "TDMS", c11, c12, c13)
            c14, c15 = st.columns(2)
            use_corridor_choice = c14.selectbox("Use Corridor", ["Yes", "No"], key="tdms_use_corridor")
            reason_code_choice = c15.selectbox("Reason Code", REASON_CODES, key="tdms_reason")
            if st.form_submit_button("Log TDMS ticket", type="primary"):
                resolved_line = MASTER_ASSET_MAP.get("TDMS_Masts", {}).get(mast_id, {}).get("line", "UP_MAIN")
                task_id = opin.log_tdms_defect(power_sector, elementary_sec, mast_id, issue, isolation_req,
                                                asset_age_years, duration_mins, week, reported_by,
                                                line_type=resolved_line,
                                                is_block_integrated=is_integrated, integrated_department=integ_dept,
                                                enforcement_condition=enforcement,
                                                use_corridor=(use_corridor_choice == "Yes"),
                                                reason_code=reason_code_choice)
                load_pipeline.clear()
                st.session_state["last_logged"] = f"{task_id} logged into Week {week} — recomputing plan…"
                st.rerun()

    if "last_logged" in st.session_state:
        st.success(st.session_state["last_logged"])
        del st.session_state["last_logged"]

    recent = opin.recent_manual_entries()
    if len(recent):
        rows = "".join(
            f'<tr><td>{r.dept}</td><td>{r.task_id}</td><td>{r.summary}</td>'
            f'<td>Week {int(r.week)}</td><td class="mono">{str(r.logged_at)[:19]}</td></tr>'
            for r in recent.itertuples()
        )
        st.markdown(f"""
        <div class="panel">
            <p class="panel-title">Session log — manually entered faults</p>
            <p class="panel-sub">Most recent first. Each of these is tagged as an emergency defect and flows through ML scoring + CP-SAT scheduling exactly like the backlog.</p>
            <table class="ctl-table">
                <tr><th>Dept</th><th>Task</th><th>Summary</th><th>Week</th><th>Logged at (UTC)</th></tr>
                {rows}
            </table>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)
    with st.expander("Reset demo data (start a fresh run)"):
        st.caption("Wipes all data — including anything logged above — and regenerates a clean synthetic backlog + timetable. Use this right before a demo.")
        if st.button("Reset to fresh synthetic baseline", type="secondary"):
            import glob
            for f in glob.glob(os.path.join(DATA_DIR, "*.csv")) + glob.glob(os.path.join(DATA_DIR, "*.json")):
                os.remove(f)
            load_pipeline.clear()
            st.rerun()

# ---------------------------------------------------------------------------
# TAB 1 — Unified inbox
# ---------------------------------------------------------------------------
with tab1:
    depts = sorted(scored_tasks["dept"].unique())
    dept_filter = st.multiselect("Department", options=depts, default=depts, label_visibility="visible")
    view = scored_tasks[scored_tasks["dept"].isin(dept_filter)]

    def _overdue_badge(r):
        if r.is_overdue:
            return f'<span class="badge-overdue">⚠ {int(r.days_overdue)}d overdue</span>'
        return f'<span class="badge-ok">due in {int(r.days_until_due)}d</span>'

    def _block_demand_badges(r):
        bits = []
        if r.enforcement_condition == "Fixed Time Slot":
            bits.append('<span class="badge-emergency" style="color:#E4A23A;border-color:#E4A23A55;background:#E4A23A22">🔒 Fixed Slot</span>')
        if not r.use_corridor:
            bits.append('<span class="badge-emergency" style="color:#7C8797;border-color:#7C879755;background:#7C879722">⛔ Standalone</span>')
        if r.is_block_integrated:
            target = r.integrated_department if isinstance(r.integrated_department, str) and r.integrated_department else "?"
            bits.append(f'<span class="badge-emergency">🔗 wants {target}</span>')
        return " ".join(bits) if bits else '<span class="badge-ok">—</span>'

    rows = "".join(
        f'<tr><td class="dept-cell"><span style="position:absolute;left:2px;top:4px;bottom:4px;width:3px;'
        f'border-radius:2px;background:{DEPT_COLOR[r.dept]}"></span>{r.dept}'
        f'{"<span class=\'badge-emergency\'>NEW</span>" if r.is_emergency else ""}</td>'
        f'<td>{r.task_id}</td><td>{r.severity.title()}</td><td>{r.coa_section}</td>'
        f'<td class="mono">{r.line}</td>'
        f'<td class="mono">{"" if pd.isna(r.start_km) else f"{r.start_km:.3f}"}</td>'
        f'<td class="mono">{r.duration_mins} min</td>'
        f'<td class="mono">{r.block_demand_date}</td>'
        f'<td>{_overdue_badge(r)}</td>'
        f'<td>{_block_demand_badges(r)}</td></tr>'
        for r in view.itertuples()
    )
    st.markdown(f"""
    <div class="panel">
        <p class="panel-title">Unified task inbox</p>
        <p class="panel-sub">TMS (track KM), SMMS (asset IDs), and TDMS (mast numbers) — resolved to one schema by the LRS unification layer, against Rly: NR · Div: DLI. Due dates are set from each defect's severity SLA at the moment it was reported; Block Demand Date is what the scheduler actually hard-matches to a corridor.</p>
        <table class="ctl-table">
            <tr><th>Dept</th><th>Task</th><th>Severity</th><th>Section</th><th>Line</th><th>KM</th><th>Duration</th><th>Demand Date</th><th>SLA status</th><th>Block Demand</th></tr>
            {rows}
        </table>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TAB 2 — Priority queue
# ---------------------------------------------------------------------------
with tab2:
    method = scored_tasks["scoring_method"].iloc[0]
    rows = "".join(
        f'<tr><td class="sev-cell"><span style="position:absolute;left:2px;top:4px;bottom:4px;width:3px;'
        f'border-radius:2px;background:{SEV_COLOR.get(r.severity, FOG)}"></span>{r.severity.title()}</td>'
        f'<td>{r.task_id}{" 🔒" if r.enforcement_condition == "Fixed Time Slot" else ""}{" ⛔" if not r.use_corridor else ""}</td>'
        f'<td>{r.dept}</td><td>{r.traffic_density.title()}</td>'
        f'<td class="mono">{r.asset_age_years} yr</td>'
        f'<td class="mono" style="color:{SEV_COLOR.get(r.severity, PAPER)}">{r.criticality_score:.1f}</td>'
        f'<td class="reason-text">{r.priority_reason}</td></tr>'
        for r in scored_tasks.itertuples()
    )
    st.markdown(f"""
    <div class="panel">
        <p class="panel-title">Priority queue</p>
        <p class="panel-sub">Ranked by criticality score (severity + urgency/overdue + operational impact + asset age) · scoring method: {method}. "Why" column is the explainability layer — ML ranks urgency, it never decides safety. 🔒 = Fixed Time Slot demand, ⛔ = standalone (Use Corridor: No).</p>
        <table class="ctl-table">
            <tr><th>Severity</th><th>Task</th><th>Dept</th><th>Traffic</th><th>Asset age</th><th>Score</th><th>Why this rank</th></tr>
            {rows}
        </table>
    </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Dedicated day-level Gantt renderer (used by the Shadow Block Gantt tab)
# ---------------------------------------------------------------------------
ALL_SECTION_LINE_ROWS = [f"{s} · {l}" for s in SECTIONS for l in LINES]


def render_shadow_block_day_gantt(day_sched: pd.DataFrame, day):
    """One row per section+line (6 fixed rows, same every day — easy for an
    operator to scan across days), one bar per Shadow Block actually formed
    that day. Two layers per block: a faint full corridor window (the whole
    derived-available block, including the safety buffer) and a solid
    utilized portion sized to the tasks actually bundled into it. Gold =
    multiple departments sharing the block; department color = single-dept."""
    day_sched = day_sched.copy()
    day_sched["window_start"] = pd.to_datetime(day_sched["window_start"])
    day_sched["window_end"] = pd.to_datetime(day_sched["window_end"])

    blocks = []
    for corridor_id, grp in day_sched.groupby("corridor_id"):
        depts = sorted(grp["dept"].unique())
        start = grp["window_start"].iloc[0]
        full_end = grp["window_end"].iloc[0]
        used_mins = int(grp["duration_mins"].sum())
        used_end = start + pd.Timedelta(minutes=used_mins)
        is_standalone = str(corridor_id).startswith("STANDALONE")
        blocks.append({
            "corridor_id": corridor_id,
            "row": f'{grp["block_section"].iloc[0]} · {grp["line"].iloc[0]}',
            "start": start, "full_end": full_end, "used_end": used_end,
            "depts": depts, "is_multi": len(depts) > 1, "is_standalone": is_standalone,
            "task_count": len(grp), "used_mins": used_mins,
        })
    blocks_df = pd.DataFrame(blocks)

    day_ts = pd.Timestamp(day)
    fig = go.Figure()

    if len(blocks_df):
        # FIX: Convert datetimes to decimal hours (e.g., 1.5 = 01:30) to prevent JSON timedelta crashes
        start_hours = blocks_df["start"].dt.hour + (blocks_df["start"].dt.minute / 60)
        full_dur = (blocks_df["full_end"] - blocks_df["start"]).dt.total_seconds() / 3600
        used_dur = (blocks_df["used_end"] - blocks_df["start"]).dt.total_seconds() / 3600

        fig.add_trace(go.Bar(
            base=start_hours, x=full_dur, y=blocks_df["row"],
            orientation="h", marker_color=LINE, marker_line_width=0, opacity=0.6,
            name="Available window (incl. safety buffer)", hoverinfo="skip",
        ))
        colors = [
            "#8B6FD6" if standalone else (GREEN if m else DEPT_COLOR[d[0]])
            for standalone, m, d in zip(blocks_df["is_standalone"], blocks_df["is_multi"], blocks_df["depts"])
        ]
        labels = [
            (" + ".join(d) + " (standalone)") if standalone else " + ".join(d)
            for standalone, d in zip(blocks_df["is_standalone"], blocks_df["depts"])
        ]
        fig.add_trace(go.Bar(
            base=start_hours, x=used_dur, y=blocks_df["row"],
            orientation="h", marker_color=colors, text=labels, textposition="inside", insidetextanchor="middle",
            textfont=dict(color="#0A0E17", size=11.5, family="IBM Plex Sans"),
            customdata=blocks_df[["corridor_id", "task_count", "used_mins"]],
            hovertemplate="<b>%{customdata[0]}</b><br>%{text}<br>%{customdata[1]} task(s) · %{customdata[2]} min used<extra></extra>",
            name="Shadow Block (utilized)",
        ))

    fig.update_layout(
        barmode="overlay",
        paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        font=dict(family="IBM Plex Sans", color=PAPER, size=12),
        legend=dict(orientation="h", y=1.18, x=0, font=dict(size=11.5)),
        margin=dict(l=10, r=10, t=40, b=10),
        # FIX: Force the axis to display a standard 24-hour clock grid
        xaxis=dict(
            type="linear", range=[0, 24], gridcolor=LINE, zerolinecolor=LINE,
            tickmode="array",
            tickvals=[0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24],
            ticktext=["00:00", "02:00", "04:00", "06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00", "24:00"],
            tickfont=dict(family="IBM Plex Mono", size=11)
        ),
        yaxis=dict(gridcolor=LINE, zerolinecolor=LINE,
                    categoryorder="array", categoryarray=list(reversed(ALL_SECTION_LINE_ROWS))),
        height=320,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    return blocks_df


# ---------------------------------------------------------------------------
# TAB 3 — Weekly Micro Plan
# ---------------------------------------------------------------------------
with tab3:
    week_choice = st.selectbox("Week", options=[1, 2, 3, 4], format_func=lambda w: f"Week {w}", index=0)
    wk_summary = monthly_summary[monthly_summary["week"] == week_choice].iloc[0]
    wk_schedule = rolling_schedule[rolling_schedule["week"] == week_choice]
    scheduled = wk_schedule[wk_schedule["status"].str.startswith("SCHEDULED")].copy()
    unscheduled = wk_schedule[~wk_schedule["status"].str.startswith("SCHEDULED")]

    st.markdown(f"""
    <div class="readout-row">
        <div class="readout"><div class="readout-val">{int(wk_summary.tasks_scheduled)}</div><div class="readout-label">Tasks scheduled this week</div></div>
        <div class="readout"><div class="readout-val">{int(wk_summary.corridors_used)}</div><div class="readout-label">Shadow blocks opened (vs {int(wk_summary.baseline_corridors)} uncoordinated)</div></div>
        <div class="readout"><div class="readout-val good">{wk_summary.downtime_reduction_pct:.1f}%</div><div class="readout-label">Downtime reduction this week</div></div>
        <div class="readout"><div class="readout-val">{int(wk_summary.multi_dept_corridors)}</div><div class="readout-label">Multi-department blocks</div></div>
        <div class="readout"><div class="readout-val">{int(wk_summary.standalone_blocks)}</div><div class="readout-label">Standalone possessions (Use Corridor: No)</div></div>
        <div class="readout"><div class="readout-val">{int(wk_summary.integration_requests_fulfilled)}/{int(wk_summary.integration_requests_total)}</div><div class="readout-label">Requested integrations fulfilled</div></div>
        <div class="readout"><div class="readout-val">{int(wk_summary.new_emergencies_this_week)}</div><div class="readout-label">New emergency defects this week</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="panel">
        <p class="panel-title">Week {week_choice} — {int(scheduled['corridor_id'].nunique()) if len(scheduled) else 0} Shadow Blocks formed</p>
        <p class="panel-sub">For the day-by-day breakdown of exactly which blocks these are and which departments share each one, see the <b>📊 Shadow Block Gantt</b> tab — it's built for this week automatically.</p>
    </div>
    """, unsafe_allow_html=True)

    if len(unscheduled):
        rows = "".join(
            f'<tr><td>{r.task_id}</td><td>{r.dept}</td><td class="mono">{r.criticality_score:.1f}</td><td>{r.block_section}</td>'
            f'<td class="reason-text">{r.status.replace("UNSCHEDULED ", "")}</td></tr>'
            for r in unscheduled.itertuples()
        )
        st.markdown(f"""
        <div class="panel">
            <p class="panel-title">Carried to next week's micro-schedule</p>
            <p class="panel-sub">Unscheduled this week — either no capacity left in a matching corridor, or the corridor for the requested Block Demand Date hasn't come up yet. Automatically reconsidered as later weeks re-optimize.</p>
            <table class="ctl-table">
                <tr><th>Task</th><th>Dept</th><th>Score</th><th>Section</th><th>Reason</th></tr>
                {rows}
            </table>
        </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TAB — Shadow Block Gantt (operator-facing view, day-level)
# ---------------------------------------------------------------------------
with tab_gantt:
    st.markdown("""
    <div class="panel">
        <p class="panel-title">Shadow Block Gantt — Operator View</p>
        <p class="panel-sub">This is the chart a section controller would actually work from: one row per track section + line, one day at a time. Faint bar = the full block window derived from the timetable + goods forecast (includes the safety buffer). Solid bar = the block actually used. Gold = multiple departments sharing one possession. Purple = a standalone possession (Use Corridor: No), granted outside the shared corridor pool.</p>
    </div>
    """, unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    gantt_week = c1.selectbox("Week", options=[1, 2, 3, 4], format_func=lambda w: f"Week {w}", key="gantt_week")

    wk_sched_all = rolling_schedule[
        (rolling_schedule["week"] == gantt_week) & (rolling_schedule["status"].str.startswith("SCHEDULED"))
    ].copy()
    wk_sched_all["date"] = pd.to_datetime(wk_sched_all["window_start"]).dt.date
    available_dates = sorted(wk_sched_all["date"].unique())

    if not available_dates:
        st.markdown('<div class="panel"><p class="panel-title">No Shadow Blocks formed in this week</p>'
                     '<p class="panel-sub">Try another week, or log a fault via Operator Input first.</p></div>',
                     unsafe_allow_html=True)
    else:
        gantt_day = c2.selectbox("Day", options=available_dates,
                                  format_func=lambda d: d.strftime("%A, %d %b"), key="gantt_day")
        day_sched = wk_sched_all[wk_sched_all["date"] == gantt_day]

        n_blocks = day_sched["corridor_id"].nunique()
        n_tasks = len(day_sched)
        n_multi = day_sched.groupby("corridor_id")["dept"].nunique().gt(1).sum()
        depts_today = day_sched["dept"].nunique()

        st.markdown(f"""
        <div class="readout-row">
            <div class="readout"><div class="readout-val">{n_blocks}</div><div class="readout-label">Shadow Blocks today</div></div>
            <div class="readout"><div class="readout-val">{n_tasks}</div><div class="readout-label">Tasks scheduled today</div></div>
            <div class="readout"><div class="readout-val good">{int(n_multi)}</div><div class="readout-label">Multi-department blocks</div></div>
            <div class="readout"><div class="readout-val">{depts_today}</div><div class="readout-label">Departments active today</div></div>
        </div>
        """, unsafe_allow_html=True)

        blocks_df = render_shadow_block_day_gantt(day_sched, gantt_day)

        if len(blocks_df):
            detail = day_sched.merge(blocks_df[["corridor_id"]], on="corridor_id")

            def _integration_cell(r):
                if not r.is_block_integrated:
                    return '<span class="badge-ok">—</span>'
                icon = "✅" if r.integration_fulfilled else "❌"
                return f'{icon} wants {r.integrated_department}'

            rows = "".join(
                f'<tr><td>{r.corridor_id}</td><td>{r.block_section} · {r.line}</td>'
                f'<td class="mono">{pd.to_datetime(r.window_start).strftime("%H:%M")}–{pd.to_datetime(r.window_end).strftime("%H:%M")}</td>'
                f'<td>{r.dept}</td><td>{r.task_id}</td><td class="mono">{r.criticality_score:.1f}</td>'
                f'<td class="mono">{r.duration_mins} min</td>'
                f'<td>{r.enforcement_condition}</td><td>{_integration_cell(r)}</td></tr>'
                for r in detail.sort_values(["corridor_id", "criticality_score"], ascending=[True, False]).itertuples()
            )
            st.markdown(f"""
            <div class="panel">
                <p class="panel-title">{gantt_day.strftime("%A, %d %b")} — Shadow Block detail</p>
                <p class="panel-sub">Every task bundled into each block, in the order the possession would actually be worked. "Integration" compares what the requester asked for (Is Block Integrated + department) against what the solver actually achieved.</p>
                <table class="ctl-table">
                    <tr><th>Block</th><th>Section · Line</th><th>Window</th><th>Dept</th><th>Task</th><th>Score</th><th>Duration</th><th>Enforcement</th><th>Integration</th></tr>
                    {rows}
                </table>
            </div>
            """, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# TAB 4 — Monthly Macro Plan
# ---------------------------------------------------------------------------
with tab4:
    total_scheduled = int(monthly_summary["tasks_scheduled"].sum())
    total_baseline = int(monthly_summary["baseline_corridors"].sum())
    total_actual = int(monthly_summary["corridors_used"].sum())
    overall_reduction = round((total_baseline - total_actual) / total_baseline * 100, 1) if total_baseline else 0.0
    backlog_end = int(monthly_summary["backlog_remaining"].iloc[-1])

    st.markdown(f"""
    <div class="readout-row">
        <div class="readout"><div class="readout-val">{total_scheduled}</div><div class="readout-label">Tasks scheduled across the month</div></div>
        <div class="readout"><div class="readout-val">{total_actual}</div><div class="readout-label">Shadow blocks opened (vs {total_baseline} uncoordinated)</div></div>
        <div class="readout"><div class="readout-val good">{overall_reduction:.1f}%</div><div class="readout-label">Monthly downtime reduction</div></div>
        <div class="readout"><div class="readout-val">{backlog_end}</div><div class="readout-label">Backlog remaining at month end</div></div>
    </div>
    """, unsafe_allow_html=True)

    fig1 = go.Figure()
    fig1.add_trace(go.Bar(x=monthly_summary["week"], y=monthly_summary["tasks_scheduled"],
                           name="Scheduled", marker_color=DEPT_COLOR["TMS"]))
    fig1.add_trace(go.Bar(x=monthly_summary["week"], y=monthly_summary["backlog_remaining"],
                           name="Backlog remaining", marker_color=AMBER))
    fig1.update_layout(
        barmode="group", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        font=dict(family="IBM Plex Sans", color=PAPER, size=12),
        legend=dict(orientation="h", y=1.15, x=0, font=dict(size=11.5)),
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(title="Week", gridcolor=LINE, dtick=1),
        yaxis=dict(gridcolor=LINE),
        height=300,
    )
    st.markdown('<div class="panel"><p class="panel-title">Backlog burn-down</p>'
                 '<p class="panel-sub">Tasks scheduled vs. tasks still outstanding, week over week — the rolling Weekly Micro Plan clearing the backlog.</p></div>',
                 unsafe_allow_html=True)
    st.plotly_chart(fig1, use_container_width=True, config={"displayModeBar": False})

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=monthly_summary["week"], y=monthly_summary["baseline_corridors"],
                           name="If uncoordinated", marker_color=f"{FOG}"))
    fig2.add_trace(go.Bar(x=monthly_summary["week"], y=monthly_summary["corridors_used"],
                           name="Actual Shadow Blocks", marker_color=GREEN))
    fig2.update_layout(
        barmode="group", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        font=dict(family="IBM Plex Sans", color=PAPER, size=12),
        legend=dict(orientation="h", y=1.15, x=0, font=dict(size=11.5)),
        margin=dict(l=10, r=10, t=30, b=10),
        xaxis=dict(title="Week", gridcolor=LINE, dtick=1),
        yaxis=dict(gridcolor=LINE, title="Blocks"),
        height=300,
    )
    st.markdown('<div class="panel"><p class="panel-title">Coordination impact: blocks opened vs. the uncoordinated baseline</p>'
                 '<p class="panel-sub">"Uncoordinated" = one separate block per task (today\'s approach). The gap is the real, computed downtime saved by Shadow Blocks.</p></div>',
                 unsafe_allow_html=True)
    st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar": False})

    st.markdown(f"""
    <div class="panel">
        <p class="panel-title">Weekly Micro Plan summary</p>
        <table class="ctl-table">
            <tr><th>Week</th><th>Scheduled</th><th>Blocks used</th><th>Baseline blocks</th><th>Downtime reduction</th><th>Multi-dept blocks</th><th>Standalone</th><th>Integrations</th><th>New emergencies</th><th>Backlog after</th></tr>
            {"".join(
                f'<tr><td>Week {int(r.week)}</td><td>{int(r.tasks_scheduled)}</td><td>{int(r.corridors_used)}</td>'
                f'<td>{int(r.baseline_corridors)}</td><td class="mono">{r.downtime_reduction_pct:.1f}%</td>'
                f'<td>{int(r.multi_dept_corridors)}</td><td>{int(r.standalone_blocks)}</td>'
                f'<td>{int(r.integration_requests_fulfilled)}/{int(r.integration_requests_total)}</td>'
                f'<td>{int(r.new_emergencies_this_week)}</td><td>{int(r.backlog_remaining)}</td></tr>'
                for r in monthly_summary.itertuples()
            )}
        </table>
    </div>
    """, unsafe_allow_html=True)
