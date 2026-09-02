"""利用已有分钟行情补齐日 K 的可复算基础字段。

当前分钟缓存记录的是每分钟增量成交量，因此按 ``代码+交易日`` 求和即可得到
日成交量。补齐过程只写空值，不覆盖日线供应商已经给出的事实值。
"""

from collections.abc import Callable
import os
from typing import Optional

import numpy as np
import pandas as pd

from data.schema import ensure_daily_bar_schema
from data.storage import atomic_write_parquet


def aggregate_minute_volume(frame: pd.DataFrame) -> pd.DataFrame:
    """把分钟增量成交量聚合为 ``代码/日期/成交量``。"""
    required = {"代码", "时间", "成交量"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"分钟数据缺少字段: {', '.join(sorted(missing))}")
    values = frame.loc[:, ["代码", "时间", "成交量"]].copy()
    values["代码"] = values["代码"].astype(str)
    values["日期"] = pd.to_datetime(values["时间"], errors="coerce").dt.normalize()
    values["成交量"] = pd.to_numeric(values["成交量"], errors="coerce")
    return (
        values.dropna(subset=["日期"])
        .groupby(["代码", "日期"], as_index=False)["成交量"]
        .sum(min_count=1)
    )


def enrich_daily_volume(
    daily_path: str,
    daily_volume: pd.DataFrame,
    *,
    code_normalizer: Optional[Callable[[str], str]] = None,
) -> dict:
    """用聚合结果补齐一个日线缓存，返回迁移统计。"""
    if not os.path.exists(daily_path):
        return {"path": daily_path, "rows": 0, "filled": 0, "written": False}
    daily = pd.read_parquet(daily_path)
    if len(daily) == 0:
        return {"path": daily_path, "rows": 0, "filled": 0, "written": False}

    daily = ensure_daily_bar_schema(daily)
    source = daily_volume.copy()
    if code_normalizer:
        source["代码"] = source["代码"].astype(str).map(code_normalizer)
    source = source.drop_duplicates(["代码", "日期"], keep="last")
    lookup = source.set_index(["代码", "日期"])["成交量"]

    keys = pd.MultiIndex.from_arrays(
        [daily["代码"].astype(str), pd.to_datetime(daily["日期"]).dt.normalize()]
    )
    candidates = lookup.reindex(keys).to_numpy()
    current = pd.to_numeric(daily["成交量"], errors="coerce").to_numpy()
    fill_mask = (~np.isfinite(current) | (current <= 0)) & np.isfinite(candidates)
    filled = int(fill_mask.sum())
    if filled:
        daily.loc[fill_mask, "成交量"] = candidates[fill_mask]
        atomic_write_parquet(daily, daily_path)
    return {
        "path": daily_path,
        "rows": len(daily),
        "filled": filled,
        "written": bool(filled),
    }


def enrich_daily_caches(
    minute_path: str,
    stock_path: str,
    etf_path: str,
) -> dict:
    """只读取一次分钟缓存，同时补齐股票和 ETF 日成交量。"""
    if not os.path.exists(minute_path):
        raise FileNotFoundError(f"分钟缓存不存在: {minute_path}")
    minute = pd.read_parquet(minute_path, columns=["代码", "时间", "成交量"])
    volume = aggregate_minute_volume(minute)
    stock = enrich_daily_volume(stock_path, volume)
    etf = enrich_daily_volume(
        etf_path,
        volume,
        code_normalizer=lambda code: code[2:] if code.startswith(("sh", "sz")) else code,
    )
    return {"minute_days": int(volume["日期"].nunique()), "stock": stock, "etf": etf}
