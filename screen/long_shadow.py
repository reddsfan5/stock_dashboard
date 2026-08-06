# ---- 管线自注册 ----
PIPELINE_META = {"id": "long_shadow", "title": "长下影线(10日)", "kwargs": {"days": 10, "min_ratio": 0.4, "min_count": 5}}

"""
长下影线频现选股 — 近N日半数以上收出长下影线

定义：下影线 / 总振幅 > min_ratio（默认40%），说明盘中曾有显著卖压但被买回。
连续多日出现 → 下方承接力量持续存在，可能是底部吸筹信号。

区别于 hammer（金针探底）：本策略不要求形态极端（锤子线要求下影≥实体×3），
只要下影线占比偏高即可，更关注「频次」而非「单日极端程度」。

使用示例
--------
$ python scripts/screen.py --only long_shadow
"""

import numpy as np
import pandas as pd
from tqdm import tqdm
from data.kline import StockData

# ======================
# 参数
# ======================

DEFAULT_DAYS = 10       # 回看天数
MIN_RATIO = 0.4         # 下影线/总振幅 最低比例
MIN_COUNT = 5           # 至少 N 天满足
MIN_AMOUNT = 5000       # 日均成交额下限（万元）


# ======================
# 管线接口
# ======================

def find_all(data, **kwargs) -> pd.DataFrame:
    return find_long_shadow(
        data,
        days=kwargs.get("days", DEFAULT_DAYS),
        min_ratio=kwargs.get("min_ratio", MIN_RATIO),
        min_count=kwargs.get("min_count", MIN_COUNT),
        min_amount=kwargs.get("min_amount", MIN_AMOUNT),
    )


def find_long_shadow(
    data: StockData,
    days: int = DEFAULT_DAYS,
    min_ratio: float = MIN_RATIO,
    min_count: int = MIN_COUNT,
    min_amount: float = MIN_AMOUNT,
) -> pd.DataFrame:
    """
    找出近 N 天有半数以上收长下影线的标的。

    Args:
        data: StockData 或 CombinedData
        days: 回看天数
        min_ratio: 下影线/总振幅 最低比例
        min_count: 至少满足天数
        min_amount: 日均成交额下限（万元）

    Returns:
        DataFrame with columns: 代码, 名称, 长下影天数, 平均下影比, 最新价, ...
    """
    cache = data.cache.copy()
    cache = cache.sort_values(["代码", "日期"])

    all_dates = sorted(cache["日期"].unique())
    if len(all_dates) < days:
        return pd.DataFrame()

    recent_dates = all_dates[-days:]
    recent = cache[cache["日期"].isin(recent_dates)]

    results = []
    for code, grp in tqdm(recent.groupby("代码"), desc="长下影线", unit="只"):
        if len(grp) < max(days // 2, 3):
            continue

        grp = grp.sort_values("日期").tail(days)
        if len(grp) < days:
            continue

        opens = grp["开盘"].values
        closes = grp["收盘"].values
        highs = grp["最高"].values
        lows = grp["最低"].values
        amounts = grp["成交额"].values

        # 每根K线的下影线比例
        lower_shadows = np.minimum(opens, closes) - lows
        total_ranges = highs - lows
        ratios = np.where(total_ranges > 0, lower_shadows / total_ranges, 0)

        # 计数: 下影线占比 > min_ratio 的天数
        shadow_days = int(np.sum(ratios >= min_ratio))
        if shadow_days < min_count:
            continue

        # 成交额过滤
        avg_amount = np.mean(amounts) / 10000
        if avg_amount < min_amount:
            continue

        avg_ratio = float(np.mean(ratios))
        last_close = closes[-1]

        results.append({
            "代码": code,
            "长下影天数": shadow_days,
            "平均下影比": round(avg_ratio, 3),
            "最新价": round(last_close, 3) if last_close > 1 else last_close,
            "近10日成交额(万)": round(avg_amount, 0),
        })

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values(["长下影天数", "平均下影比"], ascending=[False, False])
    df = df.reset_index(drop=True)
    return df
