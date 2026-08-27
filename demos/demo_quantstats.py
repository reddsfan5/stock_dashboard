#!/usr/bin/env python3
"""
Demo 2 — quantstats 绩效报告（策略 8 实战）

复用 scripts/quant_report.py 的数据构造函数，产出：
  output/quantstats_demo.html — Sharpe/回撤/月度热力图/收益曲线全套
（与 scripts/quant_report.py 同功能，这里是"先自动跑策略出 CSV"的一站式版）

运行: /usr/local/bin/python demos/demo_quantstats.py
"""

import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "scripts"))

import pandas as pd

try:
    import quantstats as qs
except ImportError as e:
    sys.exit(f"quantstats 导入失败: {e}\n安装: pip install -r demos/requirements.txt")

from quant_report import build_returns, build_benchmark, print_trade_stats

EQUITY_CSV = os.path.join(PROJECT_DIR, "output", "etf_momentum_equity.csv")
TRADES_CSV = os.path.join(PROJECT_DIR, "output", "etf_momentum_trades.csv")
OUT_HTML = os.path.join(PROJECT_DIR, "output", "quantstats_demo.html")


def main():
    # 无 CSV 则先跑策略 8（~40 秒，产出交易/权益 CSV）
    if not os.path.exists(EQUITY_CSV):
        print("未找到权益 CSV，先运行策略 8 ...")
        subprocess.run([sys.executable, "scripts/strategy_08_etf_momentum.py"],
                       cwd=PROJECT_DIR, check=True)

    returns = build_returns(pd.read_csv(EQUITY_CSV))
    benchmark = build_benchmark("sh510300", returns.index)  # 沪深300ETF，本地离线

    print(f"策略 8 日频收益: {returns.index[0].date()} ~ {returns.index[-1].date()}")
    print(f"Sharpe {qs.stats.sharpe(returns):.2f} | Sortino {qs.stats.sortino(returns):.2f} | "
          f"CAGR {qs.stats.cagr(returns):.2%} | 最大回撤 {qs.stats.max_drawdown(returns):.2%}")

    print_trade_stats(pd.read_csv(TRADES_CSV))

    qs.reports.html(returns, benchmark=benchmark, output=OUT_HTML,
                    title="策略8 ETF动量轮动 — quantstats Demo")
    print(f"\n✓ 报告: {OUT_HTML}")


if __name__ == "__main__":
    main()
