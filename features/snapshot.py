"""面向选股页面的截面决策快照。

日线事实表保留原始字段，策略模块保留各自的信号逻辑；本模块只负责把网页需要
横向比较的量价、流动性、风险和估值指标整理成每个代码一行的稳定快照。
"""

from collections import OrderedDict
from typing import Dict, Optional

import numpy as np
import pandas as pd


SCREENING_METRICS = OrderedDict([
    ("指标日期", "date"),
    ("成交量比20", "volume_ratio_20"),
    ("成交额比20", "amount_ratio_20"),
    ("换手率%", "turnover_rate"),
    ("20日动量%", "momentum_20_pct"),
    ("市场相对强弱20%", "market_relative_momentum_20_pct"),
    ("ATR14%", "atr14_pct"),
    ("20日年化波动%", "volatility_20_ann_pct"),
    ("距60日高点%", "drawdown_from_high_60_pct"),
    ("动态PE", "dynamic_pe"),
    ("流通市值(亿)", "circulating_market_cap_yi"),
    ("供应商量比", "vendor_volume_ratio"),
])

METRIC_DEFINITIONS = {
    "成交量比20": "当日成交量 ÷ 前20个交易日平均成交量，分母不含当日",
    "成交额比20": "当日成交额 ÷ 前20个交易日平均成交额，分母不含当日",
    "换手率%": "数据源提供的当日换手率，单位为百分数",
    "20日动量%": "当前收盘价相对20个交易日前收盘价的涨跌幅",
    "市场相对强弱20%": "个股20日动量减同一指标日全市场中位数",
    "ATR14%": "14日真实波幅均值占当前收盘价的比例",
    "20日年化波动%": "20日收益率标准差按252个交易日年化",
    "距60日高点%": "当前收盘价相对近60日最高收盘价的回撤，0表示处于高点",
    "动态PE": "腾讯收盘快照提供的动态市盈率；ETF等无适用值的标的留空",
    "流通市值(亿)": "腾讯收盘快照提供的流通市值，换算为亿元",
    "供应商量比": "行情供应商收盘快照中的量比，仅作为供应商口径对照",
}


def _rolling_previous_mean(
    frame: pd.DataFrame, field: str, window: int
) -> pd.Series:
    """逐代码计算前 N 日均值，严格排除当前行。"""
    return frame.groupby("代码", sort=False)[field].transform(
        lambda values: values.shift(1).rolling(window, min_periods=window).mean()
    )


def _rolling_current(
    frame: pd.DataFrame, field: str, window: int, operation: str
) -> pd.Series:
    grouped = frame.groupby("代码", sort=False)[field]
    return grouped.transform(
        lambda values: getattr(values.rolling(window, min_periods=window), operation)()
    )


