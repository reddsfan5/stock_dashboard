# ---- 管线自注册 ----
PIPELINE_META = {"id": "sideways", "title": "横盘震荡", "kwargs": {"days": 10}}

"""
横盘震荡选股 — 基于本地缓存

判断维度（满分100分）：
  1. 振幅 40%    — 越小越好（价格波动收窄）
  2. 趋势平坦度 25% — 线性斜率越平越好（无方向）
  3. 重叠率 25%    — K线重叠越高越好（筹码交换）
  4. 区间位置 10%  — 当前价在区间中部

使用示例
--------
# 命令行
$ python stock_sideways.py --no-update                        # 默认：10日，振幅<15%
$ python stock_sideways.py --days 15 --max-amp 10 --no-update # 15日，振幅<10%
$ python stock_sideways.py --max-amp 8 --r2-max 0.1 --no-update  # 严格版
$ python stock_sideways.py --max-amp 20 --r2-max 0 --no-update    # 宽松版
  # 去掉 --no-update 会先拉取最新数据再分析

# 代码调用
>>> from stock_data import StockData
>>> from stock_sideways import find_sideways

>>> data = StockData()
>>> data.update()
>>> result = find_sideways(data, days=10)
>>> print(f"横盘震荡: {len(result)} 只")
>>> print(result[["名称", "振幅%", "综合评分"]].head(10))

选出的标的特别适合做网格交易或期权卖方策略。

依赖：stock_data.py（数据层）
"""

import argparse
import numpy as np
import pandas as pd
from typing import Dict, Optional, Any
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

from stock_data import StockData


# ======================
# 参数
# ======================

DAYS = 10               # 回看天数
MAX_AMPLITUDE = 15      # 最大振幅（%）
MIN_AMOUNT = 5000       # 最小日均成交额（万元）
MAX_SLOPE = 0.5         # 线性趋势斜率绝对值上限（%），越小越平
MIN_R_SQUARED = 0.3     # R² 最大值（越小趋势越不明显，设 0 则不过滤）
POSITION_RANGE = (25, 75)  # 当前价在区间中的位置范围（%），中间区域
MIN_OVERLAP = 30        # 最小平均重叠率（%），越高越震荡
THREADS = 12


# ======================
# 核心指标
# ======================

def calc_trend_flatness(prices: np.ndarray) -> tuple:
    """
    用线性回归衡量趋势平坦度

    Returns:
        (slope_pct, r_squared)
        slope_pct: 每日涨跌幅%（越小越平）
        r_squared: 拟合优度（越接近0越无趋势）
    """
    x = np.arange(len(prices))
    y = prices

    # y = a*x + b
    n = len(x)
    a = (n * np.sum(x * y) - np.sum(x) * np.sum(y)) / (n * np.sum(x * x) - np.sum(x) ** 2 + 1e-9)
    b = np.mean(y) - a * np.mean(x)

    # R²
    y_pred = a * x + b
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / (ss_tot + 1e-9)

    # 斜率转百分比（相对均价）
    slope_pct = a / np.mean(y) * 100

    return slope_pct, r2


def calc_consolidation_score(df: pd.DataFrame) -> Dict[str, Any]:
    """
    计算横盘震荡综合评分

    Returns:
        dict with keys: 振幅%, 区间位置%, 趋势斜率%, R², 重叠率%, 日均成交额(万), 综合评分
    """
    highs = df["最高"].values
    lows = df["最低"].values
    closes = df["收盘"].values
    amounts = df["成交额"].values

    high = highs.max()
    low = lows.min()
    close = closes[-1]

    # 1. 振幅
    amplitude = (high - low) / low * 100

    # 2. 区间位置
    position = (close - low) / (high - low) * 100 if high != low else 50

    # 3. 趋势平坦度
    slope_pct, r2 = calc_trend_flatness(closes)

    # 4. 平均重叠率（相邻日 K 线重叠率均值）
    overlap_rates = []
    for i in range(1, len(df)):
        h1, l1 = highs[i - 1], lows[i - 1]
        h2, l2 = highs[i], lows[i]

        oh = min(h1, h2)
        ol = max(l1, l2)
        uh = max(h1, h2)
        ul = min(l1, l2)

        denom = uh - ul
        overlap_rates.append(1.0 if denom == 0 else (oh - ol) / denom)
    avg_overlap = np.mean(overlap_rates) * 100

    # 5. 日均成交额（万元）
    avg_amount = amounts.mean() / 10000

    # 6. 综合评分（0-100，越高越震荡）
    #   振幅越小越好、趋势越平越好、重叠率越高越好
    amp_score = max(0, 1 - amplitude / MAX_AMPLITUDE) * 40      # 权重 40%
    trend_score = max(0, 1 - abs(slope_pct) / MAX_SLOPE) * 25   # 权重 25%
    overlap_score = min(avg_overlap / 80, 1) * 25               # 权重 25%
    pos_score = (1 - abs(position - 50) / 50) * 10              # 权重 10%
    total_score = amp_score + trend_score + overlap_score + pos_score

    return {
        "最新价": round(close, 2),
        "振幅%": round(amplitude, 2),
        "区间位置%": round(position, 1),
        "趋势斜率%": round(slope_pct, 3),
        "R²": round(r2, 3),
        "平均重叠率%": round(avg_overlap, 2),
        "日均成交额(万)": round(avg_amount, 0),
        "综合评分": round(total_score, 1),
    }


