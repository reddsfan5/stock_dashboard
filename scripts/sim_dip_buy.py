#!/usr/bin/env python3
"""
全市场 Overlap + 回撤买入 组合模拟

核心改进（相比 sim_portfolio.py）：
  1. 买入价 = 限价（基于历史最大回撤计算）
     — 触发日往前推 15 天，找到最大单日跌幅
     — 限价 = 前收 × (1 - 最大跌幅%)
     — 当日最低价触及限价才成交，以限价买入
  2. 资金使用更激进：逐只满仓买入，直到当日 90%+ 资金用完
  3. 卖出目标 = 上一日最低价 × (1 + m%)，与买入成本无关
     — 触及则止盈，未触及则尾盘强平

用法：
  python scripts/sim_dip_buy.py
  python scripts/sim_dip_buy.py --capital 500000 --target 2.0 --overlap 4.0
  python scripts/sim_dip_buy.py --capital 100000 --target 1.5 --commission 1.0
"""

import argparse
import os
import sys
import time
from collections import defaultdict

import random
import numpy as np
import pandas as pd
from tqdm import tqdm

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from data.kline import StockData
from data.industry import StockInfo

OUTPUT_HTML = os.path.join(PROJECT_DIR, "output", "dip_buy_sim.html")

# 板块代码前缀规则
MAIN_PREFIXES = (
    "sh600", "sh601", "sh603", "sh605",   # 沪市主板
    "sz000", "sz001", "sz002", "sz003",   # 深市主板
)
GEM_PREFIXES = ("sz300", "sz301")          # 创业板
STAR_PREFIXES = ("sh688",)                 # 科创板
BJ_PREFIX = "bj"                           # 北交所


def _filter_board(codes: list, board: str) -> list:
    """按板块过滤股票代码列表"""
    if board == "all":
        return codes
    if board == "main":
        return [c for c in codes if c.startswith(MAIN_PREFIXES)]
    if board == "gem":
        return [c for c in codes if c.startswith(GEM_PREFIXES)]
    if board == "star":
        return [c for c in codes if c.startswith(STAR_PREFIXES)]
    return codes


# ====================================================================
# 信号扫描（增强版：含回撤买入价计算）
# ====================================================================

