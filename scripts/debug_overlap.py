#!/usr/bin/env python3
"""
单股票 Overlap 策略调试 — 逐笔展示条件判断，HTML 复用统一模板

用法：
  python scripts/debug_overlap.py                              # 比亚迪，默认参数
  python scripts/debug_overlap.py --code sh600519               # 茅台
  python scripts/debug_overlap.py --days 3 --pct 2.0            # 连续3天重叠>2%
"""

import argparse, os, sys, time

import numpy as np
import pandas as pd

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from backtest.sim_types import Trade, EquityPoint
from backtest.sim_core import collect_kline_for_trades
from backtest.renderer import render_dip_buy_report

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "overlap_debug.html")


def run_debug(code="sz002594", condition_days=5, overlap_pct=3.0,
              realistic=False, target_pct=1.0):
    data = StockData()
    name = data.get_stock_name(code)
    cache = data.cache[data.cache["代码"] == code].sort_values("日期").copy()
    if len(cache) == 0:
        print(f"✗ {code} 无缓存数据"); return

    cache = cache.reset_index(drop=True)
    print(f"\n{'='*70}")
    print(f"  {code} {name}  Overlap 策略调试")
    print(f"  条件: 连续 ≥{condition_days} 天 K线重叠区 > {overlap_pct}%")
    print(f"  模式: {'真实挂单' if realistic else '理想(买低卖高)'}")
    print(f"{'='*70}")

    # ---- 计算每日重叠区 ----
    highs = cache["最高"].values
    lows = cache["最低"].values
    closes = cache["收盘"].values
    dates = cache["日期"].values

    overlaps = []
    for i in range(1, len(cache)):
        oh = min(highs[i-1], highs[i])
        ol = max(lows[i-1], lows[i])
        pct = (oh - ol) / closes[i-1] * 100
        overlaps.append({
            "idx": i, "date": dates[i], "prev_date": dates[i-1],
            "overlap_high": oh, "overlap_low": ol,
            "overlap_pct": round(pct, 2),
            "overlap_ok": pct > overlap_pct,
        })

    # ---- 收集信号 + 终端输出 ----
    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []

    for i in range(condition_days - 1, len(overlaps)):
        streak = overlaps[i - condition_days + 1: i + 1]
        if not all(s["overlap_ok"] for s in streak):
            continue

        # 往前追溯完整连续重叠
        streak_start = i - condition_days + 1
        while streak_start > 0 and overlaps[streak_start - 1]["overlap_ok"]:
            streak_start -= 1
        full_streak = overlaps[streak_start: i + 1]
        streak_len = len(full_streak)

        buy_idx = overlaps[i]["idx"]
        sell_idx = buy_idx + 1
        if sell_idx >= len(cache):
            continue

        # ---- 终端输出（保留原有详细调试信息） ----
        n = len(trades) + 1
        first_date = pd.Timestamp(full_streak[0]["date"]).strftime("%m/%d")
        last_date = pd.Timestamp(full_streak[-1]["date"]).strftime("%m/%d")
        buy_date = dates[buy_idx]
        sell_date = dates[sell_idx]

        print(f"\n{'─'*70}")
        print(f"  🎯 信号 #{n}  |  连续重叠 {streak_len} 天 ({first_date}→{last_date})")
        print(f"        买入日: {pd.Timestamp(buy_date).strftime('%Y-%m-%d')}")
        print(f"{'─'*70}")
        print(f"  ┌─ 重叠区详情 ─────────────────────────────────────┐")
        for s in full_streak[-min(10, len(full_streak)):]:
            marker = "✓" if s["overlap_ok"] else "✗"
            print(f"  │ {marker} {pd.Timestamp(s['date']).strftime('%m/%d')}  "
                  f"重叠区[{s['overlap_high']:.2f}-{s['overlap_low']:.2f}] "
                  f"= {s['overlap_pct']:+.1f}%")
        print(f"  └──────────────────────────────────────────────────┘")

        buy_price = lows[buy_idx]
        if realistic:
            target_price = round(buy_price * (1 + target_pct / 100), 2)
            actual_high = highs[sell_idx]
            actual_close = closes[sell_idx]
            if actual_high >= target_price:
                sell_price = target_price
                filled = True
                sell_note = f"触及目标 {target_price:.2f}"
            else:
                sell_price = actual_close
                filled = False
                sell_note = f"未触及({actual_high:.2f}<{target_price:.2f}) 尾盘 {actual_close:.2f}"
        else:
            sell_price = highs[sell_idx]
            filled = True
            sell_note = ""
        gain = round((sell_price - buy_price) / buy_price * 100, 2)

        color = "🟢" if gain > 0 else "🔴"
        if realistic:
            print(f"  📈 买: {pd.Timestamp(buy_date).strftime('%Y-%m-%d')} 低 {buy_price:.2f}  "
                  f"🎯 挂单 {buy_price*(1+target_pct/100):.2f}")
            print(f"  📉 卖: {pd.Timestamp(sell_date).strftime('%Y-%m-%d')} {sell_note}")
        else:
            print(f"  📈 买: {pd.Timestamp(buy_date).strftime('%Y-%m-%d')} 低 {buy_price:.2f}  "
                  f"📉 卖: {pd.Timestamp(sell_date).strftime('%Y-%m-%d')} 高 {sell_price:.2f}")
        print(f"  {color} 收益: {gain:+.2f}%")

        # 构造 Trade（复用统一模板）
        trades.append(Trade(
            code=code, name=name,
            buy_date=pd.Timestamp(buy_date).strftime("%Y-%m-%d"),
            buy_price=round(buy_price, 2),
            sell_date=pd.Timestamp(sell_date).strftime("%Y-%m-%d"),
            sell_price=round(sell_price, 2),
            return_pct=gain, pnl=gain,  # pnl 存收益率用于统计展示
            filled=filled, lots=1, streak_days=streak_len,
        ))
        equity_curve.append(EquityPoint(
            date=pd.Timestamp(buy_date).strftime("%Y-%m-%d"),
            equity=n * 1000, cash=0, positions=0))

    # ---- 汇总 ----
    print(f"\n{'='*70}")
    if trades:
        gains = [t.return_pct for t in trades]
        wins = sum(1 for t in trades if t.return_pct > 0)
        print(f"  信号: {len(trades)} 次 | 胜率: {wins}/{len(trades)} = {wins/len(trades)*100:.1f}%")
        print(f"  平均收益: {np.mean(gains):+.2f}% | 最大: {max(gains):+.2f}% | 最小: {min(gains):+.2f}%")
    print(f"  数据: {pd.Timestamp(dates[0]).strftime('%Y-%m-%d')} ~ {pd.Timestamp(dates[-1]).strftime('%Y-%m-%d')} ({len(cache)}天)")
    print(f"{'='*70}")

    # ---- HTML（复用统一模板） ----
    kline_map = collect_kline_for_trades(data, trades, {code: name})
    html = render_dip_buy_report(
        capital=1, target_pct=target_pct, overlap_pct=overlap_pct,
        commission_rate=0, stamp_tax=0,
        start_date=pd.Timestamp(dates[0]).strftime("%Y-%m-%d"),
        lookback=condition_days, board="stock",
        max_gain=99, limit_down=99, max_range_20d=99,
        trades=trades, equity_curve=equity_curve,
        final_equity=0, kline_map=kline_map,
    )
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✓ HTML: {OUTPUT_HTML}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Overlap 策略单股调试")
    parser.add_argument("--code", type=str, default="sz002594")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--pct", type=float, default=3.0)
    parser.add_argument("--realistic", action="store_true", help="真实挂单模式")
    parser.add_argument("--target", type=float, default=1.0, help="挂单目标%%")
    args = parser.parse_args()

    t0 = time.time()
    run_debug(code=args.code, condition_days=args.days, overlap_pct=args.pct,
              realistic=args.realistic, target_pct=args.target)
    print(f"\n总耗时: {time.time() - t0:.0f}秒")
