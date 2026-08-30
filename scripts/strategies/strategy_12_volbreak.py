#!/usr/bin/env python3
"""策略12：ETF 放量突破20日高点。"""

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from backtest.execution import ExecutionConfig
from scripts.strategies.rebalance_utils import (
    add_execution_args, execution_from_args, load_etf_universe,
    run_rebalance_report,
)

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_12.html")


def run(capital=50_000, start_date="2022-01-01", hold_days=20, top_n=5,
        execution=None):
    _, cache, names = load_etf_universe()

    def selector(ctx):
        history = cache[
            (cache["日期"] <= ctx.signal_date)
            & (cache["日期"] >= ctx.signal_date - pd.Timedelta(days=60))
        ]
        scores = {}
        for code, group in history.groupby("代码"):
            group = group.sort_values("日期")
            if len(group) < 25:
                continue
            close = group["收盘"].to_numpy()
            high = group["最高"].to_numpy()
            amount = group["成交额"].to_numpy()
            high20 = np.max(high[-21:-1])
            avg_amount20 = np.mean(amount[-21:-1])
            if close[-1] <= high20 * 1.005:
                continue
            if avg_amount20 <= 0 or amount[-1] <= avg_amount20 * 1.5:
                continue
            scores[code] = amount[-1] / avg_amount20
        return sorted(scores, key=scores.get, reverse=True)

    return run_rebalance_report(
        cache=cache, selector=selector, capital=capital,
        start_date=start_date, hold_days=hold_days, top_n=top_n,
        execution=execution or ExecutionConfig(
            commission_rate=0.0003, stamp_tax_rate=0.0003,
            price_tick=0.001,
        ),
        code_to_name=names, output_html=OUTPUT_HTML,
        title="策略12：ETF放量突破20日高点",
        subtitle=f"收盘突破前高且成交额>20日均值1.5倍 · {hold_days}日轮动",
        params_html=(
            "💡 <b>信号：</b>收盘突破此前20日最高价0.5%以上<br>"
            "💡 <b>量能：</b>当日成交额>此前20日均值1.5倍<br>"
            f"💡 <b>组合：</b>按放量倍数排序，等权{top_n}只，持有{hold_days}个交易日<br>"
            "💡 <b>时序：</b>前一交易日确认突破，下一交易日成交"
        ),
        stats_title="ETF放量突破前高",
        stats_extra=f"20日突破+1.5倍成交额 | {hold_days}交易日×{top_n}只",
        board="etf",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--top", type=int, default=5)
    add_execution_args(parser, default_tick=0.001)
    args = parser.parse_args()
    started = time.time()
    run(args.capital, args.start, args.hold, args.top, execution_from_args(args))
    print(f"耗时: {time.time() - started:.0f}秒")
