#!/usr/bin/env python3
"""
回测管线入口 — 一键运行全部策略，生成统计报告 HTML

用法：
  python scripts/backtest.py                       # 全策略
  python scripts/backtest.py --only overlap,rising  # 指定策略
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import List, Dict

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

ALL_STRATEGIES = [
    ("baseline",  "基准: 任意天低买次日高卖",
     lambda c: __import__("backtest.engine", fromlist=["BaselineStrategy"]).BaselineStrategy(c), False),
    ("overlap",   "重叠>3%低买高卖",
     lambda c: __import__("backtest.engine", fromlist=["OverlapBuyStrategy"]).OverlapBuyStrategy(c), True),
    ("overlap_close", "重叠>3%收盘买入高卖",
     lambda c: __import__("backtest.engine", fromlist=["OverlapCloseStrategy"]).OverlapCloseStrategy(c), True),
    ("rising",    "连续上涨后次日涨跌",
     lambda c: __import__("backtest.engine", fromlist=["RisingStreakStrategy"]).RisingStreakStrategy(c), True),
    ("newhigh",   "连涨后5日创新高",
     lambda c: __import__("backtest.engine", fromlist=["RisingNewHighStrategy"]).RisingNewHighStrategy(c, horizon=5), False),
    ("volume_up",   "放量上涨后次日",
     lambda c: __import__("backtest.engine", fromlist=["VolumeUpStrategy"]).VolumeUpStrategy(c, volume_increase=True), True),
    ("volume_down", "缩量上涨后次日",
     lambda c: __import__("backtest.engine", fromlist=["VolumeUpStrategy"]).VolumeUpStrategy(c, volume_increase=False), True),
    ("volume_dry",  "地量后突破",
     lambda c: __import__("backtest.engine", fromlist=["VolumeDryUpStrategy"]).VolumeDryUpStrategy(c), True),
    ("gap_fill",    "跳空缺口回补(10日)",
     lambda c: __import__("backtest.engine", fromlist=["GapFillStrategy"]).GapFillStrategy(c), False),
    ("oversold",    "连续下跌后反弹",
     lambda c: __import__("backtest.engine", fromlist=["OversoldBounceStrategy"]).OversoldBounceStrategy(c), True),
    ("bollinger",   "布林带收敛突破",
     lambda c: __import__("backtest.engine", fromlist=["BollingerSqueezeStrategy"]).BollingerSqueezeStrategy(c), True),
    ("multi_signal","双信号: 重叠+缩量",
     lambda c: __import__("backtest.engine", fromlist=["MultiSignalStrategy"]).MultiSignalStrategy(c), True),
]


def run_all(only_ids: List[str] = None, start_date: str = None) -> List[Dict]:
    from backtest.engine import StatsEngine, StatsConfig
    from data.kline import StockData
    data = StockData(); engine = StatsEngine(data, start_date=start_date)
    strategies = [s for s in ALL_STRATEGIES if not only_ids or s[0] in only_ids]
    results = []
    for sid, title, factory, multi_n in strategies:
        try:
            if multi_n:
                configs = [StatsConfig(condition_days=n, overlap_pct=3.0, gain_pct=2.0)
                           for n in range(5, 11)]
                s = factory(configs[0]); batch = engine.run_multi(type(s), configs)
            else:
                s = factory(StatsConfig(condition_days=0, overlap_pct=3.0, gain_pct=2.0))
                batch = [engine.run(s)]
            rows = [{"label": str(r.config.condition_days) if r.config.condition_days > 0 else r.name,
                     "samples": r.total_samples, "success_rate": round(r.success_rate, 1),
                     "mean_gain": round(r.mean_gain, 2), "median_gain": round(r.median_gain, 2),
                     "thresholds": {str(k): round(v, 1) for k, v in r.thresholds.items()}}
                    for r in batch]
            results.append({"id": sid, "title": title, "results": rows})
            print(f"  ✓ {title}: {sum(r['samples'] for r in rows):,} samples")
        except Exception as e:
            print(f"  ✗ {title}: {e}")
    return results


def main():
    parser = argparse.ArgumentParser(description="回测统计管线")
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--start", type=str, default=None,
                        help="起始日期，如 2024-01-01，只统计此日期之后的数据")
    parser.add_argument("--out", type=str,
                        default=os.path.join(PROJECT_DIR, "output", "stats_report.html"))
    args = parser.parse_args()

    only = args.only.split(",") if args.only else None
    print(f"运行 {len(ALL_STRATEGIES) if not only else len(only)} 个策略"
          f"{' (从 ' + args.start + ')' if args.start else ''}...\n")

    t0 = time.time()
    results = run_all(only, start_date=args.start)
    print(f"\n总耗时: {time.time()-t0:.0f}秒")

    from pipeline.reporter import build_backtest_html
    html = build_backtest_html(results)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    kb = os.path.getsize(args.out) / 1024
    print(f"✓ 报告: {args.out} ({kb:.0f}KB)")


if __name__ == "__main__":
    main()
