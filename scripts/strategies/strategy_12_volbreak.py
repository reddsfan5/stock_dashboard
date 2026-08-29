#!/usr/bin/env python3
"""策略12: 放量突破前高 — 突破买入法。结果: -10% / 103笔 / 胜率46%"""
import argparse, os, sys, time
import numpy as np, pandas as pd
from tqdm import tqdm

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from data.etf import ETFData
from backtest.sim_types import Trade, EquityPoint
from backtest.sim_core import print_stats
from backtest.renderer import render_dip_buy_report

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_12.html")


def run(capital=50000, start_date="2022-01-01", hold_days=20, top_n=5):
    stock = StockData()
    etf = ETFData()
    cache = pd.concat([stock.cache, etf.cache_with_prefix], ignore_index=True)
    cache = cache[cache["代码"].str.startswith(("sh5", "sz1"))]
    all_dates = sorted(cache["日期"].unique())
    start = pd.Timestamp(start_date)
    dates = [d for d in all_dates if d >= start][::hold_days]

    cash = capital
    pos = {}
    trades = []
    eq = []

    for date in tqdm(dates, desc="放量突破"):
        # 卖出
        for c, p in list(pos.items()):
            r = cache[(cache["代码"] == c) & (cache["日期"] == date)]
            if len(r) > 0:
                sp = float(r["收盘"].iloc[0])
                cash += p["shares"] * sp * 0.9994
                trades.append(Trade(
                    code=c, name="", buy_date=str(p["date"])[:10],
                    buy_price=p["bp"], sell_date=str(date)[:10],
                    sell_price=round(sp, 2),
                    return_pct=round((p["shares"]*sp*0.9994 - p["cost"]) / p["cost"] * 100, 2),
                    pnl=round(p["shares"]*sp*0.9994 - p["cost"], 2),
                    filled=True, lots=p["shares"] // 100))
            del pos[c]

        # 选股: 突破20日高点+放量1.5倍
        prev = cache[(cache["日期"] < date) & (cache["日期"] >= date - pd.Timedelta(days=60))]
        rets = {}
        for c in prev["代码"].unique():
            g = prev[prev["代码"] == c].sort_values("日期")
            n = len(g)
            if n < 25:
                continue
            cl = g["收盘"].values
            hi = g["最高"].values
            vo = g["成交额"].values
            h20 = np.max(hi[-21:-1])
            if cl[-1] <= h20 * 1.005:
                continue
            if vo[-1] <= np.mean(vo[-21:-1]) * 1.5:
                continue
            rets[c] = vo[-1] / np.mean(vo[-21:-1])
        top = sorted(rets, key=rets.get, reverse=True)[:top_n]
        if not top:
            continue
        per = cash / len(top)
        for c in top:
            r = cache[(cache["代码"] == c) & (cache["日期"] == date)]
            if len(r) == 0:
                continue
            bp = float(r["收盘"].iloc[0])
            lots = int(per / (bp * 100 * 1.0003))
            if lots <= 0:
                continue
            cost = lots * 100 * bp * 1.0003
            if cost > cash:
                continue
            cash -= cost
            pos[c] = {"shares": lots * 100, "cost": cost, "bp": bp, "date": date}
        pv = sum(p["shares"] * p["bp"] for p in pos.values())
        eq.append(EquityPoint(date=str(date)[:10], equity=cash + pv,
                               cash=cash, positions=len(pos)))

    # 清仓
    ld = all_dates[-1]
    for c, p in list(pos.items()):
        r = cache[(cache["代码"] == c) & (cache["日期"] == ld)]
        if len(r) > 0:
            sp = float(r["收盘"].iloc[0])
            cash += p["shares"] * sp * 0.9994
            trades.append(Trade(
                code=c, name="", buy_date=str(p["date"])[:10],
                buy_price=p["bp"], sell_date=str(ld)[:10],
                sell_price=round(sp, 2),
                return_pct=round((p["shares"]*sp*0.9994 - p["cost"]) / p["cost"] * 100, 2),
                pnl=round(p["shares"]*sp*0.9994 - p["cost"], 2),
                filled=False, lots=p["shares"] // 100))

    print_stats(trades, cash, capital, start_date, str(ld)[:10],
                title="放量突破前高",
                extra_info=f"突破20日高点+量>均量1.5倍 | {hold_days}天×{top_n}只")
    html = render_dip_buy_report(
        capital=capital, target_pct=0, overlap_pct=0,
        commission_rate=0.0003, stamp_tax=0.0003, start_date=start_date,
        lookback=hold_days, board="etf", max_gain=99, limit_down=99,
        max_range_20d=99, trades=trades, equity_curve=eq, final_equity=cash,
        kline_map={},
        title="📉 策略12：放量突破20日高点",
        subtitle="收盘突破20日最高+量>均量1.5倍 · 20天×5只ETF",
        params_html="💡 <b>选股：</b>收盘价突破20日最高价 + 成交量>20日均量1.5倍<br>💡 <b>买入：</b>调仓日收盘价，等权分配<br>💡 <b>卖出：</b>持有20天到期换仓<br>💡 <b>结果：</b>-10% ❌ — 有效突破需要更大的量能和板块共振确认")
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w") as f:
        f.write(html)
    print(f"✓ HTML: {OUTPUT_HTML}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--capital", type=float, default=50000)
    p.add_argument("--start", type=str, default="2022-01-01")
    a = p.parse_args()
    t0 = time.time()
    run(a.capital, a.start)
    print(f"耗时: {time.time() - t0:.0f}秒")
