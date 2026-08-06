#!/usr/bin/env python3
"""
持续推高买入模拟 — 使用 Strategy + Engine 模式

策略：
  1. 每天扫描全市场，找连续 ≥N 天「最高价 > 前收 × 1.015」的标的
  2. 当天只选收阴 2%~5% + 连续≤8天，随机打乱，逐只半仓买入至90%
  3. 次日挂 +m% 卖单，触及则止盈卖出，否则尾盘强平

用法：
  python scripts/sim_upward_gap.py
  python scripts/sim_upward_gap.py --capital 100000 -n 5 -m 1.5
"""

import argparse, os, sys, time, random

import numpy as np
import pandas as pd
from tqdm import tqdm

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from backtest.strategy import SimStrategy, DayContext
from backtest.sim_types import Position
from backtest.sim_core import compute_lots
from backtest.sim_engine import SimEngine

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "upward_gap_sim.html")


# ====================================================================
# 策略定义（唯一需要写的部分）
# ====================================================================

class UpwardGapStrategy(SimStrategy):
    """持续推高 + 收阴买入"""

    def __init__(self, min_days=5, gap_pct=1.5):
        self.min_days = min_days
        self.gap_pct = gap_pct

    # ---------- 信号扫描 ----------

    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        min_days = kwargs.get("min_days", self.min_days)
        gap_pct = kwargs.get("gap_pct", self.gap_pct)

        print("准备数据...")
        cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
        cache = cache.sort_values(["代码", "日期"])
        cache["前收"] = cache.groupby("代码")["收盘"].shift(1)

        all_signals = []
        for code, grp in tqdm(cache.groupby("代码"), desc="扫描信号", unit="只"):
            if len(grp) < min_days + 3:
                continue
            highs = grp["最高"].values
            prev_close = grp["前收"].values
            dates = grp["日期"].values
            threshold = 1 + gap_pct / 100

            ok = np.zeros(len(grp), dtype=bool)
            for i in range(1, len(grp)):
                if pd.notna(prev_close[i]) and prev_close[i] > 0:
                    ok[i] = highs[i] > prev_close[i] * threshold

            n = len(ok)
            run_len = np.zeros(n, dtype=int)
            run_len[0] = 1 if ok[0] else 0
            for i in range(1, n):
                run_len[i] = run_len[i - 1] + 1 if ok[i] else 0

            for i in range(min_days, n):
                if run_len[i] >= min_days:
                    trigger_idx = i + 2
                    if trigger_idx >= len(dates):
                        continue
                    all_signals.append({
                        "代码": code,
                        "名称": code_to_name.get(code, ""),
                        "触发日": dates[trigger_idx],
                        "连续天数": run_len[i],
                    })

        df = pd.DataFrame(all_signals)
        if len(df) > 0:
            df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只股票")
        return df

    # ---------- 每日买卖决策 ----------

    def process_day(self, ctx: DayContext):
        new_positions = []
        today = ctx.today_signals
        random.shuffle(today)

        for s in today:
            # 仓位上限 90%
            if ctx.today_start_cash > 0 and \
               (ctx.today_start_cash - ctx.cash) / ctx.today_start_cash >= 0.90:
                break

            key = (s["代码"], ctx.date)
            if key not in ctx.kline_idx:
                continue
            high, low, close, open_ = ctx.kline_idx[key]

            # 买入过滤
            if open_ <= 0:
                continue
            drop_pct = (open_ - close) / open_ * 100
            if drop_pct < 2 or drop_pct > 5:
                continue
            if s.get("连续天数", 5) > 8:
                continue

            bp = close
            lots = compute_lots(ctx.cash, bp, 0.5, ctx.commission_rate)
            if not lots:
                continue
            shares = lots * 100
            cost = shares * bp * (1 + ctx.commission_rate)
            if cost > ctx.cash:
                continue
            ctx.cash -= cost  # 引擎依赖 ctx.cash 跟踪资金

            new_positions.append(Position(
                code=s["代码"], shares=shares, buy_price=bp,
                total_cost=cost,
                target_price=self.get_target_price(bp, low, ctx.target_pct),
                buy_date=ctx.date, buy_day_low=low,
                streak_days=s.get("连续天数", 5),
            ))

        return new_positions


# ====================================================================
# CLI（一行启动）
# ====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="持续推高买入模拟")
    parser.add_argument("--capital", type=float, default=50000)
    parser.add_argument("-n", type=int, default=5, help="连续推高天数")
    parser.add_argument("-m", type=float, default=1.0, help="止盈目标%%")
    parser.add_argument("--gap", type=float, default=1.5, help="每日推高幅度%%")
    parser.add_argument("--commission", type=float, default=1.0, help="佣金万分之")
    parser.add_argument("--start", type=str, default="2026-03-01", help="起始日期")
    args = parser.parse_args()

    t0 = time.time()
    engine = SimEngine(UpwardGapStrategy())
    engine.run(
        capital=args.capital, start_date=args.start,
        target_pct=args.m, commission_rate=args.commission / 10000,
        output_html=OUTPUT_HTML,
        min_days=args.n, gap_pct=args.gap,
    )
    print(f"\n总耗时: {time.time() - t0:.0f}秒")