# ======================
# 筛选
# ======================

def analyze_sideways(code: str, data: StockData, days: int) -> Optional[Dict[str, Any]]:
    """分析单只股票是否为横盘震荡"""
    df = data.get_kline(code, days=days)
    if len(df) < days:
        return None

    metrics = calc_consolidation_score(df)

    # 硬性过滤
    if metrics["振幅%"] > MAX_AMPLITUDE:
        return None
    if metrics["日均成交额(万)"] < MIN_AMOUNT:
        return None
    if not (POSITION_RANGE[0] <= metrics["区间位置%"] <= POSITION_RANGE[1]):
        return None
    if abs(metrics["趋势斜率%"]) >= MAX_SLOPE:
        return None
    if metrics["平均重叠率%"] < MIN_OVERLAP:
        return None
    if MIN_R_SQUARED > 0 and metrics["R²"] > MIN_R_SQUARED:
        return None  # 趋势太明显，不是横盘

    return {
        "代码": code,
        "名称": data.get_stock_name(code),
        **metrics,
    }


# ======================
# 管线接口
# ======================

def find_all(data: StockData, **kwargs) -> pd.DataFrame:
    """统一接口。kwargs: days, max_amp, min_amount, max_slope, min_overlap, r2_max"""
    global MAX_AMPLITUDE, MIN_AMOUNT, MAX_SLOPE, MIN_OVERLAP, MIN_R_SQUARED
    MAX_AMPLITUDE = kwargs.get("max_amp", MAX_AMPLITUDE)
    MIN_AMOUNT = kwargs.get("min_amount", MIN_AMOUNT)
    MAX_SLOPE = kwargs.get("max_slope", MAX_SLOPE)
    MIN_OVERLAP = kwargs.get("min_overlap", MIN_OVERLAP)
    MIN_R_SQUARED = kwargs.get("r2_max", MIN_R_SQUARED)
    return find_sideways(data, days=kwargs.get("days", DAYS))


def find_sideways(data: StockData, days: int = DAYS) -> pd.DataFrame:
    """
    找出横盘震荡的股票

    Returns:
        DataFrame (未排序，交给 Screener)
    """
    stocks = data.get_stock_list(board="main")
    results = []

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        tasks = {
            executor.submit(analyze_sideways, row["代码"], data, days): row["代码"]
            for _, row in stocks.iterrows()
        }
        for future in tqdm(as_completed(tasks), total=len(tasks),
                          desc="筛选横盘"):
            try:
                r = future.result()
                if r:
                    results.append(r)
            except Exception:
                pass

    if not results:
        return pd.DataFrame()

    return pd.DataFrame(results)


# ======================
# CLI
# ======================

def main():
    from stock_screen import Screener, add_sort_args, apply_screen_args

    parser = argparse.ArgumentParser(description="横盘震荡选股")
    parser.add_argument("--days", type=int, default=DAYS)
    parser.add_argument("--max-amp", type=float, default=MAX_AMPLITUDE)
    parser.add_argument("--min-amount", type=float, default=MIN_AMOUNT)
    parser.add_argument("--max-slope", type=float, default=MAX_SLOPE)
    parser.add_argument("--min-overlap", type=float, default=MIN_OVERLAP)
    parser.add_argument("--r2-max", type=float, default=MIN_R_SQUARED)
    parser.add_argument("--no-update", action="store_true")
    add_sort_args(parser)
    args = parser.parse_args()

    # 覆写全局参数
    globals()["MAX_AMPLITUDE"] = args.max_amp
    globals()["MIN_AMOUNT"] = args.min_amount
    globals()["MAX_SLOPE"] = args.max_slope
    globals()["MIN_OVERLAP"] = args.min_overlap
    globals()["MIN_R_SQUARED"] = args.r2_max

    # 数据准备
    data = StockData()
    if not args.no_update:
        data.update(data.get_stock_list(board="main"))

    # 筛选
    result = find_all(data, days=args.days)
    if len(result) == 0:
        print("没有找到符合条件的股票")
        return

    # ---- 管道：关联行业 → 过滤 → 排序 → 输出 ----
    screener = Screener(result)
    apply_screen_args(screener, args)
    if not args.sort:
        screener.sort("综合评分", ascending=False)

    out_file = args.out or "sideways_stocks.csv"
    screener.to_csv(out_file)

    print(f"\n横盘震荡 {args.days} 日: {screener.count()} 只 → {out_file}")
    print_cols = args.print_cols.split(",") if args.print_cols else None
    screener.print(n=args.head, cols=print_cols)


if __name__ == "__main__":
    main()
