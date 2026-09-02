"""特征目录与计算配置。

新增特征时先在这里声明所属配置，再在计算模块实现公式。这样调用方可以稳定选择
轻量的日常选股特征或更重的研究/风险特征，而不必了解缓存文件布局。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    group: str
    description: str
    unit: str = ""


CORE_FEATURES = (
    "close", "high", "low", "amount", "vol",
    "ma5", "ma10", "ma20", "ma50", "ma150", "ma200",
    "mom1", "mom5", "mom10", "mom20", "mom60",
    "amp5", "amp10", "amp20", "amp40",
    "amount_ratio_5", "amount_ratio_20", "vol_ratio_5", "vol_ratio_20",
    "near_high_60", "near_high_250", "breakout_10", "breakout_20",
    "overlap_pct",
)

LIQUIDITY_FEATURES = (
    "open", "pre_close", "volume", "turnover_rate",
    "day_amp_pct", "close_position_pct", "gap_pct",
    "amount_ma5", "amount_ma20", "volume_ma5", "volume_ma20",
    "volume_ratio_5", "volume_ratio_20", "turnover_ma20", "vwap",
)

RISK_FEATURES = (
    "atr14_pct", "volatility_20_ann_pct", "downside_vol_20_ann_pct",
    "drawdown_from_high_60_pct", "amihud_20",
    "market_relative_mom20", "market_relative_mom60",
)

FEATURE_PROFILES = {
    "core": CORE_FEATURES,
    "liquidity": CORE_FEATURES + LIQUIDITY_FEATURES,
    "research": CORE_FEATURES + LIQUIDITY_FEATURES + RISK_FEATURES,
}

FEATURE_CATALOG = {
    "amount_ratio_20": FeatureSpec(
        "amount_ratio_20", "liquidity", "当日成交额 / 前20日平均成交额", "倍"
    ),
    "volume_ratio_20": FeatureSpec(
        "volume_ratio_20", "liquidity", "当日成交量 / 前20日平均成交量", "倍"
    ),
    "turnover_rate": FeatureSpec("turnover_rate", "liquidity", "日换手率", "%"),
    "vwap": FeatureSpec("vwap", "liquidity", "成交额 / 成交量", "元"),
    "atr14_pct": FeatureSpec("atr14_pct", "risk", "14日真实波幅占收盘价", "%"),
    "volatility_20_ann_pct": FeatureSpec(
        "volatility_20_ann_pct", "risk", "20日收益率年化波动率", "%"
    ),
    "downside_vol_20_ann_pct": FeatureSpec(
        "downside_vol_20_ann_pct", "risk", "20日下行收益年化波动率", "%"
    ),
    "amihud_20": FeatureSpec(
        "amihud_20", "liquidity", "20日绝对收益/亿元成交额均值", "每亿元"
    ),
    "market_relative_mom20": FeatureSpec(
        "market_relative_mom20", "relative", "20日动量减全市场中位数", "百分点"
    ),
}


def feature_keys(profile: str) -> tuple[str, ...]:
    """返回指定配置的特征键，并对未知配置给出可操作错误。"""
    try:
        return FEATURE_PROFILES[profile]
    except KeyError as exc:
        options = ", ".join(FEATURE_PROFILES)
        raise ValueError(f"未知特征配置 {profile!r}，可选: {options}") from exc
