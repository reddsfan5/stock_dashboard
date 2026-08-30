#!/usr/bin/env python3
"""策略11：ETF MA5/MA20 均线金叉。"""

import argparse
import os
import sys
import time

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from backtest.execution import ExecutionConfig
from scripts.strategies.rebalance_utils import (
    add_execution_args, execution_from_args, load_etf_universe,
    run_rebalance_report,
)

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_11.html")


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
            if len(group) < 25:
                continue
            close = group["收盘"].to_numpy()
            ma5 = pd.Series(close).rolling(5).mean().to_numpy()
            ma20 = pd.Series(close).rolling(20).mean().to_numpy()
            if not (ma5[-2] <= ma20[-2] and ma5[-1] > ma20[-1]):
                continue
            if close[-1] <= ma5[-1]:
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
        title="策略11：ETF MA5/MA20金叉",
        subtitle=f"MA5上穿MA20且价格站上MA5 · {hold_days}交易日轮动{top_n}只",
        params_html=(
            "💡 <b>信号：</b>MA5由下向上穿越MA20，收盘价位于MA5上方<br>"
            "💡 <b>排序：</b>近20日动量由高到低<br>"
            f"💡 <b>组合：</b>等权{top_n}只，持有{hold_days}个交易日<br>"
            "💡 <b>时序：</b>前一交易日确认，下一交易日成交"
        ),
        stats_title="ETF MA5/MA20金叉",
        stats_extra=f"均线金叉+价格确认 | {hold_days}交易日×{top_n}只",
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
