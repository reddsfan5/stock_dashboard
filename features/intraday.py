"""盘中特征计算。"""

import numpy as np
import pandas as pd
from typing import Optional


def add_intraday_volume_ratio(
    frame: pd.DataFrame,
    *,
    lookback: int = 5,
    min_periods: Optional[int] = None,
) -> pd.DataFrame:
    """增加累计成交量和过去 N 日同分钟量比。

    ``盘中量比 = 当日截至当前分钟累计成交量 / 前 N 个交易日同一时刻平均累计量``。
    分母严格排除当天；不足 ``min_periods`` 个历史样本时返回空值。
    """
    if lookback <= 0:
        raise ValueError("lookback 必须大于 0")
    required = {"代码", "时间", "成交量"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"分钟数据缺少字段: {', '.join(sorted(missing))}")
    minimum = lookback if min_periods is None else min_periods
    if minimum <= 0 or minimum > lookback:
        raise ValueError("min_periods 必须在 1 到 lookback 之间")

    result = frame.copy()
    result["时间"] = pd.to_datetime(result["时间"], errors="coerce")
    result["成交量"] = pd.to_numeric(result["成交量"], errors="coerce")
    result = result.sort_values(["代码", "时间"]).reset_index(drop=True)
    result["交易日"] = result["时间"].dt.normalize()
    result["分钟"] = result["时间"].dt.strftime("%H:%M")
    result["累计成交量"] = result.groupby(
        ["代码", "交易日"], sort=False
    )["成交量"].cumsum()

    def prior_mean(values: pd.Series) -> pd.Series:
        return values.rolling(lookback, min_periods=minimum).mean().shift(1)

    baseline = result.groupby(
        ["代码", "分钟"], sort=False
    )["累计成交量"].transform(prior_mean)
    result["盘中量比"] = result["累计成交量"] / baseline.replace(0, np.nan)
    return result.drop(columns=["交易日", "分钟"])
