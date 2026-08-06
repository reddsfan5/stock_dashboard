"""
模拟引擎共用模块 — K线索引、K线采集、交易工具

所有 sim_*.py 回测脚本的公共逻辑抽取到此，避免重复维护。
"""

import os
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

import numpy as np
import pandas as pd
from tqdm import tqdm

from backtest.sim_types import Position, Trade, EquityPoint

# 沪深主板代码前缀
MAIN_BOARD = (
    "sh600", "sh601", "sh603", "sh605",
    "sz000", "sz001", "sz002", "sz003",
)


# ====================================================================
# K 线索引
# ====================================================================

def build_kline_index(cache: pd.DataFrame, start_date: str = None) -> Dict:
    """
    构建 (代码, 日期) → (最高, 最低, 收盘, 开盘) 的快速索引。
    """
    if start_date:
        cache = cache[cache["日期"] >= pd.Timestamp(start_date)]
    kline_idx = {}
    for _, row in tqdm(cache.iterrows(), total=len(cache), desc="索引K线"):
        kline_idx[(row["代码"], row["日期"])] = (
            float(row["最高"]), float(row["最低"]),
            float(row["收盘"]), float(row["开盘"]),
        )
    return kline_idx


# ====================================================================
# 信号分组
# ====================================================================

def group_signals_by_date(signals: pd.DataFrame,
                          kline_idx: Dict) -> Tuple[List, Dict]:
    """
    将信号按触发日分组，返回 (全部交易日列表, {日期: [信号]} )。
    """
    all_dates = sorted(set(d for _, d in kline_idx.keys()))
    valid_dates = set(d for _, d in kline_idx.keys())
    sig_by_date = defaultdict(list)
    for _, s in signals.iterrows():
        d = s["触发日"]
        if d in valid_dates:
            sig_by_date[d].append(s)
    return all_dates, sig_by_date


# ====================================================================
# 交易操作
# ====================================================================

def sell_position(pos: Position, date, kline_idx: Dict,
                  commission_rate: float, stamp_tax: float,
                  code_to_name: dict) -> Trade:
    """卖出一个持仓，返回 Trade 记录"""
    key = (pos.code, date)
    if key not in kline_idx:
        return None
    high, low, close, open_ = kline_idx[key]

    filled = high >= pos.target_price
    sell_price = pos.target_price if filled else close
    proceeds = pos.shares * sell_price
    sell_fee = proceeds * commission_rate + proceeds * stamp_tax
    net_proceeds = proceeds - sell_fee

    return Trade(
        code=pos.code,
        name=code_to_name.get(pos.code, ""),
        buy_date=pd.Timestamp(pos.buy_date).strftime("%Y-%m-%d"),
        buy_price=round(pos.buy_price, 2),
        sell_date=pd.Timestamp(date).strftime("%Y-%m-%d"),
        sell_price=round(sell_price, 2),
        return_pct=round((net_proceeds - pos.total_cost) / pos.total_cost * 100, 2),
        pnl=round(net_proceeds - pos.total_cost, 2),
        filled=filled,
        lots=pos.shares // 100,
    )


def compute_lots(cash: float, buy_price: float, position_pct: float,
                 commission_rate: float) -> Optional[int]:
    """计算可买手数。position_pct=1.0 满仓，0.5 半仓。"""
    available = cash * position_pct
    lots = int(available / (buy_price * 100 * (1 + commission_rate)))
    return lots if lots > 0 else None


def record_equity(date, cash: float, positions: List[Position]) -> EquityPoint:
    """记录当日权益"""
    pos_value = sum(p.shares * p.buy_price for p in positions)
    return EquityPoint(
        date=pd.Timestamp(date).strftime("%Y-%m-%d"),
        equity=cash + pos_value,
        cash=cash,
        positions=len(positions),
    )


def force_close_positions(positions: List[Position], last_date,
                          kline_idx: Dict, commission_rate: float,
                          stamp_tax: float, code_to_name: dict,
                          cash: float) -> Tuple[List[Trade], float]:
    """期末强制平仓所有持仓，返回 (交易列表, 最终现金)"""
    trades = []
    for pos in positions:
        key = (pos.code, last_date)
        if key in kline_idx:
            _, _, close, _ = kline_idx[key]
            sell_price = close
        else:
            sell_price = pos.buy_price
        proceeds = pos.shares * sell_price
        sell_fee = proceeds * commission_rate + proceeds * stamp_tax
        net_proceeds = proceeds - sell_fee
        cash += net_proceeds
        trades.append(Trade(
            code=pos.code,
            name=code_to_name.get(pos.code, ""),
            buy_date=pd.Timestamp(pos.buy_date).strftime("%Y-%m-%d"),
            buy_price=round(pos.buy_price, 2),
            sell_date=pd.Timestamp(last_date).strftime("%Y-%m-%d"),
            sell_price=round(sell_price, 2),
            return_pct=round((net_proceeds - pos.total_cost) / pos.total_cost * 100, 2),
            pnl=round(net_proceeds - pos.total_cost, 2),
            filled=False,
            lots=pos.shares // 100,
            streak_days=pos.streak_days,
        ))
    return trades, cash


