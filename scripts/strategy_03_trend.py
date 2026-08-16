#!/usr/bin/env python3
"""
策略3: 均线多头排列趋势跟踪 — MA5>MA10>MA20 + 量增, 持仓不限

结果: -18% / 398笔 / 胜率28.6%
教训: 均线多头是滞后信号，买入时往往已是阶段性高点
"""

import argparse, os, sys, time, random
import numpy as np, pandas as pd
from tqdm import tqdm
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
from backtest.strategy import SimStrategy, DayContext
from backtest.sim_types import Position
from backtest.sim_core import compute_lots
from backtest.sim_engine import SimEngine
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_03.html")

class TrendFollowStrategy(SimStrategy):
    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
        cache = cache.sort_values(["代码", "日期"])
        all_signals = []
        for code, grp in tqdm(cache.groupby("代码"), desc="扫描", unit="只"):
            if len(grp) < 30: continue
            cl = grp["收盘"].values; vol = grp["成交额"].values; dt = grp["日期"].values
            ma5 = pd.Series(cl).rolling(5).mean().values
            ma10 = pd.Series(cl).rolling(10).mean().values
            ma20 = pd.Series(cl).rolling(20).mean().values
            for i in range(25, len(grp)):
                if ma5[i] <= ma10[i] or ma10[i] <= ma20[i]: continue
                if cl[i] <= ma5[i]: continue
                if not (vol[i] > vol[i-1] > vol[i-2]): continue
                if i + 1 >= len(grp): continue
                all_signals.append({"代码": code, "名称": code_to_name.get(code,""), "触发日": dt[i+1]})
        df = pd.DataFrame(all_signals)
        if len(df) > 0: df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只")
        return df

    def process_day(self, ctx: DayContext):
        new = []; today = ctx.today_signals; random.shuffle(today)
        for s in today[:5]:
            if ctx.today_start_cash > 0 and (ctx.today_start_cash - ctx.cash) / ctx.today_start_cash >= 0.80: break
            key = (s["代码"], ctx.date)
            if key not in ctx.kline_idx: continue
            h, l, c, o = ctx.kline_idx[key]
            if c <= 0: continue
            lots = compute_lots(ctx.cash, c, 0.2, ctx.commission_rate)
            if not lots or lots < 1: continue
            sh = lots * 100; cost = sh * c * (1 + ctx.commission_rate)
            if cost > ctx.cash: continue
            ctx.cash -= cost
            new.append(Position(code=s["代码"], shares=sh, buy_price=c, total_cost=cost, target_price=round(c*1.08,2), stop_price=round(c*0.96,2), buy_date=ctx.date, buy_day_low=l))
        return new


    def get_report_meta(self):
        return {"title": "📉 策略3：均线多头排列趋势跟踪", "subtitle": "MA5>MA10>MA20三线开花 · +8%止盈 · -4%止损", "params_html": """💡 <b>选股：</b>MA5>MA10>MA20 + 收盘>MA5 + 近3日量增<br>💡 <b>买入：</b>次日收盘价，20%仓位，每天最多5只<br>💡 <b>卖出：</b>+8%止盈 / -4%止损，不限持仓天数<br>💡 <b>结果：</b>-18% ❌ — 均线滞后，追在阶段高点"""}

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--capital", type=float, default=50000); p.add_argument("--start", type=str, default="2025-01-01")
    a = p.parse_args(); t0 = time.time()
    SimEngine(TrendFollowStrategy()).run(capital=a.capital, start_date=a.start, target_pct=8.0, commission_rate=0.0001, output_html=OUTPUT_HTML)
    print(f"耗时: {time.time()-t0:.0f}秒")
