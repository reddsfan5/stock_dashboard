"""A 股日度基础快照缓存。

该表保存供应商在收盘快照中给出的估值、总/流通市值和即时量比。它与可复算的
OHLCV 日 K 主表分离，避免供应商口径字段渗入行情事实层。
"""

import os
from typing import Optional

import pandas as pd

from data.schema import empty_daily_basic, ensure_daily_basic_schema
from data.storage import atomic_write_parquet


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(PROJECT_DIR, "cache", "daily_basic_cache.parquet")


class DailyBasicData:
    """按 ``代码+日期`` 幂等维护日度基础快照。"""

    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self._cache: Optional[pd.DataFrame] = None

    @property
    def cache(self) -> pd.DataFrame:
        if self._cache is None:
            if os.path.exists(self.cache_file):
                self._cache = ensure_daily_basic_schema(pd.read_parquet(self.cache_file))
            else:
                self._cache = empty_daily_basic()
        return self._cache

    def upsert(self, rows: pd.DataFrame) -> int:
        incoming = ensure_daily_basic_schema(rows)
        if len(incoming) == 0:
            return 0
        before = len(self.cache)
        merged = incoming.copy() if before == 0 else pd.concat(
            [self.cache, incoming], ignore_index=True
        )
        self._cache = (
            merged.dropna(subset=["代码", "日期"])
            .drop_duplicates(["代码", "日期"], keep="last")
            .sort_values(["代码", "日期"])
            .reset_index(drop=True)
        )
        atomic_write_parquet(self._cache, self.cache_file)
        return len(self._cache) - before
