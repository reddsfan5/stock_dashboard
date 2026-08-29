#!/usr/bin/env python3
"""
策略13: 强趋势回调买入 — 只在MA150>MA200的强势股中，等缩量回踩时入场

Minervini式回调买入的量化版：
  选股: MA150>MA200 + 距52周高点>70% + 量>5000万
       + 近5日回调0~8%（不能太浅也不能太深）
       + 缩量（量<20日均量70%）
  买入: 月频调仓，等权5只
  卖出: 持有20天到期换仓

用法:
  python -m scripts.strategies.strategy_13_pullback
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

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "strategy_13.html")
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
    code_to_name = {}
    info = stock.cache[["代码"]].drop_duplicates()
    for _, r in info.iterrows():
        code_to_name[r["代码"]] = ""

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

        # filter: 强趋势 + 回调 + 缩量
        strong = (ind['close'].iloc[idx] > ind['ma150'].iloc[idx]) & \
                 (ind['ma150'].iloc[idx] > ind['ma200'].iloc[idx])
        nh = ind['near_high_250'].iloc[idx] > 0.7
        vol_ok = ind['vol_ratio_20'].iloc[idx].notna() & (ind['vol_ratio_20'].iloc[idx] > 0.3)
        pullback = (ind['mom5'].iloc[idx] < 0) & (ind['mom5'].iloc[idx] > -8)
        shrink = ind['vol_ratio_20'].iloc[idx] < 0.7
        score = -ind['mom5'].iloc[idx]  # 回调幅度适中

        mask = strong & nh & vol_ok & pullback & shrink & score.notna()
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
                title="强趋势回调买入",
                extra_info=f"MA150>MA200+近高点+缩量回调 | {hold_days}天×{top_n}只")

    # K线（快速路径：透视表切片）
    print("收集K线...")
    kline_map = collect_kline_for_trades(
        type("D", (), {"cache": stock.cache})(), trades, {}, pivots=ind)
    print(f"  ✓ {len(kline_map)} 条")

    html = render_dip_buy_report(capital=capital, target_pct=0, overlap_pct=0,
        commission_rate=0.0003, stamp_tax=0.0003, start_date=start_date,
        lookback=hold_days, board="main", max_gain=99, limit_down=99,
        max_range_20d=99, trades=trades, equity_curve=eq, final_equity=cash,
        kline_map=kline_map,
        title="📈 策略13：强趋势回调买入",
        subtitle="MA150>MA200强势股 + 缩量回踩5日 → 月频等权5只",
        params_html="💡 <b>选股：</b>MA150>MA200多头排列 + 距52周高点>70% + 日均量>5000万<br>💡 <b>入场：</b>近5日回调0~8% + 量缩至20日均量70%以下（洗盘确认）<br>💡 <b>调仓：</b>月频，等权分配，回调幅度适中的优先<br>💡 <b>理念：</b>只在最强趋势股回调时买——Minervini式低吸")
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
