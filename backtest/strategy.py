"""
模拟策略抽象基类

新策略只需实现两个方法，其余（K线索引、逐日循环、平仓、权益记录、
强平、统计、K线采集、HTML 渲染）全部由引擎自动处理。

使用示例
--------
>>> class MyStrategy(SimStrategy):
...     def scan_signals(self, data, code_to_name, start_date):
...         ...  # 返回 DataFrame with columns: 代码, 名称, 触发日, ...
...         return signals_df
...
...     def process_day(self, ctx) -> list[Position]:
...         ...  # 从 ctx.today_signals 中筛选、买入，返回新持仓列表
...         return new_positions
...
>>> engine = SimEngine(MyStrategy())
>>> engine.run(capital=50000, start_date="2024-01-01")
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from collections import defaultdict

import pandas as pd

from backtest.sim_types import Position


@dataclass
class DayContext:
    """单日交易上下文，策略的 process_day 方法通过它获取所有信息"""
    date: pd.Timestamp              # 当前交易日
    cash: float                     # 当日可用现金（平仓后、开仓前）
    today_start_cash: float         # 当日初始现金（用于仓位上限计算）
    today_signals: list             # 今日触发的信号列表（dict 列表）
    kline_idx: Dict                 # (代码,日期) → (高,低,收,开)
    commission_rate: float          # 佣金率
    target_pct: float               # 止盈目标%
    code_to_name: dict              # 代码→名称映射


class SimStrategy(ABC):
    """模拟策略抽象基类"""

    # ---- 子类必须实现 ----

    @abstractmethod
    def scan_signals(self, data, code_to_name: dict, start_date: str) -> pd.DataFrame:
        """
        扫描全市场信号。

        返回 DataFrame 必须含列: 代码, 名称, 触发日
        可附加策略自有列（如 连续天数、最大跌幅 等），在 process_day 中通过信号 dict 访问。
        """
        ...

    @abstractmethod
    def process_day(self, ctx: DayContext) -> List[Position]:
        """
        处理一个交易日的开仓决策。

        Args:
            ctx: DayContext，包含当日信号、现金、K线索引等

        Returns:
            当日新建持仓列表（引擎自动从 ctx.cash 中扣款）
        """
        ...

    # ---- 可选覆写 ----

    def get_board_filter(self) -> tuple:
        """返回代码前缀元组，用于限定标的范围。默认仅沪深主板。"""
        return (
            "sh600", "sh601", "sh603", "sh605",
            "sz000", "sz001", "sz002", "sz003",
        )

    def get_target_price(self, buy_price: float, buy_day_low: float,
                         target_pct: float) -> float:
        """计算止盈目标价，默认 = 买入价 × (1 + target_pct%)"""
        return round(buy_price * (1 + target_pct / 100), 2)
