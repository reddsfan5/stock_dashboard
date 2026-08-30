#!/usr/bin/env python3
"""
策略2: 缩量横盘后放量突破 T+1

结果: -38% / 793笔 / 胜率42.7%
教训: A股假突破多，一日游为主，量价过滤不够区分真假突破
"""

import argparse, os, sys, time
import numpy as np, pandas as pd
from tqdm import tqdm
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
from backtest.strategy import SimStrategy, DayContext
from backtest.sim_types import Position
from backtest.sim_core import buy_total_cost, compute_lots
from backtest.sim_engine import SimEngine
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_02.html")

class BreakoutStrategy(SimStrategy):
    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
        cache = cache.sort_values(["代码", "日期"])
        all_signals = []
        for code, grp in tqdm(cache.groupby("代码"), desc="扫描", unit="只"):
            if len(grp) < 25: continue
            cl = grp["收盘"].values; hi = grp["最高"].values; lo = grp["最低"].values
            vol = grp["成交额"].values; dt = grp["日期"].values
            ma20v = pd.Series(vol).rolling(20).mean().values
            for i in range(25, len(grp)):
                if ma20v[i] <= 0: continue
                h5 = np.max(hi[i-5:i]); l5 = np.min(lo[i-5:i])
                amp = (h5 - l5) / l5 * 100
                if amp > 8: continue
                if np.mean(vol[i-5:i]) > ma20v[i] * 0.8: continue
                if cl[i] <= h5 * 0.995: continue
                if vol[i] < ma20v[i] * 1.2: continue
                if i + 1 >= len(grp): continue
                all_signals.append({"代码": code, "名称": code_to_name.get(code,""), "触发日": dt[i+1]})
        df = pd.DataFrame(all_signals)
        if len(df) > 0: df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只")
        return df

    def process_day(self, ctx: DayContext):
        new = []; today = list(ctx.today_signals); ctx.rng.shuffle(today)
        for s in today:
            if ctx.today_start_cash > 0 and (ctx.today_start_cash - ctx.cash) / ctx.today_start_cash >= 0.90: break
            key = (s["代码"], ctx.date)
            if key not in ctx.kline_idx: continue
            h, l, c, o = ctx.kline_idx[key]
            if c <= 0: continue
            lots = compute_lots(ctx.cash, c, 0.3, ctx.commission_rate, ctx.min_commission)
            if not lots: continue
            sh = lots * 100; cost = buy_total_cost(c, sh, ctx.commission_rate, ctx.min_commission)
            if cost > ctx.cash: continue
            ctx.cash -= cost
            new.append(Position(code=s["代码"], shares=sh, buy_price=c, total_cost=cost, target_price=round(c*1.015,2), stop_price=round(c*0.98,2), buy_date=ctx.date, buy_day_low=l))
        return new


    def get_report_meta(self):
        return {"title": "📉 策略2：缩量横盘后放量突破", "subtitle": "横盘蓄力→放量突破→次日追入 · +1.5%止盈 · -2%止损", "params_html": """💡 <b>选股：</b>前5天振幅<8% + 量缩至20日均量80%以下 + 当天放量突破前高<br>💡 <b>买入：</b>次日收盘价，30%仓位<br>💡 <b>卖出：</b>次日 +1.5%止盈 / -2%止损 / 尾盘强平<br>💡 <b>结果：</b>-38% ❌ — A股假突破太多，一日游为主"""}

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--capital", type=float, default=50000); p.add_argument("--start", type=str, default="2025-01-01")
    a = p.parse_args(); t0 = time.time()
    SimEngine(BreakoutStrategy()).run(capital=a.capital, start_date=a.start, target_pct=1.5, commission_rate=0.0001, output_html=OUTPUT_HTML)
    print(f"耗时: {time.time()-t0:.0f}秒")
