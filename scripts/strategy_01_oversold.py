#!/usr/bin/env python3
"""
策略1: 超跌反弹 T+1 — 连续下跌后博反弹，带止损

结果: -93% / 2864笔 / 胜率49.9%
教训: 日线噪声太大，连续下跌是趋势信号不是反弹信号
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
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_01.html")

class OversoldStrategy(SimStrategy):
    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
        cache = cache.sort_values(["代码", "日期"])
        cache["涨跌"] = cache.groupby("代码")["收盘"].pct_change()
        all_signals = []
        for code, grp in tqdm(cache.groupby("代码"), desc="扫描", unit="只"):
            if len(grp) < 8: continue
            ch = grp["涨跌"].values; cl = grp["收盘"].values; dt = grp["日期"].values
            down = ch < 0; n = len(down)
            run = np.zeros(n, dtype=int); run[0] = 1 if down[0] else 0
            for i in range(1, n): run[i] = run[i-1]+1 if down[i] else 0
            for i in range(3, n):
                if run[i] >= 3:
                    ti = i + 1
                    if ti >= len(dt): continue
                    sc = cl[i - run[i] + 1]
                    if sc <= 0: continue
                    drop = (sc - cl[i]) / sc * 100
                    if drop < 3 or drop > 12: continue
                    all_signals.append({"代码": code, "名称": code_to_name.get(code,""), "触发日": dt[ti], "连续下跌": run[i], "累计跌幅": round(drop, 2)})
        df = pd.DataFrame(all_signals)
        if len(df) > 0: df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只")
        return df

    def process_day(self, ctx: DayContext):
        new = []; today = ctx.today_signals; random.shuffle(today)
        for s in today:
            if ctx.today_start_cash > 0 and (ctx.today_start_cash - ctx.cash) / ctx.today_start_cash >= 0.90: break
            key = (s["代码"], ctx.date)
            if key not in ctx.kline_idx: continue
            h, l, c, o = ctx.kline_idx[key]
            if c <= 0: continue
            lots = compute_lots(ctx.cash, c, 0.5, ctx.commission_rate)
            if not lots: continue
            sh = lots * 100; cost = sh * c * (1 + ctx.commission_rate)
            if cost > ctx.cash: continue
            ctx.cash -= cost
            new.append(Position(code=s["代码"], shares=sh, buy_price=c, total_cost=cost, target_price=round(c*1.01,2), stop_price=round(c*0.98,2), buy_date=ctx.date, buy_day_low=l, streak_days=s.get("连续下跌",0)))
        return new


    def get_report_meta(self):
        return {"title": "📉 策略1：超跌反弹 T+1", "subtitle": "连续下跌后博次日反弹 · +1%止盈 · -2%止损", "params_html": """💡 <b>选股：</b>连续≥3天下跌 + 累计跌幅3~12%<br>💡 <b>买入：</b>次日收盘价，半仓，每天最多90%仓位<br>💡 <b>卖出：</b>次日 +1%止盈 / -2%止损 / 尾盘强平<br>💡 <b>结果：</b>-93% ❌ — A股连跌是趋势延续不是反转信号"""}

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--capital", type=float, default=50000); p.add_argument("--start", type=str, default="2024-01-01")
    a = p.parse_args(); t0 = time.time()
    SimEngine(OversoldStrategy()).run(capital=a.capital, start_date=a.start, target_pct=1.0, commission_rate=0.0001, output_html=OUTPUT_HTML)
    print(f"耗时: {time.time()-t0:.0f}秒")
