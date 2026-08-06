"""
模拟引擎 — 接收策略，跑完整回测流程，输出 HTML

策略只需实现 SimStrategy.scan_signals() 和 process_day()。
引擎负责：K线索引、逐日平仓、权益记录、强平、统计、K线采集、HTML 渲染。
"""

import os, random, time
from collections import defaultdict
from typing import List, Dict

import pandas as pd
from tqdm import tqdm

from data.kline import StockData
from data.industry import StockInfo
from backtest.sim_types import Position, Trade, EquityPoint
from backtest.sim_core import (
    build_kline_index, group_signals_by_date,
    record_equity, force_close_positions, print_stats,
    collect_kline_for_trades,
)
from backtest.strategy import SimStrategy, DayContext
from backtest.renderer import render_dip_buy_report


class SimEngine:
    """模拟引擎：策略无关的完整回测流程"""

    def __init__(self, strategy: SimStrategy):
        self.strategy = strategy

    def run(self, capital: float = 50000, start_date: str = "2024-01-01",
            target_pct: float = 1.0, commission_rate: float = 0.0001,
            stamp_tax: float = 0.0005, output_html: str = None,
            **strategy_kwargs) -> str:
        """
        执行完整回测。

        Args:
            capital: 初始资金
            start_date: 起始日期
            target_pct: 止盈目标%
            commission_rate: 佣金率
            stamp_tax: 印花税率
            output_html: HTML 输出路径（None=不输出）
            **strategy_kwargs: 传给 strategy.scan_signals 的额外参数

        Returns:
            HTML 文件路径（如果 output_html 指定）
        """
        strat = self.strategy
        data = StockData()
        info = StockInfo()
        code_to_name = dict(zip(info.df["代码"], info.df["名称"]))

        # 1. 标的过滤
        board = strat.get_board_filter()
        orig_cache = data.cache
        data._cache = orig_cache[orig_cache["代码"].str.startswith(board)]

        # 2. 信号扫描
        print("=" * 60)
        signals = strat.scan_signals(data, code_to_name, start_date,
                                     **strategy_kwargs)
        if len(signals) == 0:
            print("无信号"); return None
        data._cache = orig_cache

        # 3. K线索引 + 信号分组
        kline_idx = build_kline_index(data.cache, start_date)
        all_dates, sig_by_date = group_signals_by_date(signals, kline_idx)
        print(f"逐日模拟 ({len(all_dates)} 天)...")

        # 4. 逐日模拟
        cash = capital
        positions: List[Position] = []
        closed_trades: List[Trade] = []
        equity_curve: List[EquityPoint] = []

        for date in tqdm(all_dates, desc="模拟"):
            # a. 平仓（通用逻辑）
            survivors = []
            for pos in positions:
                key = (pos.code, date)
                if key not in kline_idx:
                    survivors.append(pos)
                    continue
                high, low, close, open_ = kline_idx[key]
                filled = high >= pos.target_price
                sp = pos.target_price if filled else close
                proceeds = pos.shares * sp
                fee = proceeds * commission_rate + proceeds * stamp_tax
                net = proceeds - fee
                cash += net
                closed_trades.append(Trade(
                    code=pos.code, name=code_to_name.get(pos.code, ""),
                    buy_date=pd.Timestamp(pos.buy_date).strftime("%Y-%m-%d"),
                    buy_price=round(pos.buy_price, 2),
                    sell_date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                    sell_price=round(sp, 2),
                    return_pct=round((net - pos.total_cost) / pos.total_cost * 100, 2),
                    pnl=round(net - pos.total_cost, 2),
                    filled=filled, lots=pos.shares // 100,
                    streak_days=pos.streak_days,
                ))
            positions = survivors

            # b. 开仓（策略决策）
            if date in sig_by_date:
                ctx = DayContext(
                    date=date, cash=cash, today_start_cash=cash,
                    today_signals=list(sig_by_date[date]),
                    kline_idx=kline_idx, commission_rate=commission_rate,
                    target_pct=target_pct, code_to_name=code_to_name,
                )
                new_positions = strat.process_day(ctx)
                for pos in new_positions:
                    if pos.total_cost > cash:
                        continue
                    cash -= pos.total_cost
                    positions.append(pos)

            # c. 记录权益
            equity_curve.append(record_equity(date, cash, positions))

        # 5. 期末强平
        fc_trades, cash = force_close_positions(
            positions, all_dates[-1], kline_idx, commission_rate,
            stamp_tax, code_to_name, cash)
        closed_trades.extend(fc_trades)

        # 6. 统计
        print_stats(closed_trades, cash, capital, start_date,
                    pd.Timestamp(all_dates[-1]).strftime("%Y-%m-%d"),
                    title=f"{strat.__class__.__name__} 模拟",
                    extra_info=f"+{target_pct}%止盈")

        # 7. K线 + HTML
        if output_html and closed_trades:
            kline_map = collect_kline_for_trades(data, closed_trades, code_to_name)
            html = render_dip_buy_report(
                capital=capital, target_pct=target_pct, overlap_pct=0,
                commission_rate=commission_rate, stamp_tax=stamp_tax,
                start_date=start_date, lookback=0, board="main",
                max_gain=99, limit_down=99, max_range_20d=99,
                trades=closed_trades, equity_curve=equity_curve,
                final_equity=cash, kline_map=kline_map,
            )
            os.makedirs(os.path.dirname(output_html), exist_ok=True)
            with open(output_html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"\n✓ HTML: {output_html}")

        return output_html
