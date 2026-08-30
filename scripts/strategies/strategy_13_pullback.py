#!/usr/bin/env python3
"""策略13：强趋势股票缩量回调。"""

import argparse
import os
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from backtest.execution import ExecutionConfig
from backtest.indicators import compute_all
from data.kline import StockData
from scripts.strategies.rebalance_utils import (
    add_execution_args, execution_from_args, run_rebalance_report,
)

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_13.html")
MAIN = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")


def run(capital=50_000, start_date="2022-01-01", hold_days=20, top_n=5,
        execution=None):
    print("计算全市场指标...")
    stock = StockData()
    cache = stock.cache[stock.cache["代码"].str.startswith(MAIN)].copy()
    indicators = compute_all(cache, MAIN)
    close = indicators["close"]

    def selector(ctx):
        if ctx.signal_date not in close.index:
            return []
        idx = close.index.get_loc(ctx.signal_date)
        strong = (
            (indicators["close"].iloc[idx] > indicators["ma150"].iloc[idx])
            & (indicators["ma150"].iloc[idx] > indicators["ma200"].iloc[idx])
        )
        near_high = indicators["near_high_250"].iloc[idx] > 0.7
        volume = indicators["vol_ratio_20"].iloc[idx]
        volume_ok = volume.notna() & (volume > 0.3)
        momentum5 = indicators["mom5"].iloc[idx]
        pullback = (momentum5 < 0) & (momentum5 > -8)
        shrink = volume < 0.7
        score = -momentum5
        mask = strong & near_high & volume_ok & pullback & shrink & score.notna()
        return score[mask].dropna().nlargest(ctx.top_n).index.tolist()

    return run_rebalance_report(
        cache=cache, selector=selector, capital=capital,
        start_date=start_date, hold_days=hold_days, top_n=top_n,
        execution=execution or ExecutionConfig(
            commission_rate=0.0003, stamp_tax_rate=0.0003,
            price_tick=0.01,
        ),
        code_to_name={}, output_html=OUTPUT_HTML,
        title="策略13：强趋势缩量回调",
        subtitle=f"MA150>MA200强势股 + 缩量回踩5日 · {hold_days}日轮动{top_n}只",
        params_html=(
            "💡 <b>趋势：</b>收盘>MA150>MA200，且接近52周高点<br>"
            "💡 <b>入场：</b>近5日回调0%～8%，成交额缩至20日均值70%以下<br>"
            f"💡 <b>组合：</b>回调幅度较大者优先，等权{top_n}只，持有{hold_days}个交易日<br>"
            "💡 <b>前视修复：</b>指标使用前一交易日，下一交易日收盘成交"
        ),
        stats_title="强趋势缩量回调",
        stats_extra=f"MA150>MA200+近高点+缩量回调 | {hold_days}日×{top_n}只",
        board="main", collect_klines=True, pivots=indicators,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--hold", type=int, default=20)
    parser.add_argument("--top", type=int, default=5)
    add_execution_args(parser, default_tick=0.01)
    args = parser.parse_args()
    started = time.time()
    run(args.capital, args.start, args.hold, args.top, execution_from_args(args))
    print(f"耗时: {time.time() - started:.0f}秒")
