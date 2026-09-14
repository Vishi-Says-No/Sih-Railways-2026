"""
data_sources/api_source.py
-----------------------------
Live adapter for the real TMS / SMMS / TDMS / COA (timetable + FOIS goods
forecast) APIs, once Railways/CRIS provisions endpoints + credentials.

This is a TEMPLATE, not a finished integration -- nobody outside Railways
IT has the real API contract (exact paths, field names, auth flow). What
IS final is the shape: every fetch_* method must return the same columns
SyntheticAdapter returns, because that's the schema spatial_layer.py etc.
were built against. Swapping this in for SyntheticAdapter should require
zero changes anywhere else in the pipeline.

Things you WILL need from Railways/CRIS before this is real:
  - Base URLs for each system (likely reached only from inside Railnet, or
    via a gateway/VPN they provision)
  - Auth mechanism -- API key header vs. OAuth2 client-credentials vs. mTLS
  - Exact field names / pagination style / rate limits for each endpoint
  - Whether "since" filtering is supported server-side, or you must pull
    a fixed window and de-dupe client-side

Everything below (retry/backoff, pagination loop, `_map_*` functions) is
built to make swapping in the real field names a small, localized edit --
not a rewrite of this file or anything downstream.
"""

import os
import time
from datetime import date, datetime

import pandas as pd
import requests

from data_sources.base import DataSourceAdapter


class RailwayAPIError(RuntimeError):
    pass