def compute_signals(data: StockData, code_to_name: dict,
                    start_date: str = "2026-01-01",
                    min_days: int = 4, max_days: int = 10,
                    overlap_pct: float = 3.0,
                    lookback: int = 15,
                    board: str = "main",
                    max_gain: float = 15.0,
                    limit_down: float = 9.5,
                    max_range_20d: float = 20.0) -> pd.DataFrame:
    """
    扫描全市场 Overlap 信号，同时计算每只信号股的回撤买入价。

    对每只股票：
      1. groupby 后向量化计算每日重叠区%
      2. 滑动窗口检测连续重叠 → 得到触发日
      3. 触发日往前 lookback 天，找最大单日跌幅
      4. 限价买单 = 前收 × (1 - max_decline%)

    Returns:
        DataFrame with columns:
            代码, 名称, 触发日, 前收, 最大跌幅, 买入限价, 重叠天数
    """
    print("准备数据...")
    cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)].copy()
    cache = cache.sort_values(["代码", "日期"])

    # 板块过滤
    if board != "all":
        all_codes = cache["代码"].unique()
        keep = set(_filter_board(list(all_codes), board))
        cache = cache[cache["代码"].isin(keep)]
        print(f"  板块: {board} → {len(keep)} 只")

    # 预计算每只股票的前一日收盘
    cache["前收"] = cache.groupby("代码")["收盘"].shift(1)
    # 预计算每日涨跌幅%: (close - prev_close) / prev_close * 100
    # 正值=上涨，负值=下跌。我们关心的是下跌，所以记录 decline = -return
    cache["涨跌%"] = cache.groupby("代码")["收盘"].pct_change() * 100
    cache["跌幅%"] = -cache["涨跌%"]  # 正值 = 下跌幅度

    all_signals = []
    groups = cache.groupby("代码")

    for code, grp in tqdm(groups, desc="扫描信号", unit="只"):
        if len(grp) < lookback + 5:
            continue
        name = code_to_name.get(code, "")

        highs = grp["最高"].values
        lows = grp["最低"].values
        prev_close = grp["前收"].values
        declines = grp["跌幅%"].values   # 每日跌幅（正值）
        dates = grp["日期"].values
        closes = grp["收盘"].values

        # ---- 向量化计算每日重叠区% ----
        oh = np.minimum(highs[:-1], highs[1:])
        ol = np.maximum(lows[:-1], lows[1:])
        pct = (oh - ol) / prev_close[1:] * 100
        ok = pct > overlap_pct

        # ---- 滑动窗口：找连续 >= min_days 天的重叠 ----
        n = len(ok)
        run_len = np.zeros(n, dtype=int)
        run_len[0] = 1 if ok[0] else 0
        for i in range(1, n):
            if ok[i]:
                run_len[i] = run_len[i - 1] + 1
            else:
                run_len[i] = 0

        # ---- 收集信号 ----
        for i in range(min_days - 1, n):
            if run_len[i] >= min_days:
                streak = min(run_len[i], max_days)
                # overlap[i] 涉及 dates[i] 和 dates[i+1]
                # 触发日 = dates[i+2]（给一天确认）
                buy_day_idx = i + 2
                if buy_day_idx >= len(dates) - 1:
                    continue

                # 短期涨幅过滤：重叠区间内累计涨幅不能超过 max_gain%
                streak_start_idx = i - streak + 1
                streak_start_close = float(closes[streak_start_idx])
                trigger_close = float(closes[buy_day_idx])
                if streak_start_close > 0:
                    streak_gain = (trigger_close - streak_start_close) / streak_start_close * 100
                    if streak_gain > max_gain:
                        continue  # 已经涨太多了，不追

                # 跌停过滤：往前 5 天不能存在跌停（单日跌幅 ≥ limit_down%）
                ld_start = max(0, buy_day_idx - 5)
                recent_declines = declines[ld_start:buy_day_idx]
                valid_ld = recent_declines[~np.isnan(recent_declines)]
                if len(valid_ld) > 0 and np.any(valid_ld >= limit_down):
                    continue  # 近期有跌停，跳过

                trigger_date = dates[buy_day_idx]

                # 往前 lookback 天，找最大单日跌幅
                lookback_start = max(0, buy_day_idx - lookback)
                lookback_end = buy_day_idx  # 不包括触发日本身

                # 极端波动过滤：近20日区间内最大涨跌幅不能超过 max_range_20d%
                # 最大涨幅 = (区间最高 - 区间最低) / 区间最低，超阈值=山顶
                # 最大跌幅 = (区间最高 - 区间最低) / 区间最高，超阈值=飞刀
                d20_start = max(0, buy_day_idx - 20)
                if d20_start < buy_day_idx:
                    h20 = np.max(highs[d20_start:buy_day_idx + 1])
                    l20 = np.min(lows[d20_start:buy_day_idx + 1])
                    if h20 > 0 and l20 > 0:
                        rally = (h20 - l20) / l20 * 100     # 最大涨幅
                        decline = (h20 - l20) / h20 * 100   # 最大跌幅
                        if rally >= max_range_20d or decline >= max_range_20d:
                            continue  # 近期波动过大，跳过
                if lookback_end - lookback_start < 5:
                    continue

                window_declines = declines[lookback_start:lookback_end]
                # 排除 NaN 和 <=0 的（上涨日）
                valid = window_declines[~np.isnan(window_declines) & (window_declines > 0)]
                if len(valid) == 0:
                    continue
                max_decline = float(np.max(valid))

                # 限价买单 = 前收 × (1 - 最大跌幅/100)
                ref_close = float(prev_close[buy_day_idx])
                if ref_close <= 0 or np.isnan(ref_close):
                    continue
                buy_limit = round(ref_close * (1 - max_decline / 100), 2)
                if buy_limit <= 0:
                    continue

                all_signals.append({
                    "代码": code,
                    "名称": name,
                    "触发日": trigger_date,
                    "前收": ref_close,
                    "最大跌幅": round(max_decline, 2),
                    "买入限价": buy_limit,
                    "重叠天数": streak,
                })

    df = pd.DataFrame(all_signals)
    if len(df) > 0:
        df = df.sort_values("触发日").reset_index(drop=True)
        print(f"  共发现 {len(df)} 个信号, {df['代码'].nunique()} 只股票")
    else:
        print("  共发现 0 个信号")
    return df


