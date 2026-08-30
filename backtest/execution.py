"""统一订单执行模型。

该模块只负责“给定参考价格和账户约束，这张订单能否成交、成交多少、现金变化多少”。
策略信号、持仓周期和组合构建不放在这里，避免研究逻辑与撮合假设互相污染。
"""

from dataclasses import dataclass
from math import ceil, floor
from typing import Any, Mapping, Optional


BUY = "buy"
SELL = "sell"


@dataclass(frozen=True)
class ExecutionConfig:
    """交易执行假设。默认值尽量兼容现有研究脚本。"""

    commission_rate: float = 0.0003
    min_commission: float = 0.0
    stamp_tax_rate: float = 0.0005
    slippage_bps: float = 0.0
    lot_size: int = 100
    price_tick: Optional[float] = None
    max_amount_participation: Optional[float] = None
    reject_one_price_limit: bool = False
    price_limit_pct: float = 10.0

    def __post_init__(self):
        if self.commission_rate < 0 or self.min_commission < 0:
            raise ValueError("佣金参数不能为负数")
        if self.stamp_tax_rate < 0 or self.slippage_bps < 0:
            raise ValueError("税费和滑点不能为负数")
        if self.lot_size <= 0:
            raise ValueError("lot_size 必须大于 0")
        if self.price_tick is not None and self.price_tick <= 0:
            raise ValueError("price_tick 必须大于 0")
        if (self.max_amount_participation is not None and
                not 0 < self.max_amount_participation <= 1):
            raise ValueError("max_amount_participation 必须在 (0, 1] 内")
        if self.price_limit_pct <= 0:
            raise ValueError("price_limit_pct 必须大于 0")


@dataclass(frozen=True)
class ExecutionFill:
    """一次订单执行结果。cash_delta 买入为负、卖出为正。"""

    side: str
    requested_shares: int
    filled_shares: int
    reference_price: float
    fill_price: Optional[float]
    gross_amount: float
    commission: float
    stamp_tax: float
    cash_delta: float
    status: str
    reason: str = ""

    @property
    def filled(self) -> bool:
        return self.filled_shares > 0


def commission_for(amount: float, config: ExecutionConfig) -> float:
    if amount <= 0:
        return 0.0
    return max(amount * config.commission_rate, config.min_commission)


def buy_cash_required(price: float, shares: int,
                      config: ExecutionConfig) -> float:
    amount = float(price) * int(shares)
    return amount + commission_for(amount, config)


def sell_cash_received(price: float, shares: int,
                       config: ExecutionConfig) -> float:
    amount = float(price) * int(shares)
    return (
        amount
        - commission_for(amount, config)
        - amount * config.stamp_tax_rate
    )


def max_affordable_shares(cash: float, price: float,
                          config: ExecutionConfig) -> int:
    """返回不超过现金且符合整手约束的最大股数。"""
    if cash <= 0 or price <= 0:
        return 0
    shares = floor(cash / price / config.lot_size) * config.lot_size
    while shares > 0 and buy_cash_required(price, shares, config) > cash:
        shares -= config.lot_size
    return max(0, shares)


