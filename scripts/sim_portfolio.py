#!/usr/bin/env python3
"""
全市场 Overlap 组合模拟 — 优化版

优化点：
  1. groupby 一次性分组，避免 3043 次全表扫描
  2. 向量化计算每日重叠区
  3. 简化分配：按价格排序，买到资金不够最便宜的一手为止

用法：
  python scripts/sim_portfolio.py
  python scripts/sim_portfolio.py --capital 5000000 --commission 1.0
"""

import argparse
import os
import random
import sys
import time
import numpy as np
import pandas as pd
from tqdm import tqdm

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from data.industry import StockInfo
from backtest.sim_types import Position, Trade, EquityPoint
from backtest.sim_core import collect_kline_for_trades, print_stats
from backtest.renderer import render_dip_buy_report

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "portfolio_sim.html")


def compute_signals_fast(data: StockData, code_to_name: dict,
                         start_date: str = "2024-01-01",
                         min_days: int = 3, max_days: int = 10,
                         overlap_pct: float = 3.0) -> pd.DataFrame:
    """
    快速信号计算：
      1. 从缓存一次性取 start_date 之后数据
      2. groupby 分组后，逐只股票向量化计算重叠区
      3. 滑动窗口检测连续重叠，收集信号
    """
    print("准备数据...")
    cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
    cache = cache.sort_values(["代码", "日期"])

    # 预计算每只股票的前一日收盘（用于重叠率计算）
    cache["前收"] = cache.groupby("代码")["收盘"].shift(1)

    all_signals = []
    groups = cache.groupby("代码")

    for code, grp in tqdm(groups, desc="扫描信号", unit="只"):
        if len(grp) < 10:
            continue
        name = code_to_name.get(code, "")

        highs = grp["最高"].values
        lows = grp["最低"].values
        prev_close = grp["前收"].values  # 前一日收盘
        dates = grp["日期"].values

        # 向量化计算每日重叠区%: min(昨高,今高) - max(昨低,今低) > overlap_pct% × 昨收
        oh = np.minimum(highs[:-1], highs[1:])
        ol = np.maximum(lows[:-1], lows[1:])
        pct = (oh - ol) / prev_close[1:] * 100  # prev_close[1:] = 昨收
        ok = pct > overlap_pct

        # 滑动窗口：找连续 >= min_days 天满足条件的窗口
        n = len(ok)
        # 使用运行计数
        run_len = np.zeros(n, dtype=int)
        run_len[0] = 1 if ok[0] else 0
        for i in range(1, n):
            if ok[i]:
                run_len[i] = run_len[i-1] + 1
            else:
                run_len[i] = 0

        # 收集信号：run_len[i] >= min_days 且不超过 max_days
        for i in range(min_days - 1, n):
            if run_len[i] >= min_days:
                streak = min(run_len[i], max_days)
                buy_idx = i + 1 + 1  # overlap[i] 对应 dates[i+1]，买入日 = dates[i+2]
                if buy_idx >= len(dates) - 1:
                    continue
                all_signals.append({
                    "代码": code,
                    "名称": name,
                    "触发日": dates[buy_idx],
                    "买入价": lows[buy_idx],
                    "重叠天数": streak,
                })

    df = pd.DataFrame(all_signals)
    if len(df) > 0:
        df = df.sort_values("触发日").reset_index(drop=True)
    print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只股票")
    return df


