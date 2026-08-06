"""
模拟交易数据类型

消灭裸元组——Signal/Position/Trade 统一使用 dataclass，加字段时只改一处。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Signal:
    """一个买入信号"""
    code: str
    name: str
    trigger_date: str           # "2026-07-15"
    prev_close: float           # 前收盘（用于计算限价）
    max_decline: float          # lookback 天内最大单日跌幅%
    buy_limit: float            # 限价买单价格
    streak_days: int            # 连续重叠天数


@dataclass
class Position:
    """一笔持仓"""
    code: str
    shares: int                 # 持股数量
    buy_price: float            # 实际成交价
    total_cost: float           # 买入总成本（含佣金）
    target_price: float         # 止盈目标价
    buy_date: str               # "2026-07-15"
    buy_day_low: float          # 买入当日最低价（用于计算卖出目标）
    streak_days: int = 0        # 买入时的连续天数（用于 debug）


@dataclass
class Trade:
    """一笔已完成的交易（平仓后记录）"""
    code: str
    name: str
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    return_pct: float           # 收益率%
    pnl: float                  # 盈亏金额
    filled: bool                # True=止盈成交, False=尾盘强平
    lots: int                   # 手数
    streak_days: int = 0        # 买入时的连续天数（重叠/推高天数）

    @property
    def is_win(self) -> bool:
        return self.pnl > 0


@dataclass
class EquityPoint:
    """每日权益快照"""
    date: str
    equity: float               # 总权益（现金+持仓市值）
    cash: float                 # 现金
    positions: int              # 持仓数量
