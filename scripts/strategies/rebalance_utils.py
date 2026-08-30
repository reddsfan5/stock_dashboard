"""策略 07～14 共用的轮动执行与报告辅助函数。"""

import os
from dataclasses import asdict
from typing import Mapping, Optional

import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.rebalance import RebalanceConfig, RebalanceEngine
from backtest.renderer import render_dip_buy_report
from backtest.sim_core import collect_kline_for_trades, print_stats


def add_execution_args(parser, *, default_tick: float):
    parser.add_argument("--commission-bps", type=float, default=3.0,
                        help="佣金，基点（默认3，即万3）")
    parser.add_argument("--sell-tax-bps", type=float, default=3.0,
                        help="卖出税费，基点（默认3）")
    parser.add_argument("--min-commission", type=float, default=0.0,
                        help="单笔最低佣金，默认0以兼容历史结果")
    parser.add_argument("--slippage-bps", type=float, default=0.0,
                        help="单边滑点，基点")
    parser.add_argument("--price-tick", type=float, default=default_tick,
                        help="最小报价单位")
    parser.add_argument("--max-participation", type=float, default=None,
                        help="单笔成交额占当日成交额上限，例如0.01")
    parser.add_argument("--reject-one-price-limit", action="store_true",
                        help="一字涨停不买、一字跌停不卖")
    parser.add_argument("--price-limit-pct", type=float, default=10.0,
                        help="涨跌停判断阈值%%")


def execution_from_args(args) -> ExecutionConfig:
    return ExecutionConfig(
        commission_rate=args.commission_bps / 10_000,
        min_commission=args.min_commission,
        stamp_tax_rate=args.sell_tax_bps / 10_000,
        slippage_bps=args.slippage_bps,
        price_tick=args.price_tick,
        max_amount_participation=args.max_participation,
        reject_one_price_limit=args.reject_one_price_limit,
        price_limit_pct=args.price_limit_pct,
    )


def run_rebalance_report(
    *,
    cache: pd.DataFrame,
    selector,
    capital: float,
    start_date: str,
    hold_days: int,
    top_n: int,
    execution: ExecutionConfig,
    code_to_name: Optional[Mapping[str, str]],
    output_html: str,
    title: str,
    subtitle: str,
    params_html: str,
    stats_title: str,
    stats_extra: str,
    board: str,
    collect_klines: bool = False,
    pivots: Optional[dict] = None,
):
    config = RebalanceConfig(
        capital=capital,
        start_date=start_date,
        interval_days=hold_days,
        top_n=top_n,
        execution_price="close",
        execution=execution,
    )
    result = RebalanceEngine(config).run(
        cache, selector, code_to_name=code_to_name,
    )
    sell_events = [event for event in result.events if event.get("side") == "sell"]
    sell_fill_rate = (
        sum(event.get("filled_shares", 0) > 0 for event in sell_events)
        / len(sell_events) * 100
        if sell_events else 0.0
    )
    print_stats(
        result.trades, result.final_equity, capital,
        result.start_date, result.end_date,
        title=stats_title, extra_info=stats_extra,
        metrics=result.metrics,
        secondary_rate_label="卖出成交率",
        secondary_rate=sell_fill_rate,
    )

    kline_map = {}
    if collect_klines and result.trades:
        kline_map = collect_kline_for_trades(
            type("DataView", (), {"cache": cache})(),
            result.trades, dict(code_to_name or {}), pivots=pivots,
        )

    assumptions = result.assumptions["execution"]
    assumptions_html = (
        "🔎 <b>回测假设：</b>前一交易日信号 → 调仓日收盘成交 · 逐日收盘盯市"
        f" · 滑点{assumptions['slippage_bps']:g}bp"
        f" · 最低佣金¥{assumptions['min_commission']:g}"
        f" · 成交额上限{_participation_label(assumptions['max_amount_participation'])}"
    )
    html = render_dip_buy_report(
        capital=capital, target_pct=0, overlap_pct=0,
        commission_rate=execution.commission_rate,
        stamp_tax=execution.stamp_tax_rate,
        start_date=result.start_date, lookback=hold_days, board=board,
        max_gain=99, limit_down=99, max_range_20d=99,
        trades=result.trades, equity_curve=result.equity_curve,
        final_equity=result.final_equity, kline_map=kline_map,
        metrics=result.metrics, title=title, subtitle=subtitle,
        params_html=params_html, assumptions_html=assumptions_html,
        secondary_rate_label="卖出成交率",
        secondary_rate=sell_fill_rate,
    )
    output_dir = os.path.dirname(output_html)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_html, "w", encoding="utf-8") as file:
        file.write(html)
    print(f"✓ HTML: {output_html}")
    return result


def export_result_csv(result, trades_path: str, equity_path: str):
    if result.trades:
        pd.DataFrame([asdict(trade) for trade in result.trades]).to_csv(
            trades_path, index=False, encoding="utf-8",
        )
    if result.equity_curve:
        pd.DataFrame([asdict(point) for point in result.equity_curve]).to_csv(
            equity_path, index=False, encoding="utf-8",
        )


def load_etf_universe():
    from data.etf import ETFData
    from data.kline import StockData

    stock = StockData()
    etf = ETFData()
    cache = pd.concat([stock.cache, etf.cache_with_prefix], ignore_index=True)
    cache = cache[cache["代码"].str.startswith(("sh5", "sz1"))].copy()
    listing = etf.get_list()
    names = {
        ("sh" if str(code).startswith("5") else "sz") + str(code): name
        for code, name in zip(listing["代码"], listing["名称"])
    }
    return stock, cache, names


def _participation_label(value):
    return "不限" if value is None else f"{value * 100:g}%"
