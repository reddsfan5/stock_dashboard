#!/usr/bin/env python3
"""
策略14: 低波强势 — 买涨得最稳的强势股（低波动+强趋势）

理念：强势股中波动最小的，往往是机构锁仓慢牛的品种，回撤小、持续性好。

  选股: MA150>MA200 + 距52周高点>70% + 日均量>5000万 + 近20日振幅<15%
  排序: 近20日动量（涨得稳不快于急涨）
  买入: 月频调仓，等权5只
  卖出: 持有20天到期换仓

用法:
  python -m scripts.strategies.strategy_14_lowvol
"""

import argparse, os, sys, time, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from backtest.indicators import compute_all
from backtest.sim_types import Trade, EquityPoint
from backtest.sim_core import print_stats, collect_kline_for_trades
from backtest.renderer import render_dip_buy_report

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_14.html")
MAIN = ("sh600","sh601","sh603","sh605","sz000","sz001","sz002","sz003")


def run(capital=50000, start_date="2022-01-01", hold_days=20, top_n=5):
    print("计算全市场指标...")
    stock = StockData()
    ind = compute_all(stock.cache, MAIN)
    cp = ind['close']
    start = pd.Timestamp(start_date)
    date_indices = [i for i, d in enumerate(cp.index) if d >= start][::hold_days]
    if not date_indices:
        print("无交易日"); return

    cash = capital; pos = {}; trades = []; eq = []

    for idx in date_indices:
        date = cp.index[idx]
        # sell
        for c, p in list(pos.items()):
            sp = cp.iloc[idx].get(c)
            if pd.notna(sp):
                net = p["shares"] * float(sp) * 0.9994
                cash += net
                trades.append(Trade(
                    code=c, name="", buy_date=str(p["date"])[:10],
                    buy_price=p["bp"], sell_date=str(date)[:10],
                    sell_price=round(float(sp), 2),
                    return_pct=round((net - p["cost"]) / p["cost"] * 100, 2),
                    pnl=round(net - p["cost"], 2), filled=True,
                    lots=p["shares"] // 100))
            del pos[c]

        # filter: 强趋势 + 低波动
        strong = (ind['close'].iloc[idx] > ind['ma150'].iloc[idx]) & \
                 (ind['ma150'].iloc[idx] > ind['ma200'].iloc[idx])
        nh = ind['near_high_250'].iloc[idx] > 0.7
        vol_ok = ind['vol_ratio_20'].iloc[idx].notna() & (ind['vol_ratio_20'].iloc[idx] > 0.3)
        low_amp = ind['amp20'].iloc[idx] < 15
        score = ind['mom20'].iloc[idx]  # 涨得稳的优先

        mask = strong & nh & vol_ok & low_amp & score.notna() & (score > -5)
        valid = score[mask].dropna().nlargest(top_n)
        if len(valid) == 0: continue

        per = cash / len(valid)
        for c in valid.index:
            bp = float(cp.iloc[idx][c])
            lots = int(per / (bp * 100 * 1.0003))
            if lots <= 0: continue
            cost = lots * 100 * bp * 1.0003
            if cost > cash: continue
            cash -= cost
            pos[c] = {"shares": lots * 100, "cost": cost, "bp": bp, "date": date}

        pv = sum(p["shares"] * p["bp"] for p in pos.values())
        eq.append(EquityPoint(date=str(date)[:10], equity=cash + pv,
                               cash=cash, positions=len(pos)))

    # close
    last_idx = len(cp) - 1
    ld = cp.index[last_idx]
    for c, p in list(pos.items()):
        sp = cp.iloc[last_idx].get(c)
        if pd.notna(sp):
            net = p["shares"] * float(sp) * 0.9994; cash += net
            trades.append(Trade(code=c, name="", buy_date=str(p["date"])[:10],
                buy_price=p["bp"], sell_date=str(ld)[:10],
                sell_price=round(float(sp), 2),
                return_pct=round((net - p["cost"]) / p["cost"] * 100, 2),
                pnl=round(net - p["cost"], 2), filled=False,
                lots=p["shares"] // 100))

    print_stats(trades, cash, capital, start_date, str(ld)[:10],
                title="低波强势选股",
                extra_info=f"MA150>MA200+近52w高+振幅<15% | {hold_days}天×{top_n}只")

    print("收集K线...")
    kline_map = collect_kline_for_trades(
        type("D", (), {"cache": stock.cache})(), trades, {}, pivots=ind)
    print(f"  ✓ {len(kline_map)} 条")

    html = render_dip_buy_report(capital=capital, target_pct=0, overlap_pct=0,
        commission_rate=0.0003, stamp_tax=0.0003, start_date=start_date,
        lookback=hold_days, board="main", max_gain=99, limit_down=99,
        max_range_20d=99, trades=trades, equity_curve=eq, final_equity=cash,
        kline_map=kline_map,
        title="📈 策略14：低波强势选股",
        subtitle="MA150>MA200强势股+近20日振幅<15% → 月频等权5只",
        params_html="💡 <b>选股：</b>MA150>MA200多头排列 + 距52周高点>70% + 日均量>5000万 + 近20日振幅<15%<br>💡 <b>排序：</b>近20日动量——涨得稳的优先，不急涨<br>💡 <b>调仓：</b>月频，等权分配<br>💡 <b>理念：</b>低波动+强趋势=机构锁仓慢牛——回撤小、持续性好")
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w") as f: f.write(html)
    print(f"✓ HTML: {OUTPUT_HTML}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--capital", type=float, default=50000)
    p.add_argument("--start", type=str, default="2022-01-01")
    args = p.parse_args()
    t0 = time.time()
    run(args.capital, args.start)
    print(f"耗时: {time.time() - t0:.0f}秒")
