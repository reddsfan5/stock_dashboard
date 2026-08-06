"""
银河证券网格交易回测系统

设计模式：
  - Strategy Pattern：两种网格策略可互换
  - Template Method：公共回测流程在抽象基类，具体触发逻辑在子类
  - Builder Pattern：GridConfig 链式构建
  - Value Object：Bar / Trade / Position 不可变

设计原则：
  - SRP：配置、费率、策略、引擎各司其职
  - OCP：新增网格模式只需继承基类
  - LSP：所有策略可无差别替换
  - DIP：引擎依赖抽象策略接口

使用示例
--------
# 命令行
$ python galaxy_grid.py --stock sh600519 --mode 1                # 成交驱动型（茅台）
$ python galaxy_grid.py --stock sh600166 --mode 2 --step 0.5     # 到价触发型（福田汽车）
$ python galaxy_grid.py --stock sz000001 --mode 2 --step 1.0 --lot 200 --price-min 10 --price-max 50

# 代码调用 — 成交驱动型
>>> from galaxy_grid import *

>>> bars = GridBacktestEngine.fetch_data("sh600519")      # 获取日内分钟线
>>> config = (GridConfig(base_price=float(bars[0].open))  # Builder 链式配置
...           .with_step(StepMode.PCT, 0.5)
...           .with_levels(5)
...           .with_lot(100)
...           .with_position_limits(min_pos=0, max_pos=1500))

>>> engine = GridBacktestEngine(TransactionDrivenStrategy(config))
>>> result = engine.run("sh600519", initial_cash=1_000_000)
>>> print(result.summary(open_price=bars[0].open, close_price=bars[-1].close))

# 代码调用 — 到价触发型
>>> config2 = (GridConfig(base_price=float(bars[0].open))
...            .with_step(StepMode.PCT, 0.5)
...            .with_levels(5))
>>> engine2 = GridBacktestEngine(PriceTriggeredStrategy(config2))
>>> result2 = engine2.run("sh600166", initial_cash=1_000_000)
>>> for t in result2.trades:
...     print(t)

# 扩展：自定义策略只需继承 AbstractGridStrategy
>>> class MyStrategy(AbstractGridStrategy):
...     def _init_state(self, bars): ...
...     def _on_bar(self, bar, prev_close) -> List[Trade]: ...
...     def _close_out(self, final_price) -> List[Trade]: ...
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Dict, List, Optional, Iterator

import akshare as ak
import pandas as pd


# ====================================================================
# Value Objects（不可变数据对象）
# ====================================================================


class Direction(Enum):
    BUY = auto()
    SELL = auto()

    def __str__(self):
        return "买入" if self == Direction.BUY else "卖出"


class StepMode(Enum):
    PCT = "pct"   # 百分比幅度
    DIFF = "diff"  # 固定差价

    def __str__(self):
        return "%" if self == StepMode.PCT else "元"


@dataclass(frozen=True)
class Bar:
    """单根 K 线（不可变）"""
    time: str
    open: float
    high: float
    low: float
    close: float

    @classmethod
    def from_row(cls, row) -> "Bar":
        return cls(
            time=str(row["day"])[:16],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )


@dataclass(frozen=True)
class Trade:
    """成交记录（不可变）"""
    time: str
    direction: Direction
    price: float
    shares: int
    amount: float
    fee: float
    reason: str = ""

    def __repr__(self):
        return (f"{self.time} {self.direction} @{self.price:.2f} "
                f"×{self.shares}股 ={self.amount:,.0f} 费{self.fee:.0f}")


@dataclass(frozen=True)
class Position:
    """持仓快照（不可变）"""
    cash: float
    shares: int

    @property
    def market_value(self, price: float) -> float:
        return self.cash + self.shares * price


# ====================================================================
# 费率策略（Strategy Pattern 的另一个应用点：不同券商可替换）
# ====================================================================


class FeePolicy:
    """交易费率策略"""

    def __init__(self, commission: float = 0.00025, min_commission: float = 5.0,
                 stamp_tax: float = 0.0005):
        self.commission = commission
        self.min_commission = min_commission
        self.stamp_tax = stamp_tax

    def calculate(self, amount: float, direction: Direction) -> float:
        c = max(amount * self.commission, self.min_commission)
        s = amount * self.stamp_tax if direction == Direction.SELL else 0
        return c + s

    def __repr__(self):
        return (f"佣金{self.commission*100:.3f}%(min{self.min_commission:.0f}) "
                f"+ 印花税{self.stamp_tax*100:.2f}%(卖)")


# ====================================================================
# 网格配置（Builder Pattern）
# ====================================================================


class GridConfig:
    """网格交易配置（Builder 模式，链式调用）"""

    def __init__(self, base_price: float):
        # ---- 基础参数 ----
        self.base_price: float = base_price
        self.step_mode: StepMode = StepMode.PCT
        self.grid_step: float = 0.5         # 每格步长（% 或 元）
        self.grid_levels: int = 5           # 上下各 N 层
        self.lot_shares: int = 100          # 每笔基础委托股数

        # ---- 有效价格区间 ----
        self.price_floor: Optional[float] = None
        self.price_ceiling: Optional[float] = None
        self.cage_to_market: bool = True     # 超笼子转市价

        # ---- 持仓控制 ----
        self.max_position: Optional[int] = None
        self.min_position: int = 0

        # ---- 倍数委托 ----
        self.multiplier_levels: Dict[int, int] = {}  # {层号: 倍数}

        # ---- 费用 ----
        self.fee_policy: FeePolicy = FeePolicy()

    # ----- Builder 链式方法 -----

    def with_step(self, mode: StepMode, step: float) -> "GridConfig":
        self.step_mode = mode
        self.grid_step = step
        return self

    def with_levels(self, n: int) -> "GridConfig":
        self.grid_levels = n
        return self

    def with_lot(self, shares: int) -> "GridConfig":
        self.lot_shares = shares
        return self

    def with_price_range(self, floor: Optional[float] = None,
                         ceiling: Optional[float] = None) -> "GridConfig":
        self.price_floor = floor
        self.price_ceiling = ceiling
        return self

    def with_multiplier(self, levels: Dict[int, int]) -> "GridConfig":
        self.multiplier_levels = levels
        return self

    def with_position_limits(self, min_pos: int = 0,
                             max_pos: Optional[int] = None) -> "GridConfig":
        self.min_position = min_pos
        self.max_position = max_pos
        return self

    def with_fee(self, fee_policy: FeePolicy) -> "GridConfig":
        self.fee_policy = fee_policy
        return self

    # ----- 价格计算 -----

    def grid_price(self, level: int) -> float:
        """第 level 层网格价格（level=0 是基准，正=上方，负=下方）"""
        if self.step_mode == StepMode.DIFF:
            return round(self.base_price + self.grid_step * level, 2)
        else:
            return round(self.base_price * (1 + self.grid_step / 100 * level), 2)

    def get_lot(self, abs_level: int) -> int:
        """获取指定绝对层级的委托倍数"""
        mult = self.multiplier_levels.get(abs_level, 1)
        return self.lot_shares * mult

    def within_range(self, price: float) -> bool:
        """价格是否在有效区间内"""
        if self.price_floor is not None and price < self.price_floor:
            return False
        if self.price_ceiling is not None and price > self.price_ceiling:
            return False
        return True


# ====================================================================
# 抽象策略基类（Template Method + Strategy Pattern）
# ====================================================================


class AbstractGridStrategy(ABC):
    """
    网格策略抽象基类 — 模板方法模式

    子类只需实现两个方法：
      - _init_orders()   → 初始挂单
      - _check_triggers() → 每根 K 线的触发检测

    公共流程（final）：
      initialize → for bar in bars → check_triggers → execute → close_out
    """

    def __init__(self, config: GridConfig):
        self.cfg = config
        self.fee = config.fee_policy
        self._position = Position(cash=0, shares=0)
        self._trades: List[Trade] = []
        self._total_fee: float = 0

    # ========== 子类必须实现 ==========

    @abstractmethod
    def _init_state(self, bars: List[Bar]) -> None:
        """初始化策略状态（挂单、极值追踪等）"""
        ...

    @abstractmethod
    def _on_bar(self, bar: Bar, prev_close: float) -> List[Trade]:
        """
        处理一根 K 线，返回触发的交易列表

        模板方法会处理持仓更新和手续费，子类只需返回应成交的 trade 列表
        """
        ...

    @abstractmethod
    def _close_out(self, final_price: float) -> List[Trade]:
        """收盘平仓，返回平仓交易"""
        ...

    # ========== 模板方法（final - 子类不可覆写） ==========

    def run(self, bars: List[Bar], initial_cash: float) -> "BacktestResult":
        """回测主流程（模板方法）"""
        self._position = Position(cash=initial_cash, shares=0)
        self._trades = []
        self._total_fee = 0

        # 1. 初始化状态
        self._init_state(bars)

        # 2. 逐根 K 线处理
        prev_close = bars[0].open
        for bar in bars:
            new_trades = self._on_bar(bar, prev_close)
            for trade in new_trades:
                self._execute(trade)
            prev_close = bar.close

        # 3. 收盘平仓
        close_trades = self._close_out(bars[-1].close)
        for trade in close_trades:
            self._execute(trade)

        # 4. 计算结果
        final_price = bars[-1].close
        return BacktestResult(
            trades=self._trades,
            final_cash=self._position.cash,
            final_shares=self._position.shares,
            final_price=final_price,
            total_fee=self._total_fee,
            initial_cash=initial_cash,
        )

    def _execute(self, trade: Trade) -> None:
        """执行一笔交易，更新持仓（仅基类内部调用）"""
        if trade.direction == Direction.BUY:
            self._position = Position(
                cash=self._position.cash - trade.amount - trade.fee,
                shares=self._position.shares + trade.shares,
            )
        else:
            self._position = Position(
                cash=self._position.cash + trade.amount - trade.fee,
                shares=self._position.shares - trade.shares,
            )
        self._trades.append(trade)
        self._total_fee += trade.fee

    # ========== 辅助方法（子类可用） ==========

    def _make_trade(self, time: str, direction: Direction, price: float,
                    shares: int, reason: str = "") -> Trade:
        """构建一笔交易（含手续费计算）"""
        amount = shares * price
        f = self.fee.calculate(amount, direction)
        return Trade(time=time, direction=direction, price=price,
                     shares=shares, amount=amount, fee=f, reason=reason)

    def _can_buy(self, shares: int = 0) -> bool:
        """检查是否可以买入"""
        if self.cfg.max_position is not None:
            if self._position.shares + shares > self.cfg.max_position:
                return False
        return True

    def _can_sell(self, shares: int = 0) -> bool:
        """检查是否可以卖出"""
        return self._position.shares >= shares + self.cfg.min_position


# ====================================================================
# 模式1：成交驱动型网格
# ====================================================================


class TransactionDrivenStrategy(AbstractGridStrategy):
    """
    成交驱动型网格交易

    固定网格线挂单，成交价穿过即触发，触发后在对侧网格线挂反向单。
    """

    def __init__(self, config: GridConfig):
        super().__init__(config)
        self._levels: List[int] = []
        self._grid: Dict[int, float] = {}        # level -> price
        self._pending_buy: Dict[int, bool] = {}   # level -> 是否有挂买单
        self._pending_sell: Dict[int, bool] = {}  # level -> 是否有挂卖单

    def _init_state(self, bars: List[Bar]) -> None:
        n = self.cfg.grid_levels
        self._levels = list(range(-n, n + 1))
        self._grid = {lv: self.cfg.grid_price(lv) for lv in self._levels}

        self._pending_buy = {}
        self._pending_sell = {}

        # 中枢以下挂买单，中枢以上挂卖单
        for lv in self._levels:
            if lv < 0:
                self._pending_buy[lv] = True
            elif lv > 0:
                self._pending_sell[lv] = True

    def _on_bar(self, bar: Bar, prev_close: float) -> List[Trade]:
        trades = []
        c = bar.close
        in_range = self.cfg.within_range(c)

        if c > prev_close:
            # 价格上涨 → 检查卖单（从低到高，取最近的一个）
            for lv in self._levels:
                g = self._grid[lv]
                if self._pending_sell.get(lv) and prev_close < g <= c:
                    lot = self.cfg.get_lot(abs(lv))
                    if not self._can_sell(lot):
                        continue  # 持仓不足，跳过这一层

                    exec_price, reason = self._resolve_price(c, g, in_range, "卖")
                    if exec_price is None:
                        continue
                    trades.append(self._make_trade(bar.time, Direction.SELL,
                                                   exec_price, lot, reason))
                    self._pending_sell[lv] = False
                    # 下一格挂买单
                    if lv - 1 in self._grid:
                        self._pending_buy[lv - 1] = True
                    break

        elif c < prev_close:
            # 价格下跌 → 检查买单（从高到低，取最近的一个）
            for lv in sorted(self._levels, reverse=True):
                g = self._grid[lv]
                if self._pending_buy.get(lv) and prev_close > g >= c:
                    lot = self.cfg.get_lot(abs(lv))
                    if not self._can_buy(lot):
                        continue  # 超持仓上限，跳过

                    exec_price, reason = self._resolve_price(c, g, in_range, "买")
                    if exec_price is None:
                        continue
                    trades.append(self._make_trade(bar.time, Direction.BUY,
                                                   exec_price, lot, reason))
                    self._pending_buy[lv] = False
                    # 上一格挂卖单
                    if lv + 1 in self._grid:
                        self._pending_sell[lv + 1] = True
                    break

        return trades

    def _resolve_price(self, actual: float, grid_price: float,
                       in_range: bool, side: str) -> tuple[Optional[float], str]:
        """处理超笼子逻辑：有效区间外 → 市价；区间内 → 网格价；废单 → None"""
        if not in_range:
            if self.cfg.cage_to_market:
                return actual, f"超笼子转市价{side}出"
            else:
                return None, f"超笼子废单"
        return grid_price, f"触及网格{side}单"

    def _close_out(self, final_price: float) -> List[Trade]:
        return []


# ====================================================================
# 模式2：到价触发型
# ====================================================================


class PriceTriggeredStrategy(AbstractGridStrategy):
    """
    到价触发型网格交易

    上涨 X% 卖出 → 跟踪最高点 → 回落 X% 买回
    下跌 X% 买入 → 跟踪最低点 → 反弹 X% 卖出
    """

    class _State(Enum):
        NEUTRAL = auto()   # 初始状态
        UP = auto()        # 已卖出，等待回落买入
        DOWN = auto()      # 已买入，等待反弹卖出

    def __init__(self, config: GridConfig):
        super().__init__(config)
        self._direction = self._State.NEUTRAL
        self._last_trade_price: float = 0
        self._high_since_sell: float = 0   # 卖出后的最高价（用于判断回落）
        self._low_since_buy: float = 0     # 买入后的最低价（用于判断反弹）
        self._up_triggers: int = 0         # 上涨触发次数
        self._down_triggers: int = 0       # 下跌触发次数

    def _init_state(self, bars: List[Bar]) -> None:
        base = self.cfg.base_price
        self._direction = self._State.NEUTRAL
        self._last_trade_price = base
        self._high_since_sell = base
        self._low_since_buy = base
        self._up_triggers = 0
        self._down_triggers = 0

    def _on_bar(self, bar: Bar, prev_close: float) -> List[Trade]:
        trades = []
        c = bar.close

        # ---- 更新极值 ----
        if self._direction == self._State.UP:
            self._high_since_sell = max(self._high_since_sell, c)
        elif self._direction == self._State.DOWN:
            self._low_since_buy = min(self._low_since_buy, c)

        step = self.cfg.grid_step
        is_pct = self.cfg.step_mode == StepMode.PCT
        in_range = self.cfg.within_range(c)

        def _move(reference: float, current: float) -> float:
            if is_pct:
                return (current - reference) / reference * 100
            return current - reference

        # ---- 上涨/反弹触发卖出 ----
        if self._direction != self._State.UP:
            rise = _move(self._last_trade_price, c)
            if rise >= step and self._can_sell():
                self._up_triggers += 1
                ep, reason = self._resolve(c, self._up_triggers, in_range, "卖")
                lot = self.cfg.get_lot(self._up_triggers)
                trades.append(self._make_trade(
                    bar.time, Direction.SELL, ep, lot,
                    f"上涨触发卖出(第{self._up_triggers}次)"))
                self._last_trade_price = c
                self._high_since_sell = c
                self._low_since_buy = c
                self._direction = self._State.UP

        # ---- 回落触发买入 ----
        if self._direction == self._State.UP:
            pullback = -_move(self._high_since_sell, c)
            if pullback >= step and self._can_buy():
                self._down_triggers += 1
                ep, reason = self._resolve(c, -self._down_triggers, in_range, "买")
                lot = self.cfg.get_lot(self._down_triggers)
                trades.append(self._make_trade(
                    bar.time, Direction.BUY, ep, lot,
                    f"回落触发买入(第{self._down_triggers}次)"))
                self._last_trade_price = c
                self._low_since_buy = c
                self._high_since_sell = c
                self._direction = self._State.DOWN

        # ---- 下跌触发买入 ----
        if self._direction == self._State.NEUTRAL and c < self._last_trade_price:
            fall = -_move(self._last_trade_price, c)
            if fall >= step and self._can_buy():
                self._down_triggers += 1
                ep, reason = self._resolve(c, -self._down_triggers, in_range, "买")
                lot = self.cfg.get_lot(self._down_triggers)
                trades.append(self._make_trade(
                    bar.time, Direction.BUY, ep, lot,
                    f"下跌触发买入(第{self._down_triggers}次)"))
                self._last_trade_price = c
                self._low_since_buy = c
                self._high_since_sell = c
                self._direction = self._State.DOWN

        # ---- 反弹触发卖出 ----
        if self._direction == self._State.DOWN:
            rebound = _move(self._low_since_buy, c)
            if rebound >= step and self._can_sell():
                self._up_triggers += 1
                ep, reason = self._resolve(c, self._up_triggers, in_range, "卖")
                lot = self.cfg.get_lot(self._up_triggers)
                trades.append(self._make_trade(
                    bar.time, Direction.SELL, ep, lot,
                    f"反弹触发卖出(第{self._up_triggers}次)"))
                self._last_trade_price = c
                self._high_since_sell = c
                self._low_since_buy = c
                self._direction = self._State.UP

        return trades

    def _resolve(self, actual: float, level: int,
                 in_range: bool, side: str) -> tuple:
        if not in_range and self.cfg.cage_to_market:
            return actual, f"超笼子转市价{side}出"
        return self.cfg.grid_price(level), ""

    def _close_out(self, final_price: float) -> List[Trade]:
        return []


# ====================================================================
# 回测结果
# ====================================================================


@dataclass
class BacktestResult:
    """回测结果（不可变）"""
    trades: List[Trade]
    final_cash: float
    final_shares: int
    final_price: float
    total_fee: float
    initial_cash: float

    @property
    def final_value(self) -> float:
        return self.final_cash + self.final_shares * self.final_price

    @property
    def profit(self) -> float:
        return self.final_value - self.initial_cash

    @property
    def profit_pct(self) -> float:
        return (self.profit / self.initial_cash) * 100 if self.initial_cash else 0

    @property
    def buy_count(self) -> int:
        return sum(1 for t in self.trades if t.direction == Direction.BUY)

    @property
    def sell_count(self) -> int:
        return sum(1 for t in self.trades if t.direction == Direction.SELL)

    def summary(self, open_price: float, close_price: float) -> str:
        hold_profit = self.initial_cash / open_price * close_price - self.initial_cash

        lines = [
            f"交易: {len(self.trades)} 笔  (买{self.buy_count} 卖{self.sell_count})",
            f"手续费: {self.total_fee:.2f}",
            f"初始资金: {self.initial_cash:,.0f}",
            f"最终市值: {self.final_value:,.0f}  "
            f"(现金{self.final_cash:,.0f} + {self.final_shares}股×{self.final_price:.2f})",
            f"策略收益: {self.profit:+,.0f}  ({self.profit_pct:+.2f}%)",
            f"持有不动: {hold_profit:+,.0f}  ({hold_profit/self.initial_cash*100:+.2f}%)",
        ]
        return "\n".join(lines)


# ====================================================================
# 回测引擎
# ====================================================================


class GridBacktestEngine:
    """
    回测引擎

    职责：获取数据 + 选择策略 + 运行 + 输出报告
    不关心具体策略实现，只依赖 AbstractGridStrategy 接口（DIP）
    """

    def __init__(self, strategy: AbstractGridStrategy):
        self.strategy = strategy

    @classmethod
    def create(cls, mode: int, config: GridConfig) -> "GridBacktestEngine":
        """工厂方法：根据模式创建对应策略"""
        strategies = {
            1: TransactionDrivenStrategy,
            2: PriceTriggeredStrategy,
        }
        strategy_cls = strategies.get(mode)
        if strategy_cls is None:
            raise ValueError(f"未知模式: {mode}，可选 1=成交驱动型 2=到价触发型")
        return cls(strategy_cls(config))

    @staticmethod
    def fetch_data(symbol: str, period: str = "1") -> List[Bar]:
        """从新浪源获取日内分钟数据"""
        df = ak.stock_zh_a_minute(symbol=symbol, period=period, adjust="")
        df["day"] = pd.to_datetime(df["day"])
        df = df.sort_values("day")

        latest = df["day"].dt.date.max()
        df = df[df["day"].dt.date == latest]

        for c in ["open", "high", "low", "close"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        return [Bar.from_row(row) for _, row in df.iterrows()]

    def run(self, symbol: str, initial_cash: float) -> BacktestResult:
        """执行回测"""
        bars = self.fetch_data(symbol)
        return self.strategy.run(bars, initial_cash)


# ====================================================================
# 报表
# ====================================================================


class Report:
    """格式化输出回测报告"""

    MODE_NAMES = {1: "成交驱动型网格", 2: "到价触发型网格"}

    @staticmethod
    def print(mode: int, result: BacktestResult, cfg: GridConfig,
              bars: List[Bar]) -> None:
        open_price = bars[0].open
        close_price = bars[-1].close
        high = max(b.high for b in bars)
        low = min(b.low for b in bars)
        date = bars[0].time[:10]

        print(f"\n{'=' * 60}")
        print(f"  {Report.MODE_NAMES.get(mode, str(mode))}")
        print(f"{'=' * 60}")
        print(f"日期: {date}  开{open_price:.2f} 高{high:.2f} 低{low:.2f} 收{close_price:.2f}")
        print(f"振幅: {(high-low)/low*100:.2f}%  分钟线: {len(bars)} 根")
        print(f"费率: {cfg.fee_policy}")
        print(f"步长: {cfg.grid_step}{cfg.step_mode}  ×{cfg.grid_levels}层  "
              f"倍数: {cfg.multiplier_levels or '无'}")

        print(f"\n{'-' * 50}")
        print(result.summary(open_price, close_price))
        print(f"{'-' * 50}")

        for t in result.trades:
            print(f"  {t}  [{t.reason}]")

        print()


# ====================================================================
# CLI
# ====================================================================


def main():
    parser = argparse.ArgumentParser(description="银河证券网格交易回测系统")
    parser.add_argument("--stock", default="sh600519")
    parser.add_argument("--mode", type=int, default=1, choices=[1, 2])
    parser.add_argument("--step", type=float, default=0.5)
    parser.add_argument("--levels", type=int, default=5)
    parser.add_argument("--cash", type=float, default=1_000_000)
    parser.add_argument("--lot", type=int, default=100)
    parser.add_argument("--price-min", type=float, default=None)
    parser.add_argument("--price-max", type=float, default=None)
    args = parser.parse_args()

    # 获取数据
    bars = GridBacktestEngine.fetch_data(args.stock)

    # 构建配置（Builder 模式）
    config = (GridConfig(base_price=float(bars[0].open))
              .with_step(StepMode.PCT, args.step)
              .with_levels(args.levels)
              .with_lot(args.lot)
              .with_price_range(args.price_min, args.price_max)
              .with_multiplier({3: 2, 5: 3})  # 第3层2倍，第5层3倍
              .with_position_limits(min_pos=0,
                                    max_pos=args.lot * args.levels * 3))

    # 创建引擎并运行
    engine = GridBacktestEngine.create(args.mode, config)
    result = engine.run(args.stock, args.cash)

    # 输出报告
    Report.print(args.mode, result, config, bars)


if __name__ == "__main__":
    main()
