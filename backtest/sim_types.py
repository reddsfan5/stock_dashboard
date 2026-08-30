"""
模拟交易数据类型

消灭裸元组——Signal/Position/Trade 统一使用 dataclass，加字段时只改一处。
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


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
    stop_price: float = 0.0     # 止损价（0=不止损）
    holding_days: int = 0       # 已持有天数
    max_hold_days: int = 5      # 最大持有天数（0=不限）


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
    filled: bool                # 兼容字段；新代码优先读取 exit_reason
    lots: int                   # 手数
    streak_days: int = 0        # 买入时的连续天数（重叠/推高天数）
    exit_reason: str = ""       # target/stop/expiry/strategy/rebalance/end_of_data

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def is_target_exit(self) -> bool:
        """兼容旧记录：没有 exit_reason 时沿用 filled 的含义。"""
        return self.exit_reason == "target" or (
            not self.exit_reason and self.filled
        )


@dataclass
class EquityPoint:
    """每日权益快照"""
    date: str
    equity: float               # 总权益（现金+持仓市值）
    cash: float                 # 现金
    positions: int              # 持仓数量
    position_value: float = 0.0  # 按当日收盘价盯市后的持仓市值


@dataclass(frozen=True)
class SimulationMetrics:
    """一组可跨策略比较的资金曲线指标。百分比字段均使用百分数口径。"""
    total_return_pct: float = 0.0
    annualized_return_pct: Optional[float] = None
    annualized_volatility_pct: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: float = 0.0
    calmar_ratio: Optional[float] = None
    trade_count: int = 0
    win_rate_pct: float = 0.0
    profit_factor: Optional[float] = None
    avg_trade_return_pct: float = 0.0


@dataclass
class SimulationResult:
    """通用模拟引擎的结构化结果，便于测试、参数搜索和结果归档。"""
    initial_capital: float
    final_equity: float
    start_date: Optional[str]
    end_date: Optional[str]
    trades: List[Trade]
    equity_curve: List[EquityPoint]
    metrics: SimulationMetrics
    random_seed: int
    assumptions: Dict[str, Any]
    output_html: Optional[str] = None
    open_positions: List[Position] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转成可直接 JSON 序列化的字典。"""
        return asdict(self)