# ====================================================================
# 统计输出
# ====================================================================

def print_stats(trades: List[Trade], cash: float, capital: float,
                start_date: str, end_date: str, title: str = "",
                extra_info: str = ""):
    """打印模拟统计结果"""
    total = len(trades)
    wins = sum(1 for t in trades if t.is_win)
    total_pnl = sum(t.pnl for t in trades)
    hit = sum(1 for t in trades if t.filled)
    avg_pnl = total_pnl / total if total > 0 else 0

    print(f"\n{'='*60}")
    print(f"  {title}" if title else "")
    print(f"  起始: ¥{capital:,.0f}")
    print(f"  {extra_info}" if extra_info else "")
    print(f"  日期: {start_date} ~ {end_date}")
    print(f"{'='*60}")
    if total > 0:
        print(f"  总交易: {total} 笔  胜率: {wins}/{total} = {wins/total*100:.1f}%")
        print(f"  止盈率: {hit/total*100:.1f}% ({hit}笔止盈)")
    else:
        print(f"  总交易: 0 笔")
    print(f"  总盈亏: ¥{total_pnl:+,.0f}  期末: ¥{cash:,.0f}  ({(cash-capital)/capital*100:+.1f}%)")
    print(f"  平均: ¥{avg_pnl:+,.0f}/笔")
    print(f"{'='*60}")


# ====================================================================
# K 线数据采集（用于 HTML 弹窗）
# ====================================================================

def collect_kline_for_trades(data, trades: List[Trade],
                             code_to_name: dict) -> dict:
    """
    采集每笔交易对应的 K 线，以买入日为中心前后各约 30 根。
    返回 {code|buy_date: {dates, data, volumes, changes, prevs, buys, name}}。
    """
    seen_pairs = set()
    unique_pairs = []
    for t in trades:
        pair = (t.code, t.buy_date)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            unique_pairs.append(t)

    if not unique_pairs:
        return {}

    kline_map = {}
    print(f"收集K线 ({len(unique_pairs)} 条)...")
    for t in tqdm(unique_pairs, desc="K线"):
        try:
            code, buy_date = t.code, t.buy_date
            center = pd.Timestamp(buy_date)
            cache_sub = data.cache
            kdf = cache_sub[(cache_sub["代码"] == code) &
                            (cache_sub["日期"] >= center - pd.Timedelta(days=50)) &
                            (cache_sub["日期"] <= center + pd.Timedelta(days=50))].sort_values("日期")
            if len(kdf) < 10:
                continue

            # 以买入日为中心，截取前后各30个交易日
            kdf_dates = kdf["日期"].dt.strftime("%Y-%m-%d")
            center_idx = None
            for i, d in enumerate(kdf_dates):
                if d == buy_date:
                    center_idx = i
                    break
            if center_idx is not None:
                s = max(0, center_idx - 30)
                e = min(len(kdf), center_idx + 30)
                if e - s < 60:
                    s = max(0, e - 60) if e >= 60 else 0
                    e = min(len(kdf), s + 60)
                kdf = kdf.iloc[s:e]

            if "开盘" not in kdf.columns:
                kdf["开盘"] = kdf["收盘"].shift(1).fillna(kdf["收盘"])
            kdf["涨跌%"] = kdf["收盘"].pct_change() * 100
            kdf["前收"] = kdf["收盘"].shift(1)
            changes = [round(v, 2) if not pd.isna(v) else 0 for v in kdf["涨跌%"].values]
            prevs = [round(v, 2) if not pd.isna(v) else 0 for v in kdf["前收"].values]
            volumes = [float(v) if not pd.isna(v) else 0 for v in kdf["成交额"].values]
            ohlc = [[float(r["开盘"]), float(r["收盘"]), float(r["最低"]), float(r["最高"])]
                    for _, r in kdf.iterrows()]

            key = f"{code}|{buy_date}"
            kline_map[key] = {
                "dates": kdf["日期"].dt.strftime("%Y-%m-%d").tolist(),
                "data": ohlc, "prevs": prevs, "changes": changes,
                "volumes": volumes, "buys": [buy_date],
                "name": code_to_name.get(code, ""),
            }
        except Exception:
            pass
    return kline_map
