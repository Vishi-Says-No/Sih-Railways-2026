"""
data_sources/base.py
---------------------
The seam between "where does raw railway data come from" and "what the
pipeline does with it". Every adapter -- synthetic or a real Railways API --
must return pandas DataFrames with EXACTLY the same columns the synthetic
generator produces today, because spatial_layer.py / priority_model.py /
scheduler.py were built against that schema. Swap the adapter, nothing
downstream changes.

`since` is an incremental cursor (a datetime). Adapters should return only
records created/updated after `since` when it's provided -- this is what
lets a live integration poll every few minutes without re-pulling the whole
system's history each time. Adapters that can't filter server-side (e.g. the
synthetic one) can just ignore it and return everything.
"""

from abc import ABC, abstractmethod
from datetime import date, datetime

import pandas as pd


class DataSourceAdapter(ABC):
    """Contract every data source (synthetic or live) must satisfy."""

    @abstractmethod
    def fetch_tms_defects(self, since: datetime = None) -> pd.DataFrame:
        """Columns: defect_id, date_reported, section, line_type, start_km,
        end_km, defect_type, severity, asset_age_years, est_duration_mins,
        due_date, available_from_week, is_emergency."""

    @abstractmethod
    def fetch_smms_defects(self, since: datetime = None) -> pd.DataFrame:
        """Columns: fault_id, date_reported, station_id, gear_type, asset_id,
        fault_description, priority, asset_age_years, req_clearance_mins,
        due_date, available_from_week, is_emergency."""

    @abstractmethod
    def fetch_tdms_defects(self, since: datetime = None) -> pd.DataFrame:
        """Columns: ticket_id, date_reported, power_sector, elementary_sec,
        mast_id, issue, isolation_req, asset_age_years, duration_mins,
        due_date, available_from_week, is_emergency."""

    @abstractmethod
    def fetch_train_timetable(self, start_date: date, end_date: date) -> pd.DataFrame:
        """Columns: train_id, date, section, line, dep_time, arr_time, train_type."""

    @abstractmethod
    def fetch_goods_forecast(self, start_date: date, end_date: date) -> pd.DataFrame:
        """Columns: forecast_id, date, section, line, window_start, window_end,
        train_type, forecast_priority."""

    @abstractmethod
    def fetch_asset_master(self) -> dict:
        """Returns the equivalent of MASTER_ASSET_MAP: discrete SMMS/TDMS
        asset IDs resolved to {km, section, line}. Refresh weekly, not per-sync
        -- this rarely changes and shouldn't be re-pulled on every poll."""
