#!/usr/bin/env python3
"""策略8：ETF 动量轮动——持有近20日涨幅靠前的ETF。"""

import argparse
import os
import sys
import time

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from backtest.execution import ExecutionConfig
from scripts.strategies.rebalance_utils import (
    add_execution_args, execution_from_args, export_result_csv,
    load_etf_universe, run_rebalance_report,
)

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "etf_momentum.html")


def run(capital=50_000, start_date="2022-01-01", hold_days=30, top_n=5,
        execution=None):
    _, cache, code_to_name = load_etf_universe()

    def selector(ctx):
        history = cache[
            (cache["日期"] <= ctx.signal_date)
            & (cache["日期"] >= ctx.signal_date - pd.Timedelta(days=45))
        ]
        momentum = {}
        for code, group in history.groupby("代码"):
            group = group.sort_values("日期")
            if len(group) < 15:
                continue
            close = group["收盘"].to_numpy()
            if close[0] <= 0 or close[-1] <= 0:
                continue
            value = (close[-1] / close[0] - 1) * 100
            if -20 < value < 40:
                momentum[code] = value
        return sorted(momentum, key=momentum.get, reverse=True)

    result = run_rebalance_report(
        cache=cache, selector=selector, capital=capital,
        start_date=start_date, hold_days=hold_days, top_n=top_n,
        execution=execution or ExecutionConfig(
            commission_rate=0.0003, stamp_tax_rate=0.0003,
            price_tick=0.001,
        ),
        code_to_name=code_to_name, output_html=OUTPUT_HTML,
        title="策略8：ETF动量轮动",
        subtitle=f"前一日近20日动量排名 · 每{hold_days}个交易日等权轮动{top_n}只",
        params_html=(
            "💡 <b>信号：</b>调仓日前一交易日计算近20日动量，过滤极端涨跌<br>"
            f"💡 <b>组合：</b>排名前{top_n}只ETF等权，持有{hold_days}个交易日<br>"
            "💡 <b>退出：</b>调仓日换仓，不设置日内止盈止损<br>"
            "💡 <b>评估：</b>逐日盯市并计入费用、滑点和可选成交容量"
        ),
        stats_title=f"ETF动量轮动 ({hold_days}交易日×{top_n}只)",
        stats_extra="近20日涨幅排名 | 前一日信号 | 等权买入",
        board="etf", collect_klines=True,
    )
    export_result_csv(
        result,
        os.path.join(PROJECT_DIR, "output", "etf_momentum_trades.csv"),
        os.path.join(PROJECT_DIR, "output", "etf_momentum_equity.csv"),
    )
    print("✓ CSV: output/etf_momentum_trades.csv / etf_momentum_equity.csv")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETF动量轮动策略")
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--start", default="2022-01-01")
    add_execution_args(parser, default_tick=0.001)
    args = parser.parse_args()
    started = time.time()
    run(args.capital, args.start, args.hold, args.top, execution_from_args(args))
    print(f"总耗时: {time.time() - started:.0f}秒")