def build_decision_snapshot(
    daily: pd.DataFrame,
    basic: Optional[pd.DataFrame] = None,
    *,
    as_of=None,
) -> pd.DataFrame:
    """把日线和日度基础快照整理为每代码一行的网页指标。

    仅需要最近 65 个交易日即可计算当前页面指标，避免为了一个截面结果构造整段
    全市场宽表。``as_of`` 用于历史复核；默认使用日线中的最新日期。
    """
    required = {"代码", "日期", "最高", "最低", "收盘", "成交额"}
    missing = required.difference(daily.columns)
    if missing:
        raise ValueError(f"决策快照缺少日线字段: {', '.join(sorted(missing))}")
    if daily.empty:
        return pd.DataFrame(columns=["代码", *SCREENING_METRICS.keys()])

    columns = [
        column for column in
        ("代码", "日期", "最高", "最低", "收盘", "前收",
         "成交量", "成交额", "换手率%")
        if column in daily.columns
    ]
    frame = daily.loc[:, columns].copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["代码", "日期", "收盘"])
    target = (
        pd.Timestamp(as_of).normalize()
        if as_of is not None else frame["日期"].max()
    )
    frame = frame[frame["日期"] <= target]
    frame = (
        frame.groupby("代码", sort=False, group_keys=False).tail(65)
        .sort_values(["代码", "日期"])
        .reset_index(drop=True)
    )
    for field in ("最高", "最低", "收盘", "前收", "成交量", "成交额", "换手率%"):
        if field not in frame:
            frame[field] = np.nan
        frame[field] = pd.to_numeric(frame[field], errors="coerce")

    grouped = frame.groupby("代码", sort=False)
    shifted_close = grouped["收盘"].shift(1)
    pre_close = frame["前收"].combine_first(shifted_close)
    frame["volume_ratio_20"] = (
        frame["成交量"] / _rolling_previous_mean(frame, "成交量", 20).replace(0, np.nan)
    )
    frame["amount_ratio_20"] = (
        frame["成交额"] / _rolling_previous_mean(frame, "成交额", 20).replace(0, np.nan)
    )
    frame["momentum_20_pct"] = (
        frame["收盘"] / grouped["收盘"].shift(20).replace(0, np.nan) - 1
    ) * 100
    frame["_true_range"] = np.maximum.reduce([
        (frame["最高"] - frame["最低"]).to_numpy(),
        (frame["最高"] - pre_close).abs().to_numpy(),
        (frame["最低"] - pre_close).abs().to_numpy(),
    ])
    frame["atr14_pct"] = (
        _rolling_current(frame, "_true_range", 14, "mean")
        / frame["收盘"].replace(0, np.nan) * 100
    )
    frame["_return"] = frame["收盘"] / shifted_close.replace(0, np.nan) - 1
    frame["volatility_20_ann_pct"] = (
        _rolling_current(frame, "_return", 20, "std") * np.sqrt(252) * 100
    )
    frame["drawdown_from_high_60_pct"] = (
        frame["收盘"]
        / _rolling_current(frame, "收盘", 60, "max").replace(0, np.nan)
        - 1
    ) * 100

    latest = grouped.tail(1).copy()
    latest["date"] = latest["日期"].dt.strftime("%Y-%m-%d")
    latest["turnover_rate"] = latest["换手率%"]
    latest["market_relative_momentum_20_pct"] = (
        latest["momentum_20_pct"]
        - latest.groupby("日期")["momentum_20_pct"].transform("median")
    )

    if basic is not None and not basic.empty:
        facts = basic.copy()
        facts["日期"] = pd.to_datetime(facts["日期"], errors="coerce").dt.normalize()
        facts = (
            facts[facts["日期"] <= target]
            .sort_values(["代码", "日期"])
            .groupby("代码", sort=False, as_index=False).tail(1)
        )
        facts = facts.rename(columns={
            "供应商量比": "vendor_volume_ratio",
            "市盈率_动态": "dynamic_pe",
            "流通市值": "circulating_market_cap",
        })
        wanted = [
            column for column in
            ("代码", "vendor_volume_ratio", "dynamic_pe", "circulating_market_cap")
            if column in facts.columns
        ]
        latest = latest.merge(facts[wanted], on="代码", how="left")

    for field in ("vendor_volume_ratio", "dynamic_pe", "circulating_market_cap"):
        if field not in latest:
            latest[field] = np.nan
    latest["circulating_market_cap_yi"] = latest["circulating_market_cap"] / 1e8
    snapshot = latest.loc[:, ["代码", *SCREENING_METRICS.values()]].rename(
        columns={value: label for label, value in SCREENING_METRICS.items()}
    )
    numeric = [column for column in snapshot.columns if column not in ("代码", "指标日期")]
    snapshot[numeric] = snapshot[numeric].round(4)
    return snapshot.reset_index(drop=True)


def enrich_screening_results(
    results: Dict[str, pd.DataFrame], snapshot: pd.DataFrame
) -> Dict[str, pd.DataFrame]:
    """把统一决策指标附加到每个选股模块结果，保留模块原有列顺序。"""
    if snapshot.empty:
        return {key: value.copy() for key, value in results.items()}
    indexed = snapshot.set_index("代码")
    enriched: Dict[str, pd.DataFrame] = {}
    for key, value in results.items():
        frame = value.copy()
        if frame.empty or "代码" not in frame:
            enriched[key] = frame
            continue
        additions = indexed.reindex(frame["代码"].astype(str)).reset_index(drop=True)
        for column in SCREENING_METRICS:
            if column not in frame.columns:
                frame[column] = additions[column].to_numpy()
        enriched[key] = frame
    return enriched


def snapshot_lookup(snapshot: pd.DataFrame) -> Dict[str, Dict]:
    """生成供 HTML 载荷使用的 JSON 友好映射。"""
    if snapshot is None or snapshot.empty:
        return {}
    cleaned = snapshot.replace({np.nan: None})
    return {
        str(row["代码"]): {
            str(column): row[column]
            for column in cleaned.columns if column != "代码"
        }
        for _, row in cleaned.iterrows()
    }

