#!/usr/bin/env python3
"""
策略5: 超卖均值回归 + 5天持仓限制 — 同策略4信号，但最多持有5天

结果: -10.9% / 502笔 / 胜率36.5%
教训: 5天窗口太短，均值回归不一定在5天内发生，到期强平=被动止损
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
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_05.html")

class LimitedHoldStrategy(SimStrategy):
    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
        cache = cache.sort_values(["代码", "日期"])
        all_signals = []
        for code, grp in tqdm(cache.groupby("代码"), desc="扫描", unit="只"):
            if len(grp) < 25: continue
            cl = grp["收盘"].values; hi = grp["最高"].values; lo = grp["最低"].values
            op = grp["开盘"].values if "开盘" in grp.columns else cl; dt = grp["日期"].values
            for i in range(22, len(grp)):
                h20 = np.max(hi[i-20:i]); l20 = np.min(lo[i-20:i])
                if l20 <= 0: continue
                amp = (h20 - l20) / l20 * 100
                if amp < 10 or amp > 35: continue
                pos = (cl[i] - l20) / l20 * 100
                if pos > 3: continue
                if cl[i] <= op[i]: continue
                if i + 1 >= len(grp): continue
                all_signals.append({"代码": code, "名称": code_to_name.get(code,""), "触发日": dt[i+1]})
        df = pd.DataFrame(all_signals)
        if len(df) > 0: df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只")
        return df

    def process_day(self, ctx: DayContext):
        new = []; today = list(ctx.today_signals); ctx.rng.shuffle(today)
        for s in today[:5]:
            if ctx.today_start_cash > 0 and (ctx.today_start_cash - ctx.cash) / ctx.today_start_cash >= 0.80: break
            key = (s["代码"], ctx.date)
            if key not in ctx.kline_idx: continue
            h, l, c, o = ctx.kline_idx[key]
            if c <= 0: continue
            lots = compute_lots(ctx.cash, c, 0.2, ctx.commission_rate, ctx.min_commission)
            if not lots or lots < 1: continue
            sh = lots * 100; cost = buy_total_cost(c, sh, ctx.commission_rate, ctx.min_commission)
            if cost > ctx.cash: continue
            ctx.cash -= cost
            new.append(Position(code=s["代码"], shares=sh, buy_price=c, total_cost=cost, target_price=round(c*1.05,2), stop_price=round(c*0.97,2), max_hold_days=5, holding_days=1, buy_date=ctx.date, buy_day_low=l))
        return new


    def get_report_meta(self):
        return {"title": "📉 策略5：均值回归 + 5天持仓限制", "subtitle": "同策略4信号但持仓≤5天到期强平", "params_html": """💡 <b>选股：</b>同策略4（20日低点+收阳+振幅适中）<br>💡 <b>买入：</b>次日收盘价，20%仓位，每天最多5只<br>💡 <b>卖出：</b>+5%止盈 / -3%止损 / 5天到期强平<br>💡 <b>结果：</b>-11% ❌ — 5天窗口太短，反弹还没来"""}

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--capital", type=float, default=50000); p.add_argument("--start", type=str, default="2025-01-01")
    a = p.parse_args(); t0 = time.time()
    SimEngine(LimitedHoldStrategy()).run(capital=a.capital, start_date=a.start, target_pct=5.0, commission_rate=0.0001, output_html=OUTPUT_HTML)
    print(f"耗时: {time.time()-t0:.0f}秒")
