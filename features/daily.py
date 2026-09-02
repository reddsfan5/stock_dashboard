"""股票日频特征计算。

命名约定：
- ``amount_*`` 明确表示成交额；
- ``volume_*`` 明确表示成交量；
- ``vol_ratio_*`` 仅作为旧策略兼容别名，等同 ``amount_ratio_*``。
"""

from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from features.store import FeatureStore, build_signature, scope_name
from features.catalog import LIQUIDITY_FEATURES, RISK_FEATURES, feature_keys


def _pivot(frame: pd.DataFrame, field: str) -> pd.DataFrame:
    return frame.pivot_table(
        index="日期", columns="代码", values=field, aggfunc="last"
    ).sort_index()


def _empty_like(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(np.nan, index=frame.index, columns=frame.columns)


def _rolling_ratio(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """当日值 / 前 N 个交易日均值；分母严格不包含当日。"""
    baseline = frame.rolling(window, min_periods=window).mean().shift(1)
    return frame / baseline.replace(0, np.nan)


def compute_daily_features(
    cache: pd.DataFrame,
    board_prefix: Optional[Iterable[str]] = None,
    *,
    use_cache: bool = True,
    cache_dir=None,
    profile: str = "core",
) -> Dict[str, pd.DataFrame]:
    """计算日频特征。

    ``core`` 用于日常选股；``liquidity`` 增加成交量、换手和 VWAP；
    ``research`` 再增加波动、回撤、冲击成本代理和市场相对强弱。不同配置使用
    相互隔离的版本化缓存。
    """
    selected_keys = feature_keys(profile)
    needs_liquidity = any(key in selected_keys for key in LIQUIDITY_FEATURES)
    frame = cache.copy()
    if board_prefix:
        prefixes = tuple(board_prefix)
        frame = frame[frame["代码"].str.startswith(prefixes)].copy()
    if len(frame) == 0:
        return {}
    frame["日期"] = pd.to_datetime(frame["日期"])
    frame = frame.sort_values(["代码", "日期"])

    scope = f"{scope_name(board_prefix)}-{profile}"
    signature = build_signature(frame, scope)
    store = FeatureStore(scope, cache_dir=cache_dir)
    if use_cache:
        cached = store.load(signature)
        if cached is not None:
            return cached

    close = _pivot(frame, "收盘")
    high = _pivot(frame, "最高")
    low = _pivot(frame, "最低")
    amount = _pivot(frame, "成交额")
    shifted_close = close.shift(1)

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()

    mom1 = close.pct_change(1, fill_method=None) * 100
    mom5 = close.pct_change(5, fill_method=None) * 100
    mom10 = close.pct_change(10, fill_method=None) * 100
    mom20 = close.pct_change(20, fill_method=None) * 100
    mom60 = close.pct_change(60, fill_method=None) * 100

    amp5 = (high.rolling(5).max() - low.rolling(5).min()) / low.rolling(5).min() * 100
    amp10 = (high.rolling(10).max() - low.rolling(10).min()) / low.rolling(10).min() * 100
    amp20 = (high.rolling(20).max() - low.rolling(20).min()) / low.rolling(20).min() * 100
    amp40 = (high.rolling(40).max() - low.rolling(40).min()) / low.rolling(40).min() * 100
    amount_ratio_5 = _rolling_ratio(amount, 5)
    amount_ratio_20 = _rolling_ratio(amount, 20)

    near_high_60 = close / high.rolling(60).max()
    near_high_250 = close / high.rolling(250).max()

    h10 = high.rolling(10).max().shift(1)
    h20 = high.rolling(20).max().shift(1)
    breakout_10 = (close > h10) & (amount_ratio_20 > 1.5)
    breakout_20 = (close > h20) & (amount_ratio_20 > 1.5)

    overlap_high = np.minimum(high.shift(1).to_numpy(), high.to_numpy())
    overlap_low = np.maximum(low.shift(1).to_numpy(), low.to_numpy())
    overlap_pct = pd.DataFrame(
        np.maximum(overlap_high - overlap_low, 0)
        / shifted_close.replace(0, np.nan).to_numpy() * 100,
        index=close.index,
        columns=close.columns,
    )

    result = {
        "close": close,
        "high": high,
        "low": low,
        "amount": amount,
        "vol": amount,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma50": ma50,
        "ma150": ma150,
        "ma200": ma200,
        "mom1": mom1,
        "mom5": mom5,
        "mom10": mom10,
        "mom20": mom20,
        "mom60": mom60,
        "amp5": amp5,
        "amp10": amp10,
        "amp20": amp20,
        "amp40": amp40,
        "amount_ratio_5": amount_ratio_5,
        "amount_ratio_20": amount_ratio_20,
        "vol_ratio_5": amount_ratio_5,
        "vol_ratio_20": amount_ratio_20,
        "near_high_60": near_high_60,
        "near_high_250": near_high_250,
        "breakout_10": breakout_10,
        "breakout_20": breakout_20,
        "overlap_pct": overlap_pct,
    }
    if needs_liquidity:
        open_price = _pivot(frame, "开盘")
        volume = _pivot(frame, "成交量") if "成交量" in frame else _empty_like(close)
        turnover = (
            _pivot(frame, "换手率%") if "换手率%" in frame else _empty_like(close)
        )
        source_pre_close = (
            _pivot(frame, "前收") if "前收" in frame else _empty_like(close)
        )
        pre_close = source_pre_close.combine_first(shifted_close)
        result.update({
            "open": open_price,
            "pre_close": pre_close,
            "volume": volume,
            "turnover_rate": turnover,
            "day_amp_pct": (high - low) / pre_close.replace(0, np.nan) * 100,
            "close_position_pct": (
                (close - low) / (high - low).replace(0, np.nan) * 100
            ),
            "gap_pct": (open_price / pre_close.replace(0, np.nan) - 1) * 100,
            "amount_ma5": amount.rolling(5).mean(),
            "amount_ma20": amount.rolling(20).mean(),
            "volume_ma5": volume.rolling(5).mean(),
            "volume_ma20": volume.rolling(20).mean(),
            "volume_ratio_5": _rolling_ratio(volume, 5),
            "volume_ratio_20": _rolling_ratio(volume, 20),
            "turnover_ma20": turnover.rolling(20).mean(),
            "vwap": amount / volume.replace(0, np.nan),
        })
    if any(key in selected_keys for key in RISK_FEATURES):
        true_range = pd.DataFrame(
            np.maximum.reduce([
                (high - low).to_numpy(),
                (high - pre_close).abs().to_numpy(),
                (low - pre_close).abs().to_numpy(),
            ]),
            index=close.index,
            columns=close.columns,
        )
        daily_return = close.pct_change(fill_method=None)
        downside = daily_return.where(daily_return < 0, 0)
        result.update({
            "atr14_pct": true_range.rolling(14).mean()
            / close.replace(0, np.nan) * 100,
            "volatility_20_ann_pct": daily_return.rolling(20).std()
            * np.sqrt(252) * 100,
            "downside_vol_20_ann_pct": downside.rolling(20).std()
            * np.sqrt(252) * 100,
            "drawdown_from_high_60_pct": (
                close / close.rolling(60).max().replace(0, np.nan) - 1
            ) * 100,
            "amihud_20": (
                daily_return.abs() / (amount / 1e8).replace(0, np.nan)
            ).rolling(20).mean(),
            "market_relative_mom20": mom20.sub(mom20.median(axis=1), axis=0),
            "market_relative_mom60": mom60.sub(mom60.median(axis=1), axis=0),
        })
    result = {key: result[key] for key in selected_keys}
    store.save(result, signature)
    return result