def execute_order(
    side: str,
    requested_shares: int,
    reference_price: float,
    config: ExecutionConfig,
    *,
    cash: Optional[float] = None,
    available_shares: Optional[int] = None,
    bar: Optional[Mapping[str, Any]] = None,
) -> ExecutionFill:
    """执行一张买卖单，并返回包含拒绝/缩量原因的结构化结果。"""
    if side not in {BUY, SELL}:
        raise ValueError("side 仅支持 buy/sell")
    if reference_price <= 0:
        return _rejected(side, requested_shares, reference_price, "invalid_price")
    if requested_shares <= 0:
        return _rejected(side, requested_shares, reference_price, "invalid_quantity")

    shares = requested_shares // config.lot_size * config.lot_size
    if shares <= 0:
        return _rejected(side, requested_shares, reference_price, "below_lot_size")

    if config.reject_one_price_limit and _blocked_by_one_price_limit(
        side, bar, config.price_limit_pct
    ):
        return _rejected(side, requested_shares, reference_price,
                         "one_price_limit")

    fill_price = _slipped_price(side, reference_price, config)
    reasons = []
    capacity_shares = _capacity_shares(fill_price, bar, config)
    if capacity_shares is not None:
        if capacity_shares < shares:
            reasons.append("amount_capacity")
        shares = min(shares, capacity_shares)
    if side == BUY:
        if cash is None:
            raise ValueError("买入订单必须提供 cash")
        affordable = max_affordable_shares(cash, fill_price, config)
        if affordable < shares:
            reasons.append("cash_limit")
        shares = min(shares, affordable)
    else:
        if available_shares is None:
            raise ValueError("卖出订单必须提供 available_shares")
        if available_shares < shares:
            reasons.append("position_limit")
        shares = min(shares, available_shares)
        shares = shares // config.lot_size * config.lot_size

    if shares <= 0:
        reason = "insufficient_cash" if side == BUY else "insufficient_position"
        if capacity_shares == 0:
            reason = "amount_capacity"
        return _rejected(side, requested_shares, reference_price, reason)

    gross = fill_price * shares
    commission = commission_for(gross, config)
    stamp_tax = gross * config.stamp_tax_rate if side == SELL else 0.0
    cash_delta = -(gross + commission) if side == BUY else (
        gross - commission - stamp_tax
    )
    status = "filled" if shares == requested_shares else "partial"

    return ExecutionFill(
        side=side,
        requested_shares=requested_shares,
        filled_shares=shares,
        reference_price=round(float(reference_price), 6),
        fill_price=round(fill_price, 6),
        gross_amount=round(gross, 6),
        commission=round(commission, 6),
        stamp_tax=round(stamp_tax, 6),
        cash_delta=round(cash_delta, 6),
        status=status,
        reason="+".join(dict.fromkeys(reasons)),
    )


def _slipped_price(side: str, reference_price: float,
                   config: ExecutionConfig) -> float:
    direction = 1 if side == BUY else -1
    price = reference_price * (1 + direction * config.slippage_bps / 10_000)
    if config.price_tick:
        ticks = price / config.price_tick
        ticks = ceil(ticks - 1e-12) if side == BUY else floor(ticks + 1e-12)
        price = ticks * config.price_tick
    return float(price)


def _capacity_shares(price: float, bar: Optional[Mapping[str, Any]],
                     config: ExecutionConfig) -> Optional[int]:
    if config.max_amount_participation is None:
        return None
    amount = _bar_value(bar, "amount", "成交额")
    if amount is None or amount <= 0:
        return 0
    capacity = amount * config.max_amount_participation
    return floor(capacity / price / config.lot_size) * config.lot_size


def _blocked_by_one_price_limit(side: str, bar: Optional[Mapping[str, Any]],
                                limit_pct: float) -> bool:
    high = _bar_value(bar, "high", "最高")
    low = _bar_value(bar, "low", "最低")
    close = _bar_value(bar, "close", "收盘")
    prev_close = _bar_value(bar, "prev_close", "前收")
    if None in {high, low, close, prev_close} or prev_close <= 0:
        return False
    if abs(high - low) > max(1e-9, close * 1e-8):
        return False
    change_pct = (close / prev_close - 1) * 100
    return (
        side == BUY and change_pct >= limit_pct - 0.05
    ) or (
        side == SELL and change_pct <= -limit_pct + 0.05
    )


def _bar_value(bar: Optional[Mapping[str, Any]], *keys: str):
    if bar is None:
        return None
    for key in keys:
        try:
            value = bar.get(key)
        except AttributeError:
            value = None
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _rejected(side: str, requested_shares: int, reference_price: float,
              reason: str) -> ExecutionFill:
    return ExecutionFill(
        side=side,
        requested_shares=requested_shares,
        filled_shares=0,
        reference_price=float(reference_price),
        fill_price=None,
        gross_amount=0.0,
        commission=0.0,
        stamp_tax=0.0,
        cash_delta=0.0,
        status="rejected",
        reason=reason,
    )