# ====================================================================
# 逐日模拟
# ====================================================================

def run_sim(capital: float = 50000, target_pct: float = 1.0,
            overlap_pct: float = 3.0, commission_rate: float = 0.0001,
            stamp_tax: float = 0.0005, start_date: str = "2024-01-01",
            lookback: int = 15, board: str = "main",
            max_gain: float = 20.0, limit_down: float = 9.5,
            max_range_20d: float = 20.0):
    """
    主模拟流程。

    每天：
      a. 平仓：持仓卖出（目标价止盈 or 尾盘强平）
      b. 开仓：逐只检查信号，限价单成交则满仓买入，直到 90%+ 资金用完
      c. 记录权益
    """
    data = StockData()
    info = StockInfo()
    code_to_name = dict(zip(info.df["代码"], info.df["名称"]))

    # 1. 扫描信号
    print("=" * 60)
    signals = compute_signals(data, code_to_name, start_date=start_date,
                              overlap_pct=overlap_pct, lookback=lookback,
                              board=board, max_gain=max_gain,
                              limit_down=limit_down,
                              max_range_20d=max_range_20d)
    if len(signals) == 0:
        print("无信号"); return

    # 2. 构建 K 线快速索引：(代码, 日期) → (最高, 最低, 收盘)
    print("索引K线...")
    cache = data.cache[data.cache["日期"] >= pd.Timestamp(start_date)]
    kline_idx = {}
    for _, row in tqdm(cache.iterrows(), total=len(cache), desc="索引"):
        kline_idx[(row["代码"], row["日期"])] = (
            float(row["最高"]), float(row["最低"]), float(row["收盘"])
        )

    # 3. 按日期分组信号
    all_trading_days = sorted(set(d for _, d in kline_idx.keys()
                                  if d >= pd.Timestamp(start_date)))
    sig_by_date = defaultdict(list)
    for _, s in signals.iterrows():
        d = s["触发日"]
        sig_by_date[d].append(s)

    print(f"逐日模拟 ({len(all_trading_days)} 天)...")

    from backtest.sim_types import Position, Trade, EquityPoint
    from backtest.renderer import render_dip_buy_report

    cash = capital
    positions: list = []       # List[Position]
    closed_trades: list = []   # List[Trade]
    equity_curve: list = []    # List[EquityPoint]

    for date in tqdm(all_trading_days, desc="模拟"):
        # ================================================================
        # a. 平仓
        # ================================================================
        survivors = []
        for pos in positions:
            key = (pos.code, date)
            if key not in kline_idx:
                survivors.append(pos)
                continue
            high, low, close = kline_idx[key]

            filled = high >= pos.target_price
            sell_price = pos.target_price if filled else close
            proceeds = pos.shares * sell_price
            sell_fee = proceeds * commission_rate + proceeds * stamp_tax
            net_proceeds = proceeds - sell_fee
            cash += net_proceeds

            return_pct = round((net_proceeds - pos.total_cost) / pos.total_cost * 100, 2)
            closed_trades.append(Trade(
                code=pos.code,
                name=code_to_name.get(pos.code, ""),
                buy_date=pd.Timestamp(pos.buy_date).strftime("%Y-%m-%d"),
                buy_price=round(pos.buy_price, 2),
                sell_date=pd.Timestamp(date).strftime("%Y-%m-%d"),
                sell_price=round(sell_price, 2),
                return_pct=return_pct,
                pnl=round(net_proceeds - pos.total_cost, 2),
                filled=filled,
                lots=pos.shares // 100,
                streak_days=pos.streak_days,
            ))
        positions = survivors

        # ================================================================
        # b. 开仓
        # ================================================================
        if date in sig_by_date:
            today_cash_start = cash
            today_signals = list(sig_by_date[date])
            random.shuffle(today_signals)

            for s in today_signals:
                cash_used = today_cash_start - cash
                if today_cash_start > 0 and cash_used / today_cash_start >= 0.90:
                    break

                code = s["代码"]
                buy_limit = s["买入限价"]
                key = (code, date)
                if key not in kline_idx:
                    continue
                high, low, close = kline_idx[key]

                if low > buy_limit:
                    continue

                buy_price = buy_limit
                if cash < buy_price * 100 * (1 + commission_rate):
                    continue

                lots = int(cash / (buy_price * 100 * (1 + commission_rate)))
                if lots <= 0:
                    continue
                shares = lots * 100
                cost = shares * buy_price
                buy_fee = cost * commission_rate
                total_cost = cost + buy_fee
                if total_cost > cash:
                    continue

                cash -= total_cost
                buy_day_low = low
                target_price = round(buy_day_low * (1 + target_pct / 100), 2)
                positions.append(Position(
                    code=code, shares=shares, buy_price=buy_price,
                    total_cost=total_cost, target_price=target_price,
                    buy_date=date, buy_day_low=buy_day_low,
                    streak_days=int(s.get("重叠天数", 0)),
                ))

        # ================================================================
        # c. 记录权益
        # ================================================================
        pos_value = sum(p.shares * p.buy_price for p in positions)
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        equity_curve.append(EquityPoint(
            date=date_str,
            equity=cash + pos_value,
            cash=cash,
            positions=len(positions),
        ))

    all_dates = all_trading_days

    # ================================================================
    # 4. 强制平仓
    # ================================================================
    if positions:
        last_date = all_dates[-1]
        last_date_str = pd.Timestamp(last_date).strftime("%Y-%m-%d")
        for pos in positions:
            key = (pos.code, last_date)
            if key in kline_idx:
                _, _, close = kline_idx[key]
                sell_price = close
            else:
                sell_price = pos.buy_price
            proceeds = pos.shares * sell_price
            sell_fee = proceeds * commission_rate + proceeds * stamp_tax
            net_proceeds = proceeds - sell_fee
            cash += net_proceeds
            return_pct = round((net_proceeds - pos.total_cost) / pos.total_cost * 100, 2)
            closed_trades.append(Trade(
                code=pos.code,
                name=code_to_name.get(pos.code, ""),
                buy_date=pd.Timestamp(pos.buy_date).strftime("%Y-%m-%d"),
                buy_price=round(pos.buy_price, 2),
                sell_date=last_date_str,
                sell_price=round(sell_price, 2),
                return_pct=return_pct,
                pnl=round(net_proceeds - pos.total_cost, 2),
                filled=False,
                lots=pos.shares // 100,
                streak_days=pos.streak_days,
            ))

    # ================================================================
    # 5. 统计输出
    # ================================================================
    total = len(closed_trades)
    wins = sum(1 for t in closed_trades if t.is_win)
    total_pnl = sum(t.pnl for t in closed_trades)
    hit_count = sum(1 for t in closed_trades if t.filled)
    avg_pnl = total_pnl / total if total > 0 else 0
    avg_return = sum(t.return_pct for t in closed_trades) / total if total > 0 else 0
    hit_rate = hit_count / total * 100 if total > 0 else 0

    print(f"\n{'='*60}")
    print(f"  全市场 Overlap + 回撤买入 组合模拟")
    print(f"  起始: ¥{capital:,.0f} | 板块: {board} | 佣金 {commission_rate*10000:.0f}‱ + 印花税 {stamp_tax*10000:.0f}‱")
    print(f"  条件: 连续(3-10)天重叠>{overlap_pct}% + 短期涨幅<{max_gain}% → 回看{lookback}天最大跌幅限价买 → 上一日最低价+{target_pct}%止盈")
    print(f"  日期: {start_date} ~ {all_dates[-1].strftime('%Y-%m-%d')}")
    print(f"{'='*60}")
    print(f"  总交易: {total} 笔")
    print(f"  胜率: {wins}/{total} = {wins/total*100:.1f}%" if total > 0 else "  胜率: N/A")
    print(f"  止盈率: {hit_rate:.1f}% ({hit_count}笔止盈)")
    print(f"  总盈亏: ¥{total_pnl:+,.0f}  期末: ¥{cash:,.0f}  ({(cash-capital)/capital*100:+.1f}%)")
    print(f"  平均: ¥{avg_pnl:+,.0f}/笔  平均收益: {avg_return:+.1f}%/笔")
    print(f"{'='*60}")

    # 6. 收集 K 线（用于 HTML 弹窗核对）
    traded_codes = list(set(t.code for t in closed_trades))
    buy_dates_by_code = defaultdict(list)
    for t in closed_trades:
        buy_dates_by_code[t.code].append(t.buy_date)
    kline_map = {}
    if traded_codes:
        print(f"收集K线 ({len(traded_codes)} 只)...")
        for code in tqdm(traded_codes, desc="K线"):
            try:
                kdf = data.get_kline(code, days=60)
                if len(kdf) == 0:
                    continue
                if "开盘" not in kdf.columns:
                    kdf["开盘"] = kdf["收盘"].shift(1).fillna(kdf["收盘"])
                kdf["涨跌%"] = kdf["收盘"].pct_change() * 100
                kdf["前收"] = kdf["收盘"].shift(1)
                changes = [round(v, 2) if not pd.isna(v) else 0 for v in kdf["涨跌%"].values]
                prevs = [round(v, 2) if not pd.isna(v) else 0 for v in kdf["前收"].values]
                volumes = [float(v) if not pd.isna(v) else 0 for v in kdf["成交额"].values]
                ohlc = []
                for _, r in kdf.iterrows():
                    ohlc.append([
                        float(r["开盘"]), float(r["收盘"]),
                        float(r["最低"]), float(r["最高"]),
                    ])
                kline_map[code] = {
                    "dates": kdf["日期"].dt.strftime("%Y-%m-%d").tolist(),
                    "data": ohlc,
                    "prevs": prevs,
                    "changes": changes,
                    "volumes": volumes,
                    "buys": buy_dates_by_code.get(code, []),
                    "name": code_to_name.get(code, ""),
                }
            except Exception:
                pass

    # 7. 渲染 HTML
    html = render_dip_buy_report(
        capital=capital, target_pct=target_pct, overlap_pct=overlap_pct,
        commission_rate=commission_rate, stamp_tax=stamp_tax,
        start_date=start_date, lookback=lookback, board=board,
        max_gain=max_gain, limit_down=limit_down, max_range_20d=max_range_20d,
        trades=closed_trades, equity_curve=equity_curve,
        final_equity=cash, kline_map=kline_map,
    )
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✓ HTML: {OUTPUT_HTML}")


