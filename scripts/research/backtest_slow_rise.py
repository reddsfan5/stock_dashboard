#!/usr/bin/env python3
"""近 30 日相对低位缓涨、次日开盘买入、5 日止盈退出回测。"""

from __future__ import annotations

import argparse
from dataclasses import replace
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from backtest.execution import ExecutionConfig
from backtest.slow_rise import SlowRiseConfig, run_slow_rise_backtest, summary_frame
from backtest.slow_rise_report import build_slow_rise_report
from data.kline import StockData


OUTPUT_HTML = PROJECT_DIR / "output" / "slow_rise_backtest.html"
OUTPUT_SUMMARY = PROJECT_DIR / "output" / "slow_rise_summary.csv"
OUTPUT_TRADES = PROJECT_DIR / "output" / "slow_rise_trades.csv"
OUTPUT_YEARLY = PROJECT_DIR / "output" / "slow_rise_yearly.csv"
OUTPUT_STRICT_SUMMARY = PROJECT_DIR / "output" / "slow_rise_strict_summary.csv"
OUTPUT_UNFILTERED_SUMMARY = PROJECT_DIR / "output" / "slow_rise_unfiltered_summary.csv"


def _args():
    parser = argparse.ArgumentParser(description="低位缓涨3～5日、开盘买入、5日止盈回测")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--n-values", default="3,4,5", help="观察天数，逗号分隔")
    parser.add_argument("--daily-min", type=float, default=-1.0)
    parser.add_argument("--daily-max", type=float, default=1.5)
    parser.add_argument("--cumulative-min", type=float, default=1.0)
    parser.add_argument("--cumulative-max", type=float, default=5.0)
    parser.add_argument("--positive-ratio", type=float, default=0.6)
    parser.add_argument("--target", type=float, default=5.0)
    parser.add_argument("--hold-days", type=int, default=5)
    parser.add_argument("--min-amount", type=float, default=50_000_000)
    parser.add_argument("--low-window", type=int, default=30,
                        help="相对低位回看交易日数（默认30）")
    parser.add_argument("--max-position", type=float, default=30.0,
                        help="当前价在区间中的最高相对位置%%（默认30）")
    parser.add_argument("--no-low-filter", action="store_true",
                        help="关闭相对低位过滤，复现上一版缓涨规则")
    parser.add_argument("--commission-bps", type=float, default=3.0)
    parser.add_argument("--sell-tax-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--out", default=str(OUTPUT_HTML))
    parser.add_argument("--summary-csv", default=str(OUTPUT_SUMMARY))
    parser.add_argument("--trades-csv", default=str(OUTPUT_TRADES))
    parser.add_argument("--yearly-csv", default=str(OUTPUT_YEARLY))
    parser.add_argument("--strict-summary-csv", default=str(OUTPUT_STRICT_SUMMARY))
    parser.add_argument("--unfiltered-summary-csv", default=str(OUTPUT_UNFILTERED_SUMMARY))
    parser.add_argument("--skip-sensitivity", action="store_true",
                        help="跳过‘每天都小涨’严格口径复核")
    parser.add_argument("--no-nav", action="store_true")
    return parser.parse_args()


def main():
    args = _args()
    n_values = tuple(int(value.strip()) for value in args.n_values.split(",") if value.strip())
    config = SlowRiseConfig(
        start_date=args.start,
        end_date=args.end,
        n_values=n_values,
        daily_min_pct=args.daily_min,
        daily_max_pct=args.daily_max,
        cumulative_min_pct=args.cumulative_min,
        cumulative_max_pct=args.cumulative_max,
        min_positive_ratio=args.positive_ratio,
        target_pct=args.target,
        hold_days=args.hold_days,
        min_avg_amount=args.min_amount,
        relative_low_window=args.low_window,
        max_relative_position_pct=None if args.no_low_filter else args.max_position,
        execution=ExecutionConfig(
            commission_rate=args.commission_bps / 10_000,
            stamp_tax_rate=args.sell_tax_bps / 10_000,
            slippage_bps=args.slippage_bps,
            min_commission=0,
        ),
    )
    print("加载本地日线，执行低位缓涨策略回测...")
    started = time.time()
    cache = StockData().cache
    result = run_slow_rise_backtest(cache, config)
    unfiltered_summary = None
    if config.max_relative_position_pct is not None:
        unfiltered_config = replace(config, max_relative_position_pct=None)
        unfiltered_result = run_slow_rise_backtest(cache, unfiltered_config)
        unfiltered_summary = summary_frame(unfiltered_result)
        result["unfiltered"] = unfiltered_summary.to_dict("records")
        unfiltered_summary.to_csv(
            args.unfiltered_summary_csv,
            index=False, encoding="utf-8-sig", float_format="%.6f",
        )
    strict_summary = None
    if not args.skip_sensitivity:
        strict_config = replace(config, daily_min_pct=0.0, min_positive_ratio=1.0)
        strict_result = run_slow_rise_backtest(cache, strict_config)
        strict_summary = summary_frame(strict_result)
        result["sensitivity"] = strict_summary.to_dict("records")
        strict_summary.to_csv(
            args.strict_summary_csv, index=False, encoding="utf-8-sig", float_format="%.6f"
        )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_slow_rise_report(result), encoding="utf-8")
    summary = summary_frame(result)
    summary.to_csv(args.summary_csv, index=False, encoding="utf-8-sig", float_format="%.6f")
    result["trades"].to_csv(
        args.trades_csv, index=False, encoding="utf-8-sig", float_format="%.6f"
    )
    import pandas as pd
    pd.DataFrame(result["yearly"]).to_csv(
        args.yearly_csv, index=False, encoding="utf-8-sig", float_format="%.6f"
    )

    print(f"\n完成，耗时 {time.time() - started:.1f}s")
    for row in summary.itertuples(index=False):
        print(
            f"  N={row.n_days}: {row.status} | {row.trades:,}笔 | "
            f"净收益 {row.avg_net_return_pct:+.2f}% | 胜率 {row.win_rate_pct:.2f}% | "
            f"止盈 {row.target_exit_rate_pct:.2f}%（基线 {row.baseline_target_rate_pct:.2f}%） | "
            f"超额 {row.avg_excess_pct:+.2f}%"
        )
    if strict_summary is not None:
        print("  严格口径（每天都上涨）：")
        for row in strict_summary.itertuples(index=False):
            print(
                f"    N={row.n_days}: {row.status} | {row.trades:,}笔 | "
                f"净收益 {row.avg_net_return_pct:+.2f}% | 止盈 {row.target_exit_rate_pct:.2f}%"
            )
    if unfiltered_summary is not None:
        print(f"  去掉{config.relative_low_window}日低位过滤后的对照：")
        for row in unfiltered_summary.itertuples(index=False):
            print(
                f"    N={row.n_days}: {row.trades:,}笔 | 净收益 {row.avg_net_return_pct:+.2f}% | "
                f"止盈 {row.target_exit_rate_pct:.2f}%"
            )
    print(f"报告: {output}")
    print(f"交易明细: {args.trades_csv}")
    if not args.no_nav:
        for module in ("scripts.reports.gen_index", "scripts.reports.gen_mobile"):
            subprocess.run([sys.executable, "-m", module], cwd=PROJECT_DIR, check=False)


if __name__ == "__main__":
    main()
