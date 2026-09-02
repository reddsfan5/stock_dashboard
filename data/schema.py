"""行情数据字段契约与规范化工具。

数据源适配器只负责把外部字段映射到这里定义的稳定契约；缓存、特征和业务层
不应再依赖 AkShare、腾讯或 baostock 的原始列名与单位。
"""

from typing import Iterable

import numpy as np
import pandas as pd


DAILY_BAR_REQUIRED = ("代码", "日期", "开盘", "最高", "最低", "收盘", "成交额")
DAILY_BAR_OPTIONAL = ("前收", "成交量", "换手率%")
DAILY_BAR_COLUMNS = (
    "代码", "日期", "开盘", "最高", "最低", "收盘",
    "前收", "成交量", "成交额", "换手率%",
)

DAILY_BASIC_COLUMNS = (
    "代码", "日期", "供应商量比", "市盈率_动态", "总市值", "流通市值",
)


def derive_previous_close(
    frame: pd.DataFrame,
    *,
    close_column: str = "收盘",
    source_column: str = "前收",
) -> pd.Series:
    """返回逐日涨跌使用的前收序列。

    数据源的 ``前收`` 可能只在最近增量更新的行中存在。这里优先采用有效的
    数据源值，缺失时回退到上一根日 K 的收盘价；调用方应先按同一标的、日期
    升序排列，避免跨标的串值。
    """
    close = pd.to_numeric(frame[close_column], errors="coerce")
    fallback = close.shift(1)
    if source_column not in frame:
        return fallback
    supplied = pd.to_numeric(frame[source_column], errors="coerce").astype(
        "float64"
    ).where(lambda values: values.gt(0))
    return supplied.combine_first(fallback)


def _missing(columns: Iterable[str], required: Iterable[str]) -> list[str]:
    available = set(columns)
    return [column for column in required if column not in available]


def empty_daily_bars() -> pd.DataFrame:
    """返回具有标准列顺序的空日 K 表。"""
    return pd.DataFrame(columns=DAILY_BAR_COLUMNS)


def ensure_daily_bar_schema(frame: pd.DataFrame) -> pd.DataFrame:
    """校验并规范日 K 字段、日期、数值类型和列顺序。

    可选字段缺失时补 ``NaN``，便于旧版七字段缓存无损迁移；未知扩展字段不会
    被写入主 K 线缓存。
    """
    if frame is None:
        return empty_daily_bars()
    missing = _missing(frame.columns, DAILY_BAR_REQUIRED)
    if missing:
        raise ValueError(f"日 K 缺少必需字段: {', '.join(missing)}")

    result = frame.copy()
    for column in DAILY_BAR_OPTIONAL:
        if column not in result:
            result[column] = np.nan
    result["代码"] = result["代码"].astype(str)
    result["日期"] = pd.to_datetime(result["日期"], errors="coerce")
    numeric = [column for column in DAILY_BAR_COLUMNS if column not in ("代码", "日期")]
    for column in numeric:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.loc[:, DAILY_BAR_COLUMNS]


def empty_daily_basic() -> pd.DataFrame:
    """返回具有标准列顺序的空日度基础快照表。"""
    return pd.DataFrame(columns=DAILY_BASIC_COLUMNS)


def ensure_daily_basic_schema(frame: pd.DataFrame) -> pd.DataFrame:
    """从供应商快照中提取并规范日度估值/即时指标。"""
    if frame is None or len(frame) == 0:
        return empty_daily_basic()
    missing = _missing(frame.columns, ("代码", "日期"))
    if missing:
        raise ValueError(f"日度基础快照缺少必需字段: {', '.join(missing)}")

    result = frame.copy()
    for column in DAILY_BASIC_COLUMNS:
        if column not in result:
            result[column] = np.nan
    result["代码"] = result["代码"].astype(str)
    result["日期"] = pd.to_datetime(result["日期"], errors="coerce")
    for column in DAILY_BASIC_COLUMNS[2:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.loc[:, DAILY_BASIC_COLUMNS]


def codes_missing_field_history(
    frame: pd.DataFrame,
    codes: Iterable[str],
    field: str,
    target_date,
    *,
    periods: int = 21,
) -> list[str]:
    """找出目标日前没有足够有效历史值的代码。

    只扫描约三倍窗口的自然日，避免在日常更新中反复 groupby 全历史缓存。
    """
    requested = list(dict.fromkeys(str(code) for code in codes))
    if not requested:
        return []
    if field not in frame.columns or len(frame) == 0:
        return requested
    target = pd.Timestamp(target_date).normalize()
    start = target - pd.Timedelta(days=max(periods * 3, 35))
    dates = pd.to_datetime(frame["日期"], errors="coerce").dt.normalize()
    recent = frame.loc[
        frame["代码"].astype(str).isin(requested)
        & dates.between(start, target),
        ["代码", field],
    ].copy()
    recent[field] = pd.to_numeric(recent[field], errors="coerce")
    counts = recent.loc[recent[field].gt(0)].groupby("代码")[field].count()
    available = recent.groupby("代码")[field].size()
    missing = []
    for code in requested:
        total = int(available.get(code, 0))
        required = periods if total == 0 else min(periods, total)
        if int(counts.get(code, 0)) < required:
            missing.append(code)
    return missing
