#!/usr/bin/env python3
"""策略9：ETF 缩量回调——前期上涨、近期缩量回踩。"""

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

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_09.html")


def run(capital=50_000, start_date="2022-01-01", hold_days=20, top_n=5,
        execution=None):
    _, cache, code_to_name = load_etf_universe()

    def selector(ctx):
        history = cache[
            (cache["日期"] <= ctx.signal_date)
            & (cache["日期"] >= ctx.signal_date - pd.Timedelta(days=50))
        ]
        pullbacks = {}
        for code, group in history.groupby("代码"):
            group = group.sort_values("日期")
            if len(group) < 20:
                continue
            close = group["收盘"].to_numpy()
            amount = group["成交额"].to_numpy()
            if close[-5] <= close[-10] * 1.03:
                continue
            if close[-1] >= close[-5] * 0.97 or close[-1] <= close[-5] * 0.88:
                continue
            if np.mean(amount[-3:]) >= np.mean(amount[-10:-5]) * 0.8:
                continue
            pullbacks[code] = (close[-1] / close[-5] - 1) * 100
        return sorted(pullbacks, key=pullbacks.get)

    return run_rebalance_report(
        cache=cache, selector=selector, capital=capital,
        start_date=start_date, hold_days=hold_days, top_n=top_n,
        execution=execution or ExecutionConfig(
            commission_rate=0.0003, stamp_tax_rate=0.0003,
            price_tick=0.001,
        ),
        code_to_name=code_to_name, output_html=OUTPUT_HTML,
        title="策略9：ETF缩量回调",
        subtitle=f"前期上涨+近期缩量回踩 · 每{hold_days}个交易日轮动{top_n}只",
        params_html=(
            "💡 <b>形态：</b>前段上涨超过3%，近5日回调3%～12%<br>"
            "💡 <b>量能：</b>近3日成交额低于前段均值80%<br>"
            f"💡 <b>组合：</b>回调较深者优先，等权{top_n}只，持有{hold_days}个交易日<br>"
            "💡 <b>时序：</b>前一交易日确认信号，下一交易日收盘执行"
        ),
        stats_title="ETF缩量回调买入",
        stats_extra=f"上涨后缩量回踩 | {hold_days}交易日×{top_n}只",
        board="etf", collect_klines=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--start", default="2022-01-01")
    add_execution_args(parser, default_tick=0.001)
    args = parser.parse_args()
    started = time.time()
    run(args.capital, args.start, args.hold, args.top, execution_from_args(args))
    print(f"耗时: {time.time() - started:.0f}秒")
