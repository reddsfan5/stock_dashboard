#!/usr/bin/env python3
"""
策略6: ETF动量 + 止损 — 每周买动量最强5只ETF，+5%止盈/-3%止损

结果: -16.7% / 332笔 / 胜率32.5%
教训: 即使ETF波动小于个股，3%止损仍然频繁触发，不止损才是正解
"""

import argparse, os, sys, time
import numpy as np, pandas as pd
from tqdm import tqdm
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
from backtest.strategy import SimStrategy, DayContext
from backtest.sim_types import Position
from backtest.sim_core import compute_lots
from backtest.sim_engine import SimEngine
OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_06.html")

class ETFStopStrategy(SimStrategy):
    def get_board_filter(self): return ("sh", "sz")

    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
        cache = cache.sort_values(["代码", "日期"])
        all_dates = sorted(cache["日期"].unique())
        mondays = [d for d in all_dates if d.dayofweek == 0 and d >= pd.Timestamp(start_date)]
        all_signals = []
        for monday in tqdm(mondays, desc="扫描", unit="周"):
            prev = cache[(cache["日期"] < monday) & (cache["日期"] >= monday - pd.Timedelta(days=20))]
            if len(prev) == 0: continue
            rets = {}
            for c, g in prev.groupby("代码"):
                if len(g) < 10: continue
                g = g.sort_values("日期"); cl = g["收盘"].values
                if cl[-1] <= 0 or cl[0] <= 0: continue
                r = (cl[-1] - cl[0]) / cl[0] * 100
                if -20 < r < 40: rets[c] = r
            for c in sorted(rets, key=rets.get, reverse=True)[:5]:
                all_signals.append({"代码": c, "名称": code_to_name.get(c,""), "触发日": monday})
        df = pd.DataFrame(all_signals)
        if len(df) > 0: df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只")
        return df

    def process_day(self, ctx: DayContext):
        new = []
        for s in ctx.today_signals[:5]:
            if ctx.today_start_cash > 0 and (ctx.today_start_cash - ctx.cash) / ctx.today_start_cash >= 0.90: break
            key = (s["代码"], ctx.date)
            if key not in ctx.kline_idx: continue
            h, l, c, o = ctx.kline_idx[key]
            if c <= 0: continue
            lots = compute_lots(ctx.cash, c, 0.2, ctx.commission_rate)
            if not lots or lots < 1: continue
            sh = lots * 100; cost = sh * c * (1 + ctx.commission_rate)
            if cost > ctx.cash: continue
            ctx.cash -= cost
            new.append(Position(code=s["代码"], shares=sh, buy_price=c, total_cost=cost, target_price=round(c*1.05,2), stop_price=round(c*0.97,2), max_hold_days=5, holding_days=1, buy_date=ctx.date, buy_day_low=l))
        return new


    def get_report_meta(self):
        return {"title": "📉 策略6：ETF动量 + 止损保护", "subtitle": "每周买近10日最强5只ETF · +5%止盈 · -3%止损", "params_html": """💡 <b>选股：</b>全市场ETF按近10日涨幅排名，取前5<br>💡 <b>买入：</b>周一收盘价，等权分配<br>💡 <b>卖出：</b>+5%止盈 / -3%止损 / 5天到期强平<br>💡 <b>结果：</b>-17% ❌ — 3%止损在周频仍然频繁触发"""}

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--capital", type=float, default=50000); p.add_argument("--start", type=str, default="2025-01-01")
    a = p.parse_args()
    # 加载ETF数据
    from data.kline import StockData; from data.etf import ETFData
    stock = StockData(); etf = ETFData()
    stock._cache = pd.concat([stock.cache, etf.cache_with_prefix], ignore_index=True)
    t0 = time.time()
    SimEngine(ETFStopStrategy()).run(capital=a.capital, start_date=a.start, target_pct=5.0, commission_rate=0.0001, output_html=OUTPUT_HTML, data=stock)
    print(f"耗时: {time.time()-t0:.0f}秒")
