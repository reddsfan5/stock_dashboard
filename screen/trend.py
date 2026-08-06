# ---- 管线自注册 ----
PIPELINE_META = {"id": "trend", "title": "连续涨跌", "kwargs": {"days": 5},
                "variants": [
                    {"id": "trend-up",  "title": "连续上涨(5日)", "kwargs": {"direction": "up"}},
                    {"id": "trend-down","title": "连续下跌(5日)", "kwargs": {"direction": "down"}},
                ]}

"""
趋势选股 — 基于本地缓存的连续涨跌分析

支持：
  - 连续 N 天上涨 / 下跌
  - 总涨幅 / 跌幅限制
  - 单日涨停过滤
  - 成交额过滤

使用示例
--------
# 命令行
$ python stock_trend.py                                # 默认：连续5天上涨，总涨幅<20%
$ python stock_trend.py --direction down               # 连续5天下跌
$ python stock_trend.py --days 3 --max-gain 10          # 连续3天涨，总涨幅<10%
$ python stock_trend.py --days 7 --no-filter-limit      # 连续7天涨，不过滤涨停
$ python stock_trend.py --direction down --min-amount 10000  # 连续下跌+日成交>1亿

# 代码调用
>>> from data.kline import StockData
>>> from stock_trend import find_consecutive

>>> data = StockData()
>>> data.update()
>>> result = find_consecutive(data, days=5, direction="up", max_gain=20)
>>> print(f"连续上涨: {len(result)} 只")
>>> print(result[["累计%", "名称"]].head())

依赖：stock_data.py（数据层）
"""

import argparse
import pandas as pd
from data.kline import StockData


# ======================
# 参数
# ======================

DEFAULT_DAYS = 5
MAX_GAIN_PCT = 20      # 总涨幅上限（%），过滤连板妖股
MIN_AMOUNT = 5000      # 万元，日均成交额下限
LIMIT_UP = 9.8         # 单日涨幅超过此值视为涨停（保留两位小数判断）


# ======================
# 核心逻辑
# ======================

# ======================
# 管线接口
# ======================

def find_all(data: StockData, **kwargs) -> pd.DataFrame:
    """统一接口"""
    return find_consecutive(
        data, days=kwargs.get("days", DEFAULT_DAYS),
        direction=kwargs.get("direction", "up"),
        max_gain=kwargs.get("max_gain", MAX_GAIN_PCT),
        min_amount=kwargs.get("min_amount", MIN_AMOUNT),
        filter_limit_up=kwargs.get("filter_limit_up", True),
    )


def find_consecutive(
    data: StockData,
    days: int = DEFAULT_DAYS,
    direction: str = "up",
    max_gain: float = MAX_GAIN_PCT,
    min_amount: float = MIN_AMOUNT,
    filter_limit_up: bool = True,
) -> pd.DataFrame:
    """
    从缓存中筛选连续 N 天同向变动的股票

    Args:
        data: StockData 实例
        days: 连续天数
        direction: 'up'=连续上涨, 'down'=连续下跌
        max_gain: 累计涨跌幅上限（%），过滤极端行情
        min_amount: 日均成交额下限（万元）
        filter_limit_up: 是否过滤单日涨跌幅>9.8%的股票

    Returns:
        DataFrame sorted by cumulative gain/loss
    """
    pivot = data.get_pivot(days=days, field="收盘").dropna()
    if len(pivot) == 0:
        return pd.DataFrame()

    ds = list(pivot.columns)
    ascending = direction == "up"

    # 连续上涨/下跌
    mask = pd.Series(True, index=pivot.index)
    for i in range(1, len(ds)):
        if ascending:
            mask &= pivot[ds[i]] > pivot[ds[i - 1]]
        else:
            mask &= pivot[ds[i]] < pivot[ds[i - 1]]

    result = pivot[mask].copy()

    # 每日涨跌幅
    for i in range(1, len(ds)):
        result[f"D{i}%"] = (
            (result[ds[i]] - result[ds[i - 1]]) / result[ds[i - 1]] * 100
        )

    # 累计涨跌幅
    result["累计%"] = (
        (result[ds[-1]] - result[ds[0]]) / result[ds[0]] * 100
    )

    # 过滤
    if ascending:
        result = result[result["累计%"] < max_gain]
    else:
        result = result[result["累计%"] > -max_gain]  # 跌幅不超过上限

    if filter_limit_up:
        for i in range(1, len(ds)):
            col = f"D{i}%"
            result = result[result[col].abs() < LIMIT_UP]

    # 成交额过滤
    snap = data.get_latest_snapshot()
    avg_amount = (
        data.cache[
            data.cache["日期"].isin(data.latest_dates[-days:])
        ]
        .groupby("代码")["成交额"]
        .mean()
        / 10000
    )
    result = result.join(avg_amount.rename("日均成交额(万)"), how="left")
    result = result[result["日均成交额(万)"] >= min_amount]

    # 排序
    result = result.sort_values("累计%", ascending=not ascending)

    # 加名称
    name_map = {c: data.get_stock_name(c) for c in result.index}
    result["名称"] = result.index.map(name_map)
    result = result.dropna(subset=["名称"])

    return result


# ======================
# CLI
# ======================

def main():
    from screen.engine import Screener, add_sort_args, apply_screen_args

    parser = argparse.ArgumentParser(description="趋势选股")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--direction", choices=["up", "down"], default="up")
    parser.add_argument("--max-gain", type=float, default=MAX_GAIN_PCT)
    parser.add_argument("--min-amount", type=float, default=MIN_AMOUNT)
    parser.add_argument("--no-filter-limit", action="store_true")
    parser.add_argument("--no-update", action="store_true")
    add_sort_args(parser)
    args = parser.parse_args()

    data = StockData()
    stocks = data.get_stock_list(board="main")
    if not args.no_update:
        data.update(stocks)

    result = find_all(
        data, days=args.days, direction=args.direction,
        max_gain=args.max_gain, min_amount=args.min_amount,
        filter_limit_up=not args.no_filter_limit,
    )

    if len(result) == 0:
        print("没有找到符合条件的股票")
        return

    # ---- 管道 ----
    result = result.reset_index()  # 代码从 index 变列
    screener = Screener(result)
    apply_screen_args(screener, args)
    if not args.sort:
        screener.sort("累计%", ascending=args.direction == "down")

    label = "连续上涨" if args.direction == "up" else "连续下跌"
    out_file = args.out or f"{'up' if args.direction == 'up' else 'down'}_stocks.csv"
    screener.to_csv(out_file)
    print(f"\n{label} {args.days} 天: {screener.count()} 只 → {out_file}")
    print_cols = args.print_cols.split(",") if args.print_cols else None
    screener.print(n=args.head, cols=print_cols)


if __name__ == "__main__":
    main()
