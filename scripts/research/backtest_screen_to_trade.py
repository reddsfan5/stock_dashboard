#!/usr/bin/env python3
"""选股信号 → 次日开盘买入 → 持有 N 日的可证伪回测入口。"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from backtest.execution import ExecutionConfig
from backtest.screen_to_trade import MODULE_TITLES, ScreenToTradeConfig, run_screen_to_trade
from backtest.screen_to_trade_report import write_report_files
from data.index import IndexData
from data.kline import StockData


OUTPUT_HTML = PROJECT_DIR / "output" / "screen_to_trade_report.html"
OUTPUT_JSON = PROJECT_DIR / "output" / "screen_to_trade_report.json"
OUTPUT_TRADES = PROJECT_DIR / "output" / "screen_to_trade_trades.csv"


def _args():
    parser = argparse.ArgumentParser(
        description="选股模块历史信号 → 次日买入持有回测（对照基线）"
    )
    parser.add_argument(
        "--module", default="hammer",
        choices=sorted(MODULE_TITLES.keys()),
        help="选股模块：hammer / continuity / sideways",
    )
    parser.add_argument("--hold", type=int, default=5, help="持有交易日数")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument(
        "--entry", default="next_open", choices=["next_open", "next_close"],
    )
    parser.add_argument("--max-per-day", type=int, default=20)
    parser.add_argument(
        "--min-amount", type=float, default=50_000_000, help="流动性门槛（元）",
    )
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--notional", type=float, default=10_000)
    parser.add_argument("--validation-ratio", type=float, default=0.3)
    parser.add_argument("--embargo", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--index", default="sh000300")
    parser.add_argument("--commission-bps", type=float, default=3.0)
    parser.add_argument("--sell-tax-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--out", default=str(OUTPUT_HTML))
    parser.add_argument("--json-out", default=str(OUTPUT_JSON))
    parser.add_argument("--trades-csv", default=str(OUTPUT_TRADES))
    parser.add_argument("--no-industry-placeholder", action="store_true")
    return parser.parse_args()


def main():
    args = _args()
    config = ScreenToTradeConfig(
        module=args.module,
        start_date=args.start,
        end_date=args.end,
        hold_days=args.hold,
        entry_timing=args.entry,
        max_signals_per_day=args.max_per_day,
        min_avg_amount=args.min_amount,
        capital=args.capital,
        notional_per_trade=args.notional,
        validation_ratio=args.validation_ratio,
        embargo_size=args.embargo,
        random_seed=args.seed,
        index_code=args.index,
        include_industry_neutral=not args.no_industry_placeholder,
        execution=ExecutionConfig(
            commission_rate=args.commission_bps / 10_000,
            stamp_tax_rate=args.sell_tax_bps / 10_000,
            slippage_bps=args.slippage_bps,
            min_commission=0.0,
        ),
    )
    print(
        f"加载本地日线，执行 {config.module_title} → "
        f"持有{config.hold_days}日 回测..."
    )
    started = time.time()
    stock = StockData().cache
    index = IndexData().cache
    result = run_screen_to_trade(stock, index, config)
    write_report_files(result, args.out, args.json_out)
    trades = result["trades"]
    if not trades.empty:
        Path(args.trades_csv).parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(
            args.trades_csv, index=False, encoding="utf-8-sig", float_format="%.6f",
        )

    card = result["report_card"]
    event = card["event_stats"]
    print(f"\n完成，耗时 {time.time() - started:.1f}s")
    print(
        f"  {config.module_title}: {event['trades']:,} 笔 / "
        f"{event['signal_dates']:,} 个信号日 | "
        f"均净收益 {event['avg_net_return_pct']:+.2f}% | "
        f"胜率 {event['win_rate_pct']:.2f}% | "
        f"换手 {card['turnover']:.2f} | "
        f"最大回撤 {card['portfolio_metrics']['max_drawdown_pct']:.2f}%"
    )
    for row in card.get("baselines") or []:
        if not row.get("available"):
            print(f"  基线 {row.get('baseline')}: 不可用 — {row.get('reason')}")
            continue
        excess = row.get("excess_pct")
        excess_txt = f"{excess:+.2f}%" if excess is not None else "—"
        print(
            f"  vs {row.get('baseline')}: 基线 "
            f"{(row.get('baseline_avg_pct') or 0):+.2f}% | "
            f"超额 {excess_txt}"
        )
    weak = (card.get("slices") or {}).get("weak_slices") or []
    if weak:
        print("  偏弱切片:")
        for w in weak:
            print(
                f"    - {w.get('slice')} ({w.get('kind')}): "
                f"{w.get('avg_net_return_pct'):+.2f}% / {w.get('trades')} 笔"
            )
    print(f"报告: {args.out}")
    print(f"JSON: {args.json_out}")
    if not trades.empty:
        print(f"交易明细: {args.trades_csv}")


if __name__ == "__main__":
    main()
