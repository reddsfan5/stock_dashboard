#!/usr/bin/env python3
"""
vectorbt 参数扫描 — 均线金叉策略的全组合快速粗筛

用法
----
$ python scripts/sweep.py --universe etf                       # ETF 池（默认，秒级）
$ python scripts/sweep.py --universe etf --fast 5,10,20 --slow 20,30,60
$ python scripts/sweep.py --universe stock                     # 主板股票（吃指标缓存）
$ python scripts/sweep.py --universe etf --top 20 --heatmap    # 输出热力图

说明
----
- 撮合按 bar 价（vectorbt 向量化回测的固有近似），结果用于「哪个参数组合有 edge」
  的粗筛定位；精细验证仍用自研 sim 引擎（T+1/涨跌停/手续费规则齐全）
- 首次运行 numba JIT 编译需 10-30 秒

⚠ 运行环境: 必须用 .venv/bin/python（vectorbt 与系统 numpy 2.0 冲突，已隔离；
py3.9 下 vectorbt 锁 numba<0.57→numpy<1.24），其余脚本用 /usr/local/bin/python：
    .venv/bin/python scripts/sweep.py --universe etf

输出
----
output/sweep_results.csv   — 全组合结果（按总收益降序）
output/sweep_heatmap.png   — 可选热力图（--heatmap）
"""

import argparse
import os
import sys

import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

try:
    import vectorbt as vbt
except ImportError as e:
    sys.exit(f"vectorbt 导入失败: {e}\n安装: pip install vectorbt（py3.9: numba==0.59.1）")

from data.etf import ETFData
from data.kline import StockData


def load_close(universe: str) -> pd.DataFrame:
    """加载收盘价透视表（index=日期, columns=代码）"""
    if universe == "etf":
        cache = ETFData().cache_with_prefix
        cache = cache[cache["代码"].str.startswith(("sh5", "sz1"))]
    else:
        from backtest.indicators import compute_all
        from data.sources import MAIN_BOARD_PREFIX
        cache = StockData().cache
        # 只扫主板（与选股系统约定一致），剔除上市太晚的次新股
        # 缓存代码带 sh/sz 前缀，先去掉前两位再匹配数字前缀
        cache = cache[cache["代码"].str[2:].str.startswith(MAIN_BOARD_PREFIX)]
        ind = compute_all(cache, use_cache=False)["close"]
        ind = ind.dropna(axis=1, thresh=int(len(ind) * 0.8))
        return ind

    close = cache.pivot_table(index="日期", columns="代码", values="收盘", aggfunc="last")
    close = close.dropna(axis=1, how="all")
    return close


def main():
    parser = argparse.ArgumentParser(description="vectorbt 均线金叉参数扫描")
    parser.add_argument("--universe", choices=["etf", "stock"], default="etf")
    parser.add_argument("--fast", default="5,10,20", help="快均线窗口（逗号分隔）")
    parser.add_argument("--slow", default="20,30,60", help="慢均线窗口（逗号分隔）")
    parser.add_argument("--out", default=os.path.join(PROJECT_DIR, "output", "sweep_results.csv"))
    parser.add_argument("--top", type=int, default=10, help="打印前 N 组合")
    parser.add_argument("--heatmap", action="store_true", help="输出热力图 PNG")
    args = parser.parse_args()

    fast_windows = [int(x) for x in args.fast.split(",")]
    slow_windows = [int(x) for x in args.slow.split(",")]

    print(f"加载 {args.universe} 收盘价...")
    close = load_close(args.universe)
    print(f"  标的 {close.shape[1]} 只, 区间 {close.index.min().date()} ~ {close.index.max().date()}")
    if len(close) < max(slow_windows) + 10:
        print(f"  ⚠ 数据仅 {len(close)} 个交易日 < MA{max(slow_windows)} 窗口，长窗口结果无意义")
        print("    股票缓存是滚动 60 天；长窗口扫描请用 --universe etf 或先回填缓存")

    print(f"扫描 {len(fast_windows)}×{len(slow_windows)} 组参数（首次含 numba 编译）...")
    # 逐组合循环：run_combs 会把组合展开到每个标的列，直接按组合构造
    # 共享资金组合（cash_sharing=True）——信号日资金均分，贴近策略 08 的等权语义
    rows = []
    for fw in fast_windows:
        fast_ma = vbt.MA.run(close, fw).ma
        for sw in slow_windows:
            slow_ma = vbt.MA.run(close, sw).ma
            entries = fast_ma.vbt.crossed_above(slow_ma)
            exits = fast_ma.vbt.crossed_below(slow_ma)
            pf = vbt.Portfolio.from_signals(
                close, entries, exits, freq="1D",
                cash_sharing=True, group_by=True,
            )
            rows.append({"快均线": fw, "慢均线": sw, "总收益": pf.total_return()})

    results = pd.DataFrame(rows).sort_values("总收益", ascending=False).reset_index(drop=True)
    results = results.astype({"快均线": "int64", "慢均线": "int64"})
    results.to_csv(args.out, index=False, encoding="utf-8")
    print(f"\n✓ 结果: {args.out}")

    print(f"\n===== Top {args.top} 组合 =====")
    # 用 to_dict 而非 iterrows：iterrows 会按行对齐 dtype 把 int 变 float
    for row in results.head(args.top).to_dict("records"):
        print(f"  MA{row['快均线']:>2} / MA{row['慢均线']:>2}: {row['总收益']:+.1%}")

    if args.heatmap:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        total_return = results.pivot(index="快均线", columns="慢均线", values="总收益")
        total_return = total_return.reindex(index=fast_windows, columns=slow_windows)
        fig, ax = plt.subplots(figsize=(8, 6))
        im = ax.imshow(total_return, cmap="RdYlGn", aspect="auto")
        ax.set_xticks(range(len(slow_windows)))
        ax.set_xticklabels([f"MA{s}" for s in slow_windows])
        ax.set_yticks(range(len(fast_windows)))
        ax.set_yticklabels([f"MA{f}" for f in fast_windows])
        ax.set_title(f"{args.universe} 均线金叉参数热力图（总收益）")
        fig.colorbar(im, ax=ax, format=lambda v, _: f"{v:.0%}")
        out_png = os.path.join(PROJECT_DIR, "output", "sweep_heatmap.png")
        fig.tight_layout()
        fig.savefig(out_png, dpi=150)
        print(f"✓ 热力图: {out_png}")


if __name__ == "__main__":
    main()
