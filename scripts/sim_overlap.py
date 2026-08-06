#!/usr/bin/env python3
"""
Overlap 策略单股票模拟

规则：
  - 起始资金 5 万，每笔买最大手数（100 股/手）
  - 连续 N 天重叠 > X% → 第 N+1 天最低价买入
  - 挂限价单 +Y%，触及即成交，否则尾盘收盘价卖出

用法：
  python scripts/sim_overlap.py                           # 比亚迪，5万起
  python scripts/sim_overlap.py --code sh600519             # 茅台
  python scripts/sim_overlap.py --days 5 --pct 3.0          # 更严格的条件
  python scripts/sim_overlap.py --capital 100000 --target 2.0
"""

import argparse, os, sys, time

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from backtest.sim_types import Trade, EquityPoint
from backtest.sim_core import print_stats, collect_kline_for_trades
from backtest.renderer import render_dip_buy_report

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "overlap_sim.html")


def run_sim(code="sz002594", condition_days=3, overlap_pct=2.0,
            target_pct=1.0, capital=50000, start_date="2020-01-01",
            commission_rate=0.0001, stamp_tax=0.0005):
    data = StockData()
    name = data.get_stock_name(code)
    cache = data.cache[(data.cache["代码"] == code) &
                       (data.cache["日期"] >= pd.Timestamp(start_date))].sort_values("日期").copy()
    if len(cache) == 0:
        print(f"✗ {code} {start_date} 之后无数据"); return

    cache = cache.reset_index(drop=True)
    highs = cache["最高"].values
    lows = cache["最低"].values
    closes = cache["收盘"].values
    dates = cache["日期"].values

    # ---- 计算每日重叠区 ----
    overlaps = []
    for i in range(1, len(cache)):
        oh = min(highs[i-1], highs[i])
        ol = max(lows[i-1], lows[i])
        pct = (oh - ol) / closes[i-1] * 100
        overlaps.append({"idx": i, "overlap_ok": pct > overlap_pct})

    # ---- 逐笔模拟 ----
    cash = capital
    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []
    last_trade_date = None

    # 记录起始权益
    equity_curve.append(EquityPoint(
        date=pd.Timestamp(dates[0]).strftime("%Y-%m-%d"),
        equity=capital, cash=capital, positions=0))

    for i in range(condition_days - 1, len(overlaps)):
        streak = overlaps[i - condition_days + 1: i + 1]
        if not all(s["overlap_ok"] for s in streak):
            continue

        buy_idx = overlaps[i]["idx"]
        sell_idx = buy_idx + 1
        if sell_idx >= len(cache):
            continue

        buy_date = dates[buy_idx]
        if last_trade_date is not None and buy_date <= last_trade_date:
            continue

        buy_price = lows[buy_idx]
        lots = int(cash / (buy_price * 100))
        if lots <= 0:
            continue
        shares = lots * 100
        cost = shares * buy_price
        buy_fee = cost * commission_rate
        total_cost = cost + buy_fee
        cash -= total_cost

        target_price = round(buy_price * (1 + target_pct / 100), 2)
        sell_date = dates[sell_idx]
        actual_high = highs[sell_idx]
        actual_close = closes[sell_idx]

        filled = actual_high >= target_price
        sell_price = target_price if filled else actual_close
        proceeds = shares * sell_price
        sell_fee = proceeds * commission_rate + proceeds * stamp_tax
        net_proceeds = proceeds - sell_fee
        cash += net_proceeds

        trades.append(Trade(
            code=code, name=name,
            buy_date=pd.Timestamp(buy_date).strftime("%Y-%m-%d"),
            buy_price=round(buy_price, 2),
            sell_date=pd.Timestamp(sell_date).strftime("%Y-%m-%d"),
            sell_price=round(sell_price, 2),
            return_pct=round((net_proceeds - total_cost) / total_cost * 100, 2),
            pnl=round(net_proceeds - total_cost, 2),
            filled=filled, lots=lots,
            streak_days=condition_days,
        ))

        equity_curve.append(EquityPoint(
            date=pd.Timestamp(buy_date).strftime("%Y-%m-%d"),
            equity=cash + shares * buy_price, cash=cash, positions=1))
        equity_curve.append(EquityPoint(
            date=pd.Timestamp(sell_date).strftime("%Y-%m-%d"),
            equity=cash, cash=cash, positions=0))

        last_trade_date = sell_date

    # ---- 统计 ----
    end_date = pd.Timestamp(dates[-1]).strftime("%Y-%m-%d")
    print_stats(trades, cash, capital, start_date, end_date,
                title=f"{code} {name} Overlap 模拟",
                extra_info=f"连续≥{condition_days}天重叠>{overlap_pct}% | +{target_pct}%止盈")

    # ---- K线 + HTML ----
    code_to_name = {code: name}
    kline_map = collect_kline_for_trades(data, trades, code_to_name)
    html = render_dip_buy_report(
        capital=capital, target_pct=target_pct, overlap_pct=overlap_pct,
        commission_rate=commission_rate, stamp_tax=stamp_tax,
        start_date=start_date, lookback=condition_days, board="stock",
        max_gain=99, limit_down=99, max_range_20d=99,
        trades=trades, equity_curve=equity_curve,
        final_equity=cash, kline_map=kline_map,
    )
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✓ HTML: {OUTPUT_HTML}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Overlap 策略模拟交易")
    parser.add_argument("--code", type=str, default="sz002594")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--pct", type=float, default=2.0)
    parser.add_argument("--target", type=float, default=1.0)
    parser.add_argument("--capital", type=float, default=50000)
    parser.add_argument("--start", type=str, default="2020-01-01")
    args = parser.parse_args()

    t0 = time.time()
    run_sim(code=args.code, condition_days=args.days, overlap_pct=args.pct,
            target_pct=args.target, capital=args.capital, start_date=args.start)
    print(f"\n总耗时: {time.time() - t0:.0f}秒")
