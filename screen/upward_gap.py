# ---- 管线自注册 ----
PIPELINE_META = {"id": "upward_gap", "title": "持续推高", "kwargs": {"days": 5, "min_gap_pct": 1.5}}

"""
持续推高选股 — 基于本地缓存

条件：
  1. 连续 N 天，每天最高价 > 前一日收盘价 × (1 + min_gap%)
  2. 近 N 天日均成交额 ≥ 5000 万
  3. 仅沪深主板（排除创业/科创/北交所/ST）

逻辑：
  每日收盘前检查：如果当天最高价已经比昨日收盘高出 1.5% 以上，
  说明盘中多头力量强劲，价格有向上推力。
  连续多天都满足 → 稳定的上升惯性，适合尾盘买入。

筛选出的标的：
  - 温和但持续的上升通道（每天都有溢价推力）
  - 区别于暴力拉升——每天只需 1.5%，不要求涨停
  - 尾盘确认信号后买入，次日获利了结

使用示例
--------
# 命令行
$ python scripts/screen.py --only upward_gap

# 代码调用
>>> from data.kline import StockData
>>> from screen.upward_gap import find_all
>>> data = StockData()
>>> result = find_all(data, days=5, min_gap_pct=1.5)
"""

import numpy as np
import pandas as pd
from typing import Dict, Optional, Any
from tqdm import tqdm

from data.kline import StockData

# ======================
# 参数
# ======================

DEFAULT_DAYS = 5
MIN_GAP_PCT = 1.5        # 每日最低溢价幅度%
MIN_AMOUNT = 5000        # 日均成交额下限（万元）
MAX_CONSECUTIVE_GAIN = 20  # N天总涨幅上限%，过滤妖股


# ======================
# 管线接口
# ======================

def find_all(data, **kwargs) -> pd.DataFrame:
    """统一管线接口"""
    return find_upward_gap(
        data,
        days=kwargs.get("days", DEFAULT_DAYS),
        min_gap_pct=kwargs.get("min_gap_pct", MIN_GAP_PCT),
        min_amount=kwargs.get("min_amount", MIN_AMOUNT),
        max_consecutive_gain=kwargs.get("max_consecutive_gain", MAX_CONSECUTIVE_GAIN),
    )


def find_upward_gap(
    data: StockData,
    days: int = DEFAULT_DAYS,
    min_gap_pct: float = MIN_GAP_PCT,
    min_amount: float = MIN_AMOUNT,
    max_consecutive_gain: float = MAX_CONSECUTIVE_GAIN,
) -> pd.DataFrame:
    """
    找出连续 N 天最高价 > 前收 × (1 + min_gap%) 的股票。

    Args:
        data: StockData 实例
        days: 连续天数
        min_gap_pct: 最低溢价幅度%
        min_amount: 日均成交额下限（万元）
        max_consecutive_gain: 连续期间总涨幅上限%

    Returns:
        DataFrame with columns: 代码, 名称, 连续天数, 平均溢价%, 累计涨幅%, ...
    """
    results = []
    cache = data.cache.copy()
    cache = cache.sort_values(["代码", "日期"])

    # 预计算前一日收盘
    cache["前收"] = cache.groupby("代码")["收盘"].shift(1)

    # 日均成交额
    avg_amount = (
        cache.groupby("代码")["成交额"]
        .rolling(days, min_periods=days).mean()
        .reset_index(level=0, drop=True)
    )
    cache["日均成交额"] = avg_amount / 10000  # 转为万元

    groups = cache.groupby("代码")
    desc = f"持续推高(N={days}, gap>{min_gap_pct}%)"

    for code, grp in tqdm(groups, desc=desc, unit="只"):
        if len(grp) < days + 2:
            continue

        highs = grp["最高"].values
        prev_close = grp["前收"].values
        closes = grp["收盘"].values
        amounts = grp["日均成交额"].values

        # 检查 daily: 最高 > 前收 × (1 + gap)
        threshold = 1 + min_gap_pct / 100
        ok = np.zeros(len(grp), dtype=bool)
        for i in range(1, len(grp)):
            if pd.notna(prev_close[i]) and prev_close[i] > 0:
                ok[i] = highs[i] > prev_close[i] * threshold

        # 找最近连续 days 天满足条件
        run = 0
        consecutive_start = -1
        for i in range(len(grp)):
            if ok[i]:
                run += 1
                if consecutive_start < 0:
                    consecutive_start = i
            else:
                run = 0
                consecutive_start = -1

        # 只看最近一次（最新交易日是否处于连续状态中）
        if run >= days:
            last_idx = len(grp) - 1
            start_idx = last_idx - run + 1

            # 成交额过滤
            if amounts[last_idx] < min_amount:
                continue

            # 总涨幅过滤（防妖股）
            if closes[start_idx] > 0:
                total_gain = (closes[last_idx] - closes[start_idx]) / closes[start_idx] * 100
                if total_gain > max_consecutive_gain:
                    continue
            else:
                continue

            # 平均溢价（每天 (high - prev_close) / prev_close）
            gaps = []
            for j in range(start_idx, last_idx + 1):
                if prev_close[j] > 0 and pd.notna(prev_close[j]):
                    gaps.append((highs[j] - prev_close[j]) / prev_close[j] * 100)
            avg_gap = float(np.mean(gaps)) if gaps else 0

            results.append({
                "代码": code,
                "名称": "",
                "连续天数": run,
                "平均溢价%": round(avg_gap, 2),
                "累计涨幅%": round(total_gain, 2),
                "最新价": closes[last_idx],
                "最近N日成交额(万)": round(amounts[last_idx], 0),
            })

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values(["连续天数", "平均溢价%"], ascending=[False, False])
    df = df.reset_index(drop=True)
    return df
