#!/usr/bin/env python3
"""策略7：月度多因子选股——振幅、动量、成交额和强势度综合排名。"""

import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from backtest.execution import ExecutionConfig
from data.kline import StockData
from scripts.strategies.rebalance_utils import (
    add_execution_args, execution_from_args, run_rebalance_report,
)

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_07.html")
MAIN = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")


def run(capital=50_000, start_date="2022-01-01", hold_days=20, top_n=10,
        execution=None):
    stock = StockData()
    cache = stock.cache[stock.cache["代码"].str.startswith(MAIN)].copy()

    def selector(ctx):
        history = cache[
            (cache["日期"] <= ctx.signal_date)
            & (cache["日期"] >= ctx.signal_date - pd.Timedelta(days=45))
        ]
        scores = {}
        for code, group in history.groupby("代码"):
            group = group.sort_values("日期")
            if len(group) < 20:
                continue
            close = group["收盘"].to_numpy()
            high = group["最高"].to_numpy()
            low = group["最低"].to_numpy()
            amount = group["成交额"].to_numpy()
            high20, low20 = np.max(high[-20:]), np.min(low[-20:])
            if low20 <= 0 or close[-20] <= 0:
                continue
            amplitude = (high20 - low20) / low20 * 100
            momentum = (close[-1] - close[-20]) / close[-20] * 100
            avg_amount = np.mean(amount[-20:])
            if not 3 <= amplitude <= 50 or not -30 < momentum < 50:
                continue
            if avg_amount < 5e7:
                continue
            score_amp = max(0, 1 - amplitude / 30)
            score_momentum = momentum / 30 + 0.5
            score_liquidity = min(avg_amount / 5e8, 1.0)
            score_strength = max(0, 1 - (high20 - close[-1]) / high20 * 5)
            scores[code] = (
                score_amp * 0.30 + score_momentum * 0.25
                + score_liquidity * 0.15 + score_strength * 0.30
            )
        return sorted(scores, key=scores.get, reverse=True)

    return run_rebalance_report(
        cache=cache, selector=selector, capital=capital,
        start_date=start_date, hold_days=hold_days, top_n=top_n,
        execution=execution or ExecutionConfig(
            commission_rate=0.0003, stamp_tax_rate=0.0003,
            price_tick=0.01,
        ),
        code_to_name={}, output_html=OUTPUT_HTML,
        title="策略7：月度多因子选股",
        subtitle=f"振幅+动量+成交额+强势度综合排名 · {hold_days}日轮动{top_n}只",
        params_html=(
            "💡 <b>信号：</b>调仓日前一交易日计算，避免同日收盘前视<br>"
            "💡 <b>因子：</b>20日振幅30% + 动量25% + 成交额15% + 强势度30%<br>"
            f"💡 <b>组合：</b>每{hold_days}个交易日等权持有{top_n}只主板股票<br>"
            "💡 <b>评估：</b>权益逐日盯市，结果可能不同于旧版仅调仓日估值"
        ),
        stats_title="月度多因子选股",
        stats_extra=f"4因子综合排名×{top_n}只×{hold_days}交易日",
        board="main",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--top", type=int, default=10)
    add_execution_args(parser, default_tick=0.01)
    args = parser.parse_args()
    started = time.time()
    run(args.capital, args.start, args.hold, args.top, execution_from_args(args))
    print(f"耗时: {time.time() - started:.0f}秒")
