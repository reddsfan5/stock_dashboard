#!/usr/bin/env python3
"""
Demo 4 — vectorbt 均线金叉参数扫描（30 只 ETF 小规模演示）

scripts/sweep.py 的全市场版这里缩到 30 只 ETF 跑通完整工作流：
  vbt.MA.run → 金叉/死叉信号 → 共享资金组合 → 3×3 参数矩阵 → 热力图

重要概念（详见 docs/10-库与生态.md）：
  - vectorbt 向量化回测按 bar 价撮合，用于「哪个参数组合有 edge」的粗筛；
    精细验证（T+1/涨跌停/手续费）仍用项目自研 sim 引擎
  - 首次运行 numba JIT 编译 10-30 秒，之后秒级

运行: .venv/bin/python demos/demo_vectorbt.py   （必须用 venv，见 demos/README.md）
"""

import os
import sys
import time

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

import vectorbt as vbt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data.etf import ETFData

OUT_PNG = os.path.join(PROJECT_DIR, "output", "sweep_heatmap_demo.png")
FAST = [5, 10, 20]
SLOW = [20, 30, 60]


def main():
    t0 = time.time()
    cache = ETFData().cache_with_prefix
    cache = cache[cache["代码"].str.startswith(("sh5", "sz1"))]
    codes = cache["代码"].unique()[:30]  # 取 30 只做演示
    close = (cache[cache["代码"].isin(codes)]
             .pivot_table(index="日期", columns="代码", values="收盘", aggfunc="last")
             .dropna(axis=1, how="all"))
    print(f"30 只 ETF, {close.index.min().date()} ~ {close.index.max().date()}")

    results = {}
    for fw in FAST:
        fast_ma = vbt.MA.run(close, fw).ma
        for sw in SLOW:
            slow_ma = vbt.MA.run(close, sw).ma
            pf = vbt.Portfolio.from_signals(
                close,
                fast_ma.vbt.crossed_above(slow_ma),
                fast_ma.vbt.crossed_below(slow_ma),
                freq="1D", cash_sharing=True, group_by=True,
            )
            results[(fw, sw)] = pf.total_return()

    print(f"扫描 {len(results)} 组合耗时 {time.time() - t0:.0f} 秒（含编译）\n")
    print("===== Top 5 =====")
    for (fw, sw), ret in sorted(results.items(), key=lambda kv: -kv[1])[:5]:
        print(f"  MA{fw:>2} / MA{sw:>2}: {ret:+.1%}")

    # 热力图
    grid = np.full((len(FAST), len(SLOW)), np.nan)
    for (fw, sw), ret in results.items():
        grid[FAST.index(fw), SLOW.index(sw)] = ret
    fig, ax = plt.subplots(figsize=(7, 5))
    im = ax.imshow(grid, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(SLOW))); ax.set_xticklabels([f"MA{s}" for s in SLOW])
    ax.set_yticks(range(len(FAST))); ax.set_yticklabels([f"MA{f}" for f in FAST])
    ax.set_title("30只ETF 均线金叉参数热力图（总收益）")
    fig.colorbar(im, ax=ax, format=lambda v, _: f"{v:.0%}")
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=150)
    print(f"\n✓ 热力图: {OUT_PNG}")


if __name__ == "__main__":
    main()
