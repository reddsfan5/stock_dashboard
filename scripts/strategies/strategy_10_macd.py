#!/usr/bin/env python3
"""策略10：ETF MACD 金叉并放量。"""

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

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_10.html")


def run(capital=50_000, start_date="2022-01-01", hold_days=20, top_n=5,
        execution=None):
    _, cache, names = load_etf_universe()

    def selector(ctx):
        history = cache[
            (cache["日期"] <= ctx.signal_date)
            & (cache["日期"] >= ctx.signal_date - pd.Timedelta(days=80))
        ]
        scores = {}
        for code, group in history.groupby("代码"):
            group = group.sort_values("日期")
            if len(group) < 35:
                continue
            close = group["收盘"].to_numpy()
            amount = group["成交额"].to_numpy()
            ema12 = pd.Series(close).ewm(span=12).mean().to_numpy()
            ema26 = pd.Series(close).ewm(span=26).mean().to_numpy()
            dif = ema12 - ema26
            dea = pd.Series(dif).ewm(span=9).mean().to_numpy()
            if not (dif[-2] <= dea[-2] and dif[-1] > dea[-1]):
                continue
            if amount[-1] <= np.mean(amount[-6:-1]) * 1.2:
                continue
            scores[code] = (close[-1] / close[-20] - 1) * 100
        return sorted(scores, key=scores.get, reverse=True)

    return run_rebalance_report(
        cache=cache, selector=selector, capital=capital,
        start_date=start_date, hold_days=hold_days, top_n=top_n,
        execution=execution or ExecutionConfig(
            commission_rate=0.0003, stamp_tax_rate=0.0003,
            price_tick=0.001,
        ),
        code_to_name=names, output_html=OUTPUT_HTML,
        title="策略10：ETF MACD金叉+放量",
        subtitle=f"DIF上穿DEA且成交额放大 · {hold_days}交易日轮动{top_n}只",
        params_html=(
            "💡 <b>信号：</b>DIF由下向上穿越DEA，成交额>前5日均值1.2倍<br>"
            "💡 <b>排序：</b>近20日动量由高到低<br>"
            f"💡 <b>组合：</b>等权{top_n}只，持有{hold_days}个交易日<br>"
            "💡 <b>时序：</b>前一交易日确认，下一交易日成交"
        ),
        stats_title="ETF MACD金叉+放量",
        stats_extra=f"DIF上穿DEA+放量 | {hold_days}交易日×{top_n}只",
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