# ====================================================================
# CLI
# ====================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="全市场 Overlap + 回撤买入 组合模拟",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/sim_dip_buy.py
  python scripts/sim_dip_buy.py --capital 100000 --target 2.0
  python scripts/sim_dip_buy.py --board all                # 全A股
  python scripts/sim_dip_buy.py --board main               # 仅沪深主板（默认）
  python scripts/sim_dip_buy.py --capital 500000 -m 1.5 --overlap 4.0 --lookback 10
        """,
    )
    parser.add_argument("--capital", type=float, default=50000,
                        help="初始资金（元），默认 50000")
    parser.add_argument("--target", "-m", type=float, default=1.0,
                        help="止盈目标%%，默认 1.0")
    parser.add_argument("--overlap", type=float, default=3.0,
                        help="重叠区阈值%%，默认 3.0")
    parser.add_argument("--commission", type=float, default=1.0,
                        help="佣金（万分之），默认 1.0")
    parser.add_argument("--lookback", type=int, default=15,
                        help="回看天数，默认 15")
    parser.add_argument("--start-date", type=str, default="2026-01-01",
                        help="起始日期")
    parser.add_argument("--board", type=str, default="main",
                        choices=["main", "all", "gem", "star"],
                        help="标的范围: main=沪深主板, all=全A股, gem=创业板, star=科创板 (默认 main)")
    parser.add_argument("--max-gain", type=float, default=20.0,
                        help="重叠区间累计涨幅上限%%，防追高 (默认 20)")
    parser.add_argument("--limit-down", type=float, default=9.5,
                        help="跌停阈值%%，前5日有跌幅>=此值则跳过 (默认 9.5)")
    parser.add_argument("--max-range-20d", type=float, default=20.0,
                        help="近20日区间最大涨跌幅上限%%，超此值视为波动过大跳过 (默认 20)")
    args = parser.parse_args()

    t0 = time.time()
    run_sim(
        capital=args.capital,
        target_pct=args.target,
        overlap_pct=args.overlap,
        commission_rate=args.commission / 10000,
        start_date=args.start_date,
        lookback=args.lookback,
        board=args.board,
        max_gain=args.max_gain,
        limit_down=args.limit_down,
    )
    print(f"\n总耗时: {time.time() - t0:.0f}秒")