class RailwayAPIAdapter(DataSourceAdapter):
    def __init__(self, config: dict):
        """
        config keys (see data_sources/config.py for how these are loaded
        from environment variables):
            tms_base_url, smms_base_url, tdms_base_url, coa_base_url,
            api_key, timeout_secs, max_retries
        """
        self.cfg = config
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {config['api_key']}",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------
    # Low-level HTTP with retry/backoff -- never hammer an operational
    # railway system; back off and give up cleanly rather than looping.
    # ------------------------------------------------------------------
    def _get(self, base_url: str, path: str, params: dict = None) -> dict:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        max_retries = self.cfg.get("max_retries", 3)
        timeout = self.cfg.get("timeout_secs", 10)

        for attempt in range(1, max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=timeout)
                if resp.status_code == 429:  # rate-limited -- honor backoff
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                if attempt == max_retries:
                    raise RailwayAPIError(f"GET {url} failed after {max_retries} attempts: {e}") from e
                time.sleep(2 ** attempt)  # exponential backoff: 2s, 4s, 8s...
        raise RailwayAPIError(f"GET {url} exhausted retries")

    def _get_paginated(self, base_url: str, path: str, params: dict = None) -> list:
        """Most railway integration gateways paginate. Adjust the
        page-token field names to whatever the real API actually returns."""
        params = dict(params or {})
        records, cursor = [], None
        while True:
            if cursor:
                params["page_token"] = cursor  # TODO: confirm real param name
            payload = self._get(base_url, path, params)
            records.extend(payload.get("results", payload.get("data", [])))
            cursor = payload.get("next_page_token")  # TODO: confirm real field
            if not cursor:
                break
        return records

    # ------------------------------------------------------------------
    # TMS
    # ------------------------------------------------------------------
    def fetch_tms_defects(self, since: datetime = None) -> pd.DataFrame:
        params = {"since": since.isoformat()} if since else {}
        raw = self._get_paginated(self.cfg["tms_base_url"], "/api/v1/defects", params)
        return pd.DataFrame([self._map_tms_row(r) for r in raw])

    @staticmethod
    def _map_tms_row(r: dict) -> dict:
        # TODO: replace right-hand keys with the real TMS response field names
        return {
            "defect_id": r["defectId"],
            "date_reported": r["reportedOn"],
            "section": r["section"],
            "line_type": r["lineType"],
            "start_km": r["startKm"],
            "end_km": r.get("endKm", r["startKm"]),
            "defect_type": r["defectType"],
            "severity": r["severity"],
            "asset_age_years": r.get("assetAgeYears"),
            "est_duration_mins": r["estimatedDurationMins"],
            "due_date": r["slaDueDate"],
            "available_from_week": r.get("availableFromWeek", 1),
            "is_emergency": r.get("isEmergency", False),
        }

    # ------------------------------------------------------------------
    # SMMS
    # ------------------------------------------------------------------
    def fetch_smms_defects(self, since: datetime = None) -> pd.DataFrame:
        params = {"since": since.isoformat()} if since else {}
        raw = self._get_paginated(self.cfg["smms_base_url"], "/api/v1/faults", params)
        return pd.DataFrame([self._map_smms_row(r) for r in raw])

    @staticmethod
    def _map_smms_row(r: dict) -> dict:
        # TODO: replace right-hand keys with the real SMMS response field names
        return {
            "fault_id": r["faultId"],
            "date_reported": r["loggedOn"],
            "station_id": r["stationId"],
            "gear_type": r["gearType"],
            "asset_id": r["assetId"],
            "fault_description": r.get("description", ""),
            "priority": r["priority"],
            "asset_age_years": r.get("assetAgeYears"),
            "req_clearance_mins": r["requiredClearanceMins"],
            "due_date": r["slaDueDate"],
            "available_from_week": r.get("availableFromWeek", 1),
            "is_emergency": r.get("isEmergency", False),
        }

    # ------------------------------------------------------------------
    # TDMS
    # ------------------------------------------------------------------
    def fetch_tdms_defects(self, since: datetime = None) -> pd.DataFrame:
        params = {"since": since.isoformat()} if since else {}
        raw = self._get_paginated(self.cfg["tdms_base_url"], "/api/v1/tickets", params)
        return pd.DataFrame([self._map_tdms_row(r) for r in raw])

    @staticmethod
    def _map_tdms_row(r: dict) -> dict:
        # TODO: replace right-hand keys with the real TDMS response field names
        return {
            "ticket_id": r["ticketId"],
            "date_reported": r["createdOn"],
            "power_sector": r["powerSector"],
            "elementary_sec": r["elementarySection"],
            "mast_id": r["mastId"],
            "issue": r["issue"],
            "isolation_req": r.get("isolationRequirement", "NONE"),
            "asset_age_years": r.get("assetAgeYears"),
            "duration_mins": r["estimatedDurationMins"],
            "due_date": r["slaDueDate"],
            "available_from_week": r.get("availableFromWeek", 1),
            "is_emergency": r.get("isEmergency", False),
        }

    # ------------------------------------------------------------------
    # COA -- Train Time Table (passenger)
    # ------------------------------------------------------------------
    def fetch_train_timetable(self, start_date: date, end_date: date) -> pd.DataFrame:
        params = {"from": start_date.isoformat(), "to": end_date.isoformat()}
        raw = self._get_paginated(self.cfg["coa_base_url"], "/api/v1/timetable", params)
        return pd.DataFrame([self._map_timetable_row(r) for r in raw])

    @staticmethod
    def _map_timetable_row(r: dict) -> dict:
        # TODO: replace right-hand keys with the real COA timetable field names
        return {
            "train_id": r["trainNumber"],
            "date": r["serviceDate"],
            "section": r["section"],
            "line": r["line"],
            "dep_time": r["departure"],
            "arr_time": r["arrival"],
            "train_type": "PASSENGER",
        }

    # ------------------------------------------------------------------
    # COA / FOIS -- Goods Train Forecast
    # ------------------------------------------------------------------
    def fetch_goods_forecast(self, start_date: date, end_date: date) -> pd.DataFrame:
        params = {"from": start_date.isoformat(), "to": end_date.isoformat()}
        raw = self._get_paginated(self.cfg["coa_base_url"], "/api/v1/goods-forecast", params)
        return pd.DataFrame([self._map_goods_row(r) for r in raw])

    @staticmethod
    def _map_goods_row(r: dict) -> dict:
        # TODO: replace right-hand keys with the real FOIS forecast field names
        return {
            "forecast_id": r["forecastId"],
            "date": r["serviceDate"],
            "section": r["section"],
            "line": r["line"],
            "window_start": r["windowStart"],
            "window_end": r["windowEnd"],
            "train_type": "GOODS",
            "forecast_priority": r.get("priority", "MEDIUM"),
        }

    # ------------------------------------------------------------------
    # Asset master (GIS / linear-reference system) -- refresh weekly
    # ------------------------------------------------------------------
    def fetch_asset_master(self) -> dict:
        # TODO: replace with the real GIS/asset-register endpoint + shape
        raw = self._get(self.cfg["coa_base_url"], "/api/v1/asset-master")
        smms_assets = {a["assetId"]: {"km": a["km"], "section": a["section"], "line": a["line"]}
                       for a in raw.get("smms_assets", [])}
        tdms_masts = {a["mastId"]: {"km": a["km"], "section": a["section"], "line": a["line"]}
                      for a in raw.get("tdms_masts", [])}
        return {"SMMS_Assets": smms_assets, "TDMS_Masts": tdms_masts}
