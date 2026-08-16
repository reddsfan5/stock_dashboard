"""
向量化指标计算 — 基于透视表一次性算出全市场所有滚动指标

比逐只扫描快 50~100 倍。支持磁盘缓存，首次计算后后续秒级加载。
"""

import os
import numpy as np
import pandas as pd

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")


def _load_cache() -> dict:
    """从磁盘加载缓存的指标透视表"""
    # 检查第一个关键文件是否存在
    first = os.path.join(_CACHE_DIR, "indicators_close.parquet")
    if not os.path.exists(first):
        return None
    result = {}
    for key in ["close", "high", "low", "vol",
                "ma5", "ma10", "ma20", "ma50", "ma150", "ma200",
                "mom1", "mom5", "mom10", "mom20", "mom60",
                "amp5", "amp10", "amp20", "amp40",
                "vol_ratio_5", "vol_ratio_20",
                "near_high_60", "near_high_250",
                "breakout_10", "breakout_20", "overlap_pct"]:
        p = os.path.join(_CACHE_DIR, f"indicators_{key}.parquet")
        if os.path.exists(p):
            result[key] = pd.read_parquet(p)
    return result if len(result) > 0 else None


def _save_cache(ind: dict):
    """保存指标透视表到磁盘"""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    for key, df in ind.items():
        if isinstance(df, pd.DataFrame):
            df.to_parquet(os.path.join(_CACHE_DIR, f"indicators_{key}.parquet"), index=True)


def compute_all(cache: pd.DataFrame, board_prefix=None, use_cache=True) -> dict:
    """
    一次性计算全市场所有常用指标。use_cache=True 时首次计算后下次秒级加载。
    """
    # 尝试加载缓存
    if use_cache:
        cached = _load_cache()
        if cached is not None:
            return cached
    """
    一次性计算全市场所有常用指标。

    Args:
        cache: 股票K线缓存 (代码,日期,开盘,最高,最低,收盘,成交额)
        board_prefix: 代码前缀过滤，如 ("sh600","sz000")

    Returns:
        dict with keys: close, high, low, vol (透视表),
                        ma5, ma10, ma20, ma50, ma150, ma200,
                        mom1, mom5, mom10, mom20, mom60,
                        amp5, amp10, amp20, amp40,
                        vol_ratio, near_high_60, near_high_250,
                        breakout_10, breakout_20,
                        overlap_pct (每日重叠区%)
    """
    # 板块过滤
    if board_prefix:
        cache = cache[cache["代码"].str.startswith(board_prefix)]

    # 透视表
    close = cache.pivot_table(index="日期", columns="代码", values="收盘", aggfunc="last")
    high = cache.pivot_table(index="日期", columns="代码", values="最高", aggfunc="last")
    low = cache.pivot_table(index="日期", columns="代码", values="最低", aggfunc="last")
    vol = cache.pivot_table(index="日期", columns="代码", values="成交额", aggfunc="last")

    # 均线
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()

    # 区间涨跌幅（向量化！）
    mom1 = close.pct_change(1, fill_method=None) * 100
    mom5 = close.pct_change(5, fill_method=None) * 100
    mom10 = close.pct_change(10, fill_method=None) * 100
    mom20 = close.pct_change(20, fill_method=None) * 100
    mom60 = close.pct_change(60, fill_method=None) * 100

    # 振幅
    amp5 = (high.rolling(5).max() - low.rolling(5).min()) / low.rolling(5).min() * 100
    amp10 = (high.rolling(10).max() - low.rolling(10).min()) / low.rolling(10).min() * 100
    amp20 = (high.rolling(20).max() - low.rolling(20).min()) / low.rolling(20).min() * 100
    amp40 = (high.rolling(40).max() - low.rolling(40).min()) / low.rolling(40).min() * 100

    # 成交量
    vol_ma5 = vol.rolling(5).mean()
    vol_ma20 = vol.rolling(20).mean()
    vol_ratio_5 = vol / vol_ma5   # 相对5日均量
    vol_ratio_20 = vol / vol_ma20  # 相对20日均量

    # 距N日高点距离
    near_high_60 = close / high.rolling(60).max()
    near_high_250 = close / high.rolling(250).max()

    # 突破信号
    h10 = high.rolling(10).max().shift(1)
    breakout_10 = (close > h10) & (vol_ratio_20 > 1.5)
    h20 = high.rolling(20).max().shift(1)
    breakout_20 = (close > h20) & (vol_ratio_20 > 1.5)

    # K线重叠区% (每日与前日的重叠)
    overlap = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    # 简化: 用最高最低算
    oh = np.minimum(high.shift(1).values, high.values)
    ol = np.maximum(low.shift(1).values, low.values)
    prev_close = close.shift(1).values
    overlap_pct = pd.DataFrame(
        (oh - ol) / prev_close * 100,
        index=close.index, columns=close.columns
    )

    result = {
        "close": close, "high": high, "low": low, "vol": vol,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma50": ma50,
        "ma150": ma150, "ma200": ma200,
        "mom1": mom1, "mom5": mom5, "mom10": mom10,
        "mom20": mom20, "mom60": mom60,
        "amp5": amp5, "amp10": amp10, "amp20": amp20, "amp40": amp40,
        "vol_ratio_5": vol_ratio_5, "vol_ratio_20": vol_ratio_20,
        "near_high_60": near_high_60, "near_high_250": near_high_250,
        "breakout_10": breakout_10, "breakout_20": breakout_20,
        "overlap_pct": overlap_pct,
    }
    # 存缓存（下次秒级加载）
    _save_cache(result)
    return result


def get_signals_at(ind: dict, date_idx: int) -> pd.Series:
    """
    获取指定日期索引的所有股票的某个指标值。
    配合 compute_all() 返回的透视表使用。
    """
    return ind.iloc[date_idx]


def scan_consecutive_signal(ind: dict, condition_col: str,
                             min_days: int = 3, max_days: int = 10,
                             start_date: str = None) -> pd.DataFrame:
    """
    扫描连续满足条件的信号。

    返回 DataFrame: 代码, 触发日(条件结束后的第2天), 连续天数
    """
    cond = ind[condition_col]  # bool DataFrame
    all_codes = cond.columns
    all_dates = cond.index

    start_idx = 0
    if start_date:
        start_idx = max(0, (all_dates >= pd.Timestamp(start_date)).argmax())

    signals = []
    for code in all_codes:
        arr = cond[code].values[start_idx:].astype(bool)
        n = len(arr)
        run = 0
        for i in range(n):
            if arr[i]:
                run += 1
            else:
                if run >= min_days:
                    streak = min(run, max_days)
                    trigger = start_idx + i + 2  # 条件结束后 + 2 天
                    if trigger < len(all_dates):
                        signals.append({
                            "代码": code,
                            "触发日": all_dates[trigger],
                            "连续天数": streak,
                        })
                run = 0
        if run >= min_days:  # 末尾
            streak = min(run, max_days)
            trigger = start_idx + n  # 超出范围，跳过
    return pd.DataFrame(signals)
