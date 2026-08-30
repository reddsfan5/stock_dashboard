#!/usr/bin/env python3
"""ETF 动量轮动兼容入口。

实现已统一到 ``scripts.strategies.strategy_08_etf_momentum``，保留此模块避免旧命令失效。
"""

import argparse
import time

from scripts.strategies.rebalance_utils import add_execution_args, execution_from_args
from scripts.strategies.strategy_08_etf_momentum import run


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETF动量轮动策略（兼容入口）")
    parser.add_argument("--capital", type=float, default=50_000)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--start", default="2022-01-01")
    add_execution_args(parser, default_tick=0.001)
    args = parser.parse_args()
    started = time.time()
    run(args.capital, args.start, args.hold, args.top, execution_from_args(args))
    print(f"总耗时: {time.time() - started:.0f}秒")