def run_sim(capital: float = 50000, target_pct: float = 1.0,
            overlap_pct: float = 3.0, commission_rate: float = 0.0001,
            stamp_tax: float = 0.0005, start_date: str = "2024-01-01"):
    data = StockData()

    # 预加载全量股票名称（从 StockInfo，保证含创业/科创/北交）
    info = StockInfo()
    code_to_name = dict(zip(info.df["代码"], info.df["名称"]))

    # 1. 快速计算信号
    print("=" * 60)
    signals = compute_signals_fast(data, code_to_name, start_date=start_date, overlap_pct=overlap_pct)
    if len(signals) == 0:
        print("无信号"); return

    # 2. 构建 (代码, 日期) → K线 快速索引
    print("索引K线...")
    cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)]
    kline_idx = {}
    for _, row in tqdm(cache.iterrows(), total=len(cache), desc="索引"):
        kline_idx[(row["代码"], row["日期"])] = (row["最高"], row["最低"], row["收盘"])

    # 3. 逐日模拟（用全部交易日，不只是信号日）
    all_trading_days = sorted(set(d for _, d in kline_idx.keys()
                                  if d >= pd.Timestamp(start_date)))
    sig_by_date = {}
    for _, s in signals.iterrows():
        d = s["触发日"]
        sig_by_date.setdefault(d, []).append(s)

    print(f"逐日模拟 ({len(all_trading_days)} 天)...")

    cash = capital
    positions = []   # [(code, shares, buy_price, total_cost, target_price, buy_date)]
    closed_trades = []
    equity_curve = []

    for date in tqdm(all_trading_days, desc="模拟"):
        # a. 平仓：所有持仓在次日卖出
        survivors = []
        for pos in positions:
            code, shares, buy_price, total_cost, target_price, buy_date = pos
            key = (code, date)
            if key not in kline_idx:
                survivors.append(pos); continue
            high, _, close = kline_idx[key]
            sell_price = target_price if high >= target_price else close
            filled = high >= target_price
            proceeds = shares * sell_price
            sell_fee = proceeds * commission_rate + proceeds * stamp_tax
            net_proceeds = proceeds - sell_fee
            cash += net_proceeds
            closed_trades.append(Trade(
                code=code, name=code_to_name.get(code, ""),
                buy_date=pd.Timestamp(buy_date).strftime("%Y-%m-%d"),
                buy_price=buy_price,
                sell_date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                sell_price=sell_price,
                return_pct=round((net_proceeds - total_cost) / total_cost * 100, 2),
                pnl=round(net_proceeds - total_cost, 2),
                filled=filled, lots=shares // 100,
            ))
        positions = survivors

        # b. 当日新信号：先随机买2只（最大手数），剩余买低价
        if date in sig_by_date:
            today = sig_by_date[date]
            bought_codes = set()

            # 阶段1: 随机选2只，各买最大手数
            picks = random.sample(today, min(2, len(today)))
            for s in picks:
                buy_price = s["买入价"]
                lots = int(cash / (buy_price * 100 * (1 + commission_rate)))
                if lots <= 0:
                    continue
                shares = lots * 100
                cost = shares * buy_price
                buy_fee = cost * commission_rate
                total_cost = cost + buy_fee
                cash -= total_cost
                positions.append((
                    s["代码"], shares, buy_price, total_cost,
                    round(buy_price * (1 + target_pct / 100), 2), date,
                ))
                bought_codes.add(s["代码"])

            # 阶段2: 剩余现金按价格从低到高，买到不够买最便宜的一手
            rest = sorted(
                [s for s in today if s["代码"] not in bought_codes],
                key=lambda x: x["买入价"]
            )
            for s in rest:
                buy_price = s["买入价"]
                if cash < buy_price * 100 * (1 + commission_rate):
                    break
                lots = int(cash / (buy_price * 100 * (1 + commission_rate)))
                if lots <= 0:
                    continue
                shares = lots * 100
                cost = shares * buy_price
                buy_fee = cost * commission_rate
                total_cost = cost + buy_fee
                cash -= total_cost
                positions.append((
                    s["代码"], shares, buy_price, total_cost,
                    round(buy_price * (1 + target_pct / 100), 2), date,
                ))

        # c. 记录权益
        pos_value = sum(p[1] * p[2] for p in positions)
        equity_curve.append(EquityPoint(
            date=pd.Timestamp(date).strftime("%Y-%m-%d"),
            equity=cash + pos_value, cash=cash, positions=len(positions)))

    all_dates = all_trading_days

    # 4. 强制平仓
    if positions:
        last_date = all_dates[-1]
        for code, shares, buy_price, total_cost, target_price, buy_date in positions:
            key = (code, last_date)
            if key in kline_idx:
                _, _, close = kline_idx[key]
                sell_price = close
            else:
                sell_price = buy_price
            proceeds = shares * sell_price
            sell_fee = proceeds * commission_rate + proceeds * stamp_tax
            cash += proceeds - sell_fee
            closed_trades.append(Trade(
                code=code, name=code_to_name.get(code, ""),
                buy_date=pd.Timestamp(buy_date).strftime("%Y-%m-%d"),
                buy_price=buy_price,
                sell_date=pd.Timestamp(last_date).strftime("%Y-%m-%d"),
                sell_price=sell_price,
                return_pct=round((proceeds - sell_fee - total_cost) / total_cost * 100, 2),
                pnl=round(proceeds - sell_fee - total_cost, 2),
                filled=False, lots=shares // 100,
            ))

    # 5. 统计
    print_stats(closed_trades, cash, capital, start_date,
                pd.Timestamp(all_dates[-1]).strftime("%Y-%m-%d"),
                title="全市场 Overlap 组合模拟",
                extra_info=f"重叠>{overlap_pct}% | +{target_pct}%止盈")

    # 6. K线 + HTML
    kline_map = collect_kline_for_trades(data, closed_trades, code_to_name)
    html = render_dip_buy_report(
        capital=capital, target_pct=target_pct, overlap_pct=overlap_pct,
        commission_rate=commission_rate, stamp_tax=stamp_tax,
        start_date=start_date, lookback=3, board="main",
        max_gain=99, limit_down=99, max_range_20d=99,
        trades=closed_trades, equity_curve=equity_curve,
        final_equity=cash, kline_map=kline_map,
    )
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✓ HTML: {OUTPUT_HTML}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="全市场组合模拟（优化版）")
    parser.add_argument("--capital", type=float, default=50000)
    parser.add_argument("--target", type=float, default=1.0)
    parser.add_argument("--overlap", type=float, default=3.0)
    parser.add_argument("--commission", type=float, default=1.0, help="佣金(万分之)")
    args = parser.parse_args()

    t0 = time.time()
    run_sim(capital=args.capital, target_pct=args.target,
            overlap_pct=args.overlap,
            commission_rate=args.commission / 10000)
    print(f"\n总耗时: {time.time()-t0:.0f}秒")
