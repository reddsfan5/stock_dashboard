#!/usr/bin/env python3
"""回测“21个市场口诀”的前七条并生成七项事件研究报告。

用法：
  python -m scripts.research.backtest_market_proverbs
  python -m scripts.research.backtest_market_proverbs --start 2010-01-01
  python -m scripts.research.backtest_market_proverbs --min-amount 100000000
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from backtest.proverb_report import build_proverb_report
from backtest.proverbs import (
    ProverbBacktestConfig,
    run_proverb_backtest,
    summary_frame,
)
from data.index import IndexData
from data.kline import StockData


DEFAULT_HTML = PROJECT_DIR / "output" / "market_proverbs.html"
DEFAULT_SUMMARY = PROJECT_DIR / "output" / "market_proverbs_summary.csv"
DEFAULT_EVENTS = PROJECT_DIR / "output" / "market_proverbs_recent_events.csv"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="市场口诀前七条事件回测")
    parser.add_argument("--start", default="2018-01-01", help="研究起始日")
    parser.add_argument("--end", default=None, help="研究截止日；默认使用缓存最新日")
    parser.add_argument(
        "--min-amount", type=float, default=50_000_000,
        help="信号日20日平均成交额下限（元，默认5000万）",
    )
    parser.add_argument("--out", default=str(DEFAULT_HTML), help="HTML 报告路径")
    parser.add_argument("--summary-csv", default=str(DEFAULT_SUMMARY), help="汇总 CSV 路径")
    parser.add_argument("--events-csv", default=str(DEFAULT_EVENTS), help="近期事件 CSV 路径")
    parser.add_argument("--no-nav", action="store_true", help="不刷新 index/mobile 导航")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = ProverbBacktestConfig(
        start_date=args.start,
        end_date=args.end,
        min_avg_amount=args.min_amount,
    )
    print("加载本地股票与指数日线...")
    stock = StockData().cache
    index = IndexData().cache
    if stock.empty or index.empty:
        raise RuntimeError("股票或指数缓存为空，请先运行 python -m scripts.update_cache")

    started = time.time()
    result = run_proverb_backtest(stock, index, config)
    summary = summary_frame(result)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_proverb_report(result), encoding="utf-8")
    summary.to_csv(args.summary_csv, index=False, encoding="utf-8-sig", float_format="%.6f")

    event_frames = []
    for rule_id, events in result["recent_events"].items():
        frame = pd.DataFrame(events)
        if len(frame):
            frame.insert(0, "回测项", rule_id.upper())
            event_frames.append(frame)
    if event_frames:
        pd.concat(event_frames, ignore_index=True).to_csv(
            args.events_csv, index=False, encoding="utf-8-sig"
        )

    print(f"\n完成，耗时 {time.time() - started:.1f}s")
    print(f"报告: {output}")
    print(f"汇总: {args.summary_csv}")
    print("\n主观察窗口结论：")
    primary = summary[summary["primary"]]
    for row in primary.itertuples(index=False):
        print(
            f"  {row.id.upper()} {row.title}: {row.status} | "
            f"样本 {row.samples:,} | 未来 {row.avg_return_pct:+.2f}% | "
            f"超额 {row.avg_excess_pct:+.2f}% | "
            f"目标提升 {row.target_lift_pct_point:+.2f}pp"
        )

    if not args.no_nav:
        for module in ("scripts.reports.gen_index", "scripts.reports.gen_mobile"):
            subprocess.run([sys.executable, "-m", module], cwd=PROJECT_DIR, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
