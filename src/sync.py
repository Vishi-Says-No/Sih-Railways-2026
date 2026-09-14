"""
sync.py
-------
Orchestrates "pull from whichever DataSourceAdapter is configured, write it
to data/ in the same shape the rest of the pipeline already expects".

Two things a live integration needs that a demo script doesn't:
  1. Incremental upsert -- defects are fetched with `since=<last sync>` and
     merged into the existing CSV by their id, instead of overwriting the
     whole file every run (which would erase anything already scheduled).
  2. A freshness gate -- `needs_resync()` stops app.py from re-polling a
     live railway system on every dashboard page load; it only re-syncs
     once SYNC_INTERVAL_MINUTES has elapsed.

Timetable/goods-forecast and the asset master are handled differently from
defects: they're refreshed as a full snapshot for the planning horizon
(they're schedules, not an event log to accumulate) -- the asset master
additionally only refreshes weekly since it rarely changes.

Run directly:
    python src/sync.py
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from data_generator import MONTH_START, HORIZON_DAYS
from data_sources.config import get_adapter

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_PATH = os.path.join(DATA_DIR, "_sync_state.json")
ASSET_MASTER_PATH = os.path.join(DATA_DIR, "asset_master.json")


def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def _save_state(state: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _upsert_csv(filename: str, new_df: pd.DataFrame, key: str):
    path = os.path.join(DATA_DIR, filename)
    if new_df is None or new_df.empty:
        if not os.path.exists(path):
            (new_df if new_df is not None else pd.DataFrame()).to_csv(path, index=False)
        return
    if os.path.exists(path):
        existing = pd.read_csv(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=key, keep="last")
    else:
        combined = new_df
    combined.to_csv(path, index=False)


def _asset_master_stale(max_age_days: int = 7) -> bool:
    if not os.path.exists(ASSET_MASTER_PATH):
        return True
    age_secs = time.time() - os.path.getmtime(ASSET_MASTER_PATH)
    return age_secs > max_age_days * 86400


def is_live_mode() -> bool:
    return os.environ.get("DATA_SOURCE", "synthetic").lower() == "api"


def data_bootstrapped() -> bool:
    """True once the core datasets exist on disk. In synthetic/demo mode we
    only ever (re)generate from scratch on first bootstrap or an explicit
    reset -- never on the resync timer -- so an operator's manually-logged
    faults can never be silently overwritten by a periodic resync mid-demo.
    Live API mode has no such risk (the adapter never writes CSVs itself),
    so it keeps polling on the normal timer regardless of this flag."""
    required = ["tms_defects.csv", "smms_defects.csv", "tdms_defects.csv",
                "train_timetable.csv", "goods_forecast.csv"]
    return all(os.path.exists(os.path.join(DATA_DIR, f)) for f in required)


def needs_resync(interval_minutes: int) -> bool:
    """Freshness gate app.py calls before deciding whether to hit the
    adapter again -- prevents polling a live railway system on every
    dashboard page load."""
    state = _load_state()
    if "last_synced_at" not in state:
        return True
    last = datetime.fromisoformat(state["last_synced_at"])
    return (datetime.now(timezone.utc) - last).total_seconds() > interval_minutes * 60


def sync_all(incremental: bool = True) -> dict:
    adapter = get_adapter()
    state = _load_state()
    since = datetime.fromisoformat(state["last_synced_at"]) if (incremental and "last_synced_at" in state) else None

    # --- Defects: incremental fetch, upsert by id -------------------------
    tms = adapter.fetch_tms_defects(since=since)
    smms = adapter.fetch_smms_defects(since=since)
    tdms = adapter.fetch_tdms_defects(since=since)
    _upsert_csv("tms_defects.csv", tms, key="defect_id")
    _upsert_csv("smms_defects.csv", smms, key="fault_id")
    _upsert_csv("tdms_defects.csv", tdms, key="ticket_id")

    # --- Timetable / goods forecast: full snapshot for the horizon --------
    horizon_start = MONTH_START
    horizon_end = MONTH_START + timedelta(days=HORIZON_DAYS - 1)
    timetable = adapter.fetch_train_timetable(horizon_start, horizon_end)
    goods = adapter.fetch_goods_forecast(horizon_start, horizon_end)
    timetable.to_csv(os.path.join(DATA_DIR, "train_timetable.csv"), index=False)
    goods.to_csv(os.path.join(DATA_DIR, "goods_forecast.csv"), index=False)

    # --- Asset master: weekly refresh, not every sync ----------------------
    if _asset_master_stale():
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ASSET_MASTER_PATH, "w") as f:
            json.dump(adapter.fetch_asset_master(), f, indent=2)

    state["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    state["last_sync_counts"] = {"tms": len(tms), "smms": len(smms), "tdms": len(tdms)}
    _save_state(state)
    return state


if __name__ == "__main__":
    result = sync_all()
    print(f"Synced at {result['last_synced_at']}. New/updated rows this sync: {result['last_sync_counts']}")
