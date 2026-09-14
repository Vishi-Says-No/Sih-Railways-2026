"""
data_sources/config.py
-------------------------
Single place that decides "where does data come from this run" and holds
connection settings. Everything is read from environment variables so
credentials never sit in source control, and switching from the demo
adapter to the live one is a deployment config change, not a code change.

    DATA_SOURCE=synthetic   (default -- Faker-generated demo data)
    DATA_SOURCE=api         (live TMS/SMMS/TDMS/COA integration)

    RAILWAY_API_KEY=...
    TMS_API_BASE_URL=https://.../tms
    SMMS_API_BASE_URL=https://.../smms
    TDMS_API_BASE_URL=https://.../tdms
    COA_API_BASE_URL=https://.../coa
    SYNC_INTERVAL_MINUTES=15     -- how often app.py is allowed to re-poll
    API_TIMEOUT_SECS=10
    API_MAX_RETRIES=3
"""

import os

from data_sources.base import DataSourceAdapter
from data_sources.synthetic_source import SyntheticAdapter


def get_adapter() -> DataSourceAdapter:
    source = os.environ.get("DATA_SOURCE", "synthetic").lower()

    if source == "synthetic":
        return SyntheticAdapter()

    if source == "api":
        # Imported lazily -- `requests` is only needed in live-API mode.
        from data_sources.api_source import RailwayAPIAdapter
        required = ["RAILWAY_API_KEY", "TMS_API_BASE_URL", "SMMS_API_BASE_URL",
                    "TDMS_API_BASE_URL", "COA_API_BASE_URL"]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                f"DATA_SOURCE=api but missing required env vars: {', '.join(missing)}. "
                f"See data_sources/config.py for the full list."
            )
        config = {
            "api_key": os.environ["RAILWAY_API_KEY"],
            "tms_base_url": os.environ["TMS_API_BASE_URL"],
            "smms_base_url": os.environ["SMMS_API_BASE_URL"],
            "tdms_base_url": os.environ["TDMS_API_BASE_URL"],
            "coa_base_url": os.environ["COA_API_BASE_URL"],
            "timeout_secs": int(os.environ.get("API_TIMEOUT_SECS", 10)),
            "max_retries": int(os.environ.get("API_MAX_RETRIES", 3)),
        }
        return RailwayAPIAdapter(config)

    raise ValueError(f"Unknown DATA_SOURCE={source!r}. Use 'synthetic' or 'api'.")


def sync_interval_minutes() -> int:
    return int(os.environ.get("SYNC_INTERVAL_MINUTES", 15))
