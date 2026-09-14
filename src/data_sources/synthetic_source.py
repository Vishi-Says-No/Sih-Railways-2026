"""
data_sources/synthetic_source.py
----------------------------------
Wraps the existing Faker-based generators behind the DataSourceAdapter
interface. This is the default adapter -- everything you've already been
running -- now expressed the same way a live API adapter will be, so
switching between them is a one-line config change, not a rewrite.
"""

from datetime import date, datetime

import pandas as pd

from data_sources.base import DataSourceAdapter
import data_generator as gen


class SyntheticAdapter(DataSourceAdapter):
    """Demo/offline data source. Ignores `since` -- always regenerates the
    full synthetic backlog + horizon, seeded for reproducibility."""

    def fetch_tms_defects(self, since: datetime = None) -> pd.DataFrame:
        return gen.gen_tms()

    def fetch_smms_defects(self, since: datetime = None) -> pd.DataFrame:
        return gen.gen_smms()

    def fetch_tdms_defects(self, since: datetime = None) -> pd.DataFrame:
        return gen.gen_tdms()

    def fetch_train_timetable(self, start_date: date = None, end_date: date = None) -> pd.DataFrame:
        return gen.gen_train_timetable()

    def fetch_goods_forecast(self, start_date: date = None, end_date: date = None) -> pd.DataFrame:
        return gen.gen_goods_forecast()

    def fetch_asset_master(self) -> dict:
        return gen.MASTER_ASSET_MAP
