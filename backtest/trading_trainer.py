"""无未来数据的 T+1 手动交易训练状态机。

行情加载器可以在服务端持有完整历史，但 ``state()`` 只返回当前播放位置之前的
分钟线，以及当前日期之前的日线和一根由已播放分钟合成的当日 K 线。
"""

from dataclasses import asdict, dataclass, replace
from math import isfinite
from typing import Callable, Dict, List, Sequence

from backtest.execution import (
    BUY, SELL, ExecutionConfig, buy_cash_required, execute_order,
)
from features.revealed import compute_revealed_indicators


DayLoader = Callable[[str, str], Dict]
HistoryLoader = Callable[[str, str, int], Sequence[Dict]]

PRICE_ABOVE = "price_above"
PRICE_BELOW = "price_below"
DAY_GAIN = "day_gain"
DAY_LOSS = "day_loss"
TAKE_PROFIT = "take_profit"
STOP_LOSS = "stop_loss"
REBOUND_BUY = "rebound_buy"
PULLBACK_SELL = "pullback_sell"
TAKE_PROFIT_STOP_LOSS = "take_profit_stop_loss"

CONDITION_TYPES = {
    PRICE_ABOVE, PRICE_BELOW, DAY_GAIN, DAY_LOSS,
    TAKE_PROFIT, STOP_LOSS, REBOUND_BUY, PULLBACK_SELL,
    TAKE_PROFIT_STOP_LOSS,
}
PERCENT_CONDITIONS = {
    DAY_GAIN, DAY_LOSS, TAKE_PROFIT, STOP_LOSS,
    REBOUND_BUY, PULLBACK_SELL, TAKE_PROFIT_STOP_LOSS,
}
TWO_STAGE_CONDITIONS = {REBOUND_BUY, PULLBACK_SELL}
DUAL_VALUE_CONDITIONS = TWO_STAGE_CONDITIONS | {TAKE_PROFIT_STOP_LOSS}
LIVE_CONDITION_STATUSES = {"active", "waiting_execution"}
RETRIABLE_CONDITION_REASONS = {
    "t_plus_one_locked", "insufficient_position", "insufficient_cash",
}
SIDE_CONDITIONS = {
    BUY: {PRICE_ABOVE, PRICE_BELOW, DAY_GAIN, DAY_LOSS, REBOUND_BUY},
    SELL: {
        PRICE_ABOVE, PRICE_BELOW, DAY_GAIN, DAY_LOSS,
        TAKE_PROFIT, STOP_LOSS, PULLBACK_SELL, TAKE_PROFIT_STOP_LOSS,
    },
}


@dataclass(frozen=True)
class TrainerConfig:
    initial_capital: float = 100_000.0
    history_days: int = 60
    execution: ExecutionConfig = ExecutionConfig(
        commission_rate=0.0003,
        min_commission=5.0,
        stamp_tax_rate=0.0005,
        price_tick=0.001,
    )

    def __post_init__(self):
        if self.initial_capital <= 0:
            raise ValueError("初始资金必须大于 0")
        if self.history_days <= 0:
            raise ValueError("日 K 回看天数必须大于 0")


@dataclass
class InventoryLot:
    buy_date: str
    shares: int
    total_cost: float


class TradingTrainerSession:
    """一只标的、连续多个交易日的手动训练会话。"""

    def __init__(
        self,
        *,
        code: str,
        name: str,
        dates: Sequence[str],
        start_date: str,
        day_loader: DayLoader,
        history_loader: HistoryLoader,
        config: TrainerConfig = TrainerConfig(),
    ):
        ordered = list(dates)
        if not ordered or ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
            raise ValueError("交易日必须是非空、无重复的升序序列")
        if start_date not in ordered:
            raise ValueError(f"{start_date} 不在该标的分钟缓存中")
        self.code = str(code)
        self.name = str(name)
        self.dates = ordered
        self.day_index = ordered.index(start_date)
        self.day_loader = day_loader
        self.history_loader = history_loader
        self.config = config
        self.cash = float(config.initial_capital)
        self.lots: List[InventoryLot] = []
        self.orders: List[Dict] = []
        self.pending_orders: List[Dict] = []
        self.conditional_orders: List[Dict] = []
        self._pending_sequence = 0
        self._condition_sequence = 0
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self._load_current_day()
        self.start_reference_price = float(self.points[0]["open"])

    @property
    def current_date(self) -> str:
        return self.dates[self.day_index]

    @property
    def current_point(self) -> Dict:
        return self.points[self.cursor]

    @property
    def total_shares(self) -> int:
        return sum(lot.shares for lot in self.lots)

    @property
    def settled_shares(self) -> int:
        """A 股 T+1：买入日期严格早于当前日期的份额才可卖。"""
        return sum(
            lot.shares for lot in self.lots
            if lot.buy_date < self.current_date
        )

    @property
    def reserved_cash(self) -> float:
        return sum(
            float(order["reserved_cash"])
            for order in self.pending_orders
            if order["status"] == "pending"
        )

    @property
    def available_cash(self) -> float:
        return max(0.0, self.cash - self.reserved_cash)

    @property
    def reserved_shares(self) -> int:
        return sum(
            int(order["reserved_shares"])
            for order in self.pending_orders
            if order["status"] == "pending"
        )

    @property
    def available_shares(self) -> int:
        """已过 T+1 且未被普通限价卖单冻结的份额。"""
        return max(0, self.settled_shares - self.reserved_shares)

    def advance(self, steps: int = 1) -> Dict:
        if not isinstance(steps, int) or steps <= 0:
            raise ValueError("推进分钟数必须是正整数")
        target = min(self.cursor + steps, len(self.points) - 1)
        # 必须逐分钟检查，避免 5/15 分钟快进跨过条件触发点。
        while self.cursor < target:
            self.cursor += 1
            # 普通限价委托已冻结资源，同一分钟优先于未冻结资源的条件单。
            self._evaluate_pending_orders()
            self._evaluate_conditionals()
        return self.state()

    def next_day(self) -> Dict:
        if self.cursor != len(self.points) - 1:
            raise ValueError("必须先播放到当日收盘，才能进入下一交易日")
        if self.day_index >= len(self.dates) - 1:
            raise ValueError("分钟缓存中已经没有下一个交易日")
        for condition in self.conditional_orders:
            if (
                condition["status"] in LIVE_CONDITION_STATUSES
                and condition["validity"] == "day"
            ):
                condition.update({
                    "status": "expired",
                    "finished_date": self.current_date,
                    "finished_time": self.current_point["time"],
                })
        for order in self.pending_orders:
            if order["status"] == "pending" and order["validity"] == "day":
                order.update({
                    "status": "expired",
                    "finished_date": self.current_date,
                    "finished_time": self.current_point["time"],
                })
        self.day_index += 1
        self._load_current_day()
        self._evaluate_pending_orders()
        self._evaluate_conditionals()
        return self.state()

    def place_order(
        self,
        side: str,
        shares: int,
        note: str = "",
        order_type: str = "market",
        limit_price: float = None,
        validity: str = "day",
    ) -> Dict:
        """提交立即成交或限价委托。

        立即成交订单完成后不可撤。未到价的限价单会冻结相应资金/可卖持仓，
        在后续已揭示分钟触价成交，也可以在成交前撤销。
        """
        if side not in {BUY, SELL}:
            raise ValueError("交易方向仅支持 buy/sell")
        requested = self._validate_shares(shares)
        order_type = str(order_type or "market").strip()
        if order_type == "market":
            self._execute_order(
                side, requested, note, source="manual", order_type="market"
            )
            return self.state()
        if order_type != "limit":
            raise ValueError("委托方式仅支持立即成交或限价委托")

        try:
            wanted_price = float(limit_price)
        except (TypeError, ValueError):
            raise ValueError("限价委托必须填写委托价格")
        if not isfinite(wanted_price) or wanted_price <= 0:
            raise ValueError("委托价格必须是大于 0 的数字")
        tick = self.config.execution.price_tick
        if tick:
            normalized_price = round(wanted_price / tick) * tick
            if abs(normalized_price - wanted_price) > max(1e-9, tick * 1e-7):
                raise ValueError(f"委托价格必须是 {tick:g} 元的整数倍")
            wanted_price = normalized_price
        validity = str(validity or "day").strip()
        if validity not in {"day", "session"}:
            raise ValueError("有效期仅支持当日或本次训练")

        current_price = float(self.current_point["close"])
        marketable = (
            side == BUY and wanted_price >= current_price
        ) or (
            side == SELL and wanted_price <= current_price
        )
        if marketable:
            self._execute_order(
                side, requested, note, source="manual",
                order_type="limit", reference_price=current_price,
                execution_config=replace(
                    self.config.execution, slippage_bps=0
                ),
            )
            return self.state()

        reserved_cash = 0.0
        reserved_shares = 0
        if side == BUY:
            reserved_cash = buy_cash_required(
                wanted_price, requested, self.config.execution
            )
            if reserved_cash > self.available_cash + 1e-9:
                raise ValueError("可用现金不足，无法冻结该限价买单资金")
        else:
            if requested > self.available_shares:
                if self.total_shares > 0 and self.settled_shares == 0:
                    raise ValueError("T+1 锁定：当日买入持仓不可卖")
                raise ValueError("可卖持仓不足，无法冻结该限价卖单持仓")
            reserved_shares = requested

        self._pending_sequence += 1
        point = self.current_point
        self.pending_orders.append({
            "id": "L{:04d}".format(self._pending_sequence),
            "side": side,
            "order_type": "limit",
            "limit_price": round(wanted_price, 6),
            "shares": requested,
            "note": str(note or "").strip()[:200],
            "validity": validity,
            "status": "pending",
            "created_date": self.current_date,
            "created_time": point["time"],
            "reserved_cash": round(reserved_cash, 6),
            "reserved_shares": reserved_shares,
            "filled_date": None,
            "filled_time": None,
            "filled_price": None,
            "filled_shares": 0,
            "result_reason": None,
        })
        return self.state()

    def cancel_pending_order(self, order_id: str) -> Dict:
        wanted = str(order_id or "").strip()
        for order in self.pending_orders:
            if order["id"] != wanted:
                continue
            if order["status"] != "pending":
                raise ValueError("只有待成交限价委托可以撤销")
            order.update({
                "status": "cancelled",
                "finished_date": self.current_date,
                "finished_time": self.current_point["time"],
            })
            return self.state()
        raise LookupError("限价委托不存在")

    def create_conditional_order(
        self,
        side: str,
        condition_type: str,
        trigger_value: float,
        shares: int,
        note: str = "",
        validity: str = "day",
        secondary_trigger_value: float = None,
    ) -> Dict:
        """创建条件单并立即检查当前已揭示行情，不冻结资金或持仓。"""
        if side not in {BUY, SELL}:
            raise ValueError("交易方向仅支持 buy/sell")
        condition_type = str(condition_type or "").strip()
        if condition_type not in CONDITION_TYPES:
            raise ValueError("不支持的条件类型")
        if condition_type not in SIDE_CONDITIONS[side]:
            raise ValueError("该条件类型不支持当前买卖方向")
        try:
            trigger = float(trigger_value)
        except (TypeError, ValueError):
            raise ValueError("触发值必须是数字")
        if trigger <= 0:
            raise ValueError("触发值必须大于 0")
        if condition_type in PERCENT_CONDITIONS and trigger > 100:
            raise ValueError("百分比触发值不能大于 100%")
        secondary_trigger = None
        if condition_type in DUAL_VALUE_CONDITIONS:
            try:
                secondary_trigger = float(secondary_trigger_value)
            except (TypeError, ValueError):
                raise ValueError("该条件必须填写第二阶段触发比例")
            if not 0 < secondary_trigger <= 100:
                raise ValueError("第二阶段触发比例必须在 0% 到 100% 之间")
        requested = self._validate_shares(shares)
        validity = str(validity or "day").strip()
        if validity not in {"day", "session"}:
            raise ValueError("有效期仅支持当日或本次训练")
        if side == SELL and self.total_shares <= 0:
            raise ValueError("当前没有持仓，不能创建卖出条件单")
        if side == SELL and requested > self.total_shares:
            raise ValueError("卖出条件单数量不能超过当前总持仓")

        self._condition_sequence += 1
        point = self.current_point
        condition = {
            "id": "C{:04d}".format(self._condition_sequence),
            "side": side,
            "condition_type": condition_type,
            "trigger_value": round(trigger, 6),
            "secondary_trigger_value": (
                round(secondary_trigger, 6)
                if secondary_trigger is not None else None
            ),
            "shares": requested,
            "note": str(note or "").strip()[:200],
            "validity": validity,
            "status": "active",
            "created_date": self.current_date,
            "created_time": point["time"],
            "reference_price": round(float(point["close"]), 6),
            "stage": (
                "waiting_activation"
                if condition_type in TWO_STAGE_CONDITIONS else "monitoring"
            ),
            "extreme_price": round(float(point["close"]), 6),
            "activated_date": None,
            "activated_time": None,
            "activation_price": None,
            "triggered_date": None,
            "triggered_time": None,
            "triggered_price": None,
            "result_reason": None,
            "matched_branch": None,
        }
        self.conditional_orders.append(condition)
        # 条件单在当前分钟收盘后创建，不能倒用本分钟更早的最高/最低价。
        current_price = float(point["close"])
        current_snapshot = dict(point)
        current_snapshot.update({
            "open": current_price, "high": current_price,
            "low": current_price, "close": current_price,
        })
        self._evaluate_conditionals(
            condition_ids={condition["id"]}, point_override=current_snapshot
        )
        return self.state()

    def cancel_conditional_order(self, condition_id: str) -> Dict:
        wanted = str(condition_id or "").strip()
        for condition in self.conditional_orders:
            if condition["id"] != wanted:
                continue
            if condition["status"] not in LIVE_CONDITION_STATUSES:
                raise ValueError("只有监控中或已触发待执行的条件单可以撤销")
            condition.update({
                "status": "cancelled",
                "finished_date": self.current_date,
                "finished_time": self.current_point["time"],
            })
            return self.state()
        raise LookupError("条件单不存在")

    def _validate_shares(self, shares: int) -> int:
        try:
            requested = int(shares)
        except (TypeError, ValueError):
            raise ValueError("委托数量必须是整数")
        lot_size = self.config.execution.lot_size
        if requested <= 0 or requested % lot_size:
            raise ValueError(f"委托数量必须是 {lot_size} 股的正整数倍")
        return requested

    def _execute_order(
        self,
        side: str,
        shares: int,
        note: str = "",
        *,
        source: str,
        conditional_order_id: str = None,
        condition_type: str = None,
        pending_order_id: str = None,
        order_type: str = "market",
        reference_price: float = None,
        execution_config: ExecutionConfig = None,
    ) -> Dict:
        if side not in {BUY, SELL}:
            raise ValueError("交易方向仅支持 buy/sell")
        requested = self._validate_shares(shares)
        point = self.current_point
        reference_price = (
            float(point["close"])
            if reference_price is None else float(reference_price)
        )
        execution_config = execution_config or self.config.execution
        available = self.available_shares
        bar = {
            "open": point["open"], "high": point["high"],
            "low": point["low"], "close": point["close"],
            "amount": point.get("amount"), "prev_close": self.prev_close,
        }
        fill = execute_order(
            side, requested, reference_price, execution_config,
            cash=self.available_cash if side == BUY else None,
            available_shares=available if side == SELL else None,
            bar=bar,
        )
        event = asdict(fill)
        event.update({
            "date": self.current_date,
            "time": point["time"],
            "note": str(note or "").strip()[:200],
            "t_plus_one_available": available,
            "source": source,
            "conditional_order_id": conditional_order_id,
            "condition_type": condition_type,
            "pending_order_id": pending_order_id,
            "order_type": order_type,
        })
        if fill.filled:
            self.cash += fill.cash_delta
            self.total_fees += fill.commission + fill.stamp_tax
            if side == BUY:
                self.lots.append(InventoryLot(
                    buy_date=self.current_date,
                    shares=fill.filled_shares,
                    total_cost=-fill.cash_delta,
                ))
                event["realized_pnl"] = None
            else:
                allocated_cost = self._consume_available_lots(fill.filled_shares)
                pnl = fill.cash_delta - allocated_cost
                self.realized_pnl += pnl
                event["realized_pnl"] = round(pnl, 4)
        else:
            event["realized_pnl"] = None
            if side == SELL and self.total_shares > 0 and self.settled_shares == 0:
                event["reason"] = "t_plus_one_locked"
        self.orders.append(event)
        return event

    def _evaluate_pending_orders(self) -> None:
        point = self.current_point
        low = float(point["low"])
        high = float(point["high"])
        open_price = float(point["open"])
        for order in self.pending_orders:
            if order["status"] != "pending":
                continue
            limit_price = float(order["limit_price"])
            touched = (
                order["side"] == BUY and low <= limit_price
            ) or (
                order["side"] == SELL and high >= limit_price
            )
            if not touched:
                continue
            # 跳空按开盘价获得价格改善，否则保守地按限价成交；绝不劣于限价。
            fill_reference = (
                min(limit_price, open_price)
                if order["side"] == BUY
                else max(limit_price, open_price)
            )
            # 先离开 pending 状态，释放本单冻结资源，再进入统一撮合。
            order["status"] = "filling"
            event = self._execute_order(
                order["side"], order["shares"], order["note"],
                source="limit", pending_order_id=order["id"],
                order_type="limit", reference_price=fill_reference,
                execution_config=replace(
                    self.config.execution, slippage_bps=0
                ),
            )
            filled_shares = int(event.get("filled_shares") or 0)
            order.update({
                "status": (
                    "filled" if filled_shares == order["shares"]
                    else "partial" if filled_shares > 0 else "failed"
                ),
                "filled_date": self.current_date,
                "filled_time": point["time"],
                "filled_price": event.get("fill_price"),
                "filled_shares": filled_shares,
                "result_reason": event.get("reason"),
            })

    def _evaluate_conditionals(self, condition_ids=None, point_override=None) -> None:
        point = point_override or self.current_point
        for condition in self.conditional_orders:
            if condition["status"] not in LIVE_CONDITION_STATUSES:
                continue
            if condition_ids is not None and condition["id"] not in condition_ids:
                continue
            if condition["status"] == "waiting_execution":
                if not self._condition_resources_ready(condition):
                    continue
                self._attempt_conditional_order(condition)
                continue
            triggered = self._condition_triggered(condition, point)
            if not triggered:
                continue
            condition.update({
                "triggered_date": self.current_date,
                "triggered_time": point["time"],
                "triggered_price": round(float(point["close"]), 6),
            })
            self._attempt_conditional_order(condition)

    def _condition_resources_ready(self, condition: Dict) -> bool:
        if condition["side"] == SELL:
            return self.available_shares > 0
        estimated_cash = buy_cash_required(
            float(self.current_point["close"]),
            int(condition["shares"]),
            self.config.execution,
        )
        return estimated_cash <= self.available_cash + 1e-9

    def _attempt_conditional_order(self, condition: Dict) -> None:
        event = self._execute_order(
            condition["side"], condition["shares"], condition["note"],
            source="conditional",
            conditional_order_id=condition["id"],
            condition_type=condition["condition_type"],
        )
        condition.update({
            "last_attempt_date": self.current_date,
            "last_attempt_time": self.current_point["time"],
            "result_reason": event.get("reason"),
        })
        if event.get("filled_shares", 0) > 0:
            condition.update({"status": "triggered", "stage": "completed"})
        elif event.get("reason") in RETRIABLE_CONDITION_REASONS:
            condition.update({
                "status": "waiting_execution",
                "stage": "waiting_execution",
            })
        else:
            condition.update({"status": "failed", "stage": "failed"})

    def _condition_triggered(self, condition: Dict, point: Dict) -> bool:
        kind = condition["condition_type"]
        value = float(condition["trigger_value"])
        high = float(point["high"])
        low = float(point["low"])
        close = float(point["close"])
        if kind == PRICE_ABOVE:
            return high >= value
        if kind == PRICE_BELOW:
            return low <= value
        if kind == DAY_GAIN:
            return (high / self.prev_close - 1) * 100 >= value
        if kind == DAY_LOSS:
            return (low / self.prev_close - 1) * 100 <= -value
        if kind in {TAKE_PROFIT, STOP_LOSS, TAKE_PROFIT_STOP_LOSS}:
            if not self.total_shares:
                return False
            average_cost = sum(lot.total_cost for lot in self.lots) / self.total_shares
            if kind == TAKE_PROFIT_STOP_LOSS:
                loss_value = float(condition["secondary_trigger_value"])
                # 分钟 OHLC 无法确定高低点先后；同一分钟双双触发时风险优先。
                if (low / average_cost - 1) * 100 <= -loss_value:
                    condition["matched_branch"] = STOP_LOSS
                    return True
                if (high / average_cost - 1) * 100 >= value:
                    condition["matched_branch"] = TAKE_PROFIT
                    return True
                return False
            if kind == TAKE_PROFIT:
                return (high / average_cost - 1) * 100 >= value
            return (low / average_cost - 1) * 100 <= -value
        if kind == REBOUND_BUY:
            reference_price = float(condition["reference_price"])
            if condition["stage"] == "waiting_activation":
                activation_price = reference_price * (1 - value / 100)
                if low > activation_price:
                    return False
                condition.update({
                    "stage": "tracking_rebound",
                    "activated_date": self.current_date,
                    "activated_time": point["time"],
                    "activation_price": round(low, 6),
                    "extreme_price": round(low, 6),
                })
            condition["extreme_price"] = round(
                min(float(condition["extreme_price"]), low), 6
            )
            rebound = float(condition["secondary_trigger_value"])
            return close >= float(condition["extreme_price"]) * (1 + rebound / 100)
        if kind == PULLBACK_SELL:
            reference_price = float(condition["reference_price"])
            if condition["stage"] == "waiting_activation":
                activation_price = reference_price * (1 + value / 100)
                if high < activation_price:
                    return False
                condition.update({
                    "stage": "tracking_pullback",
                    "activated_date": self.current_date,
                    "activated_time": point["time"],
                    "activation_price": round(high, 6),
                    "extreme_price": round(high, 6),
                })
            condition["extreme_price"] = round(
                max(float(condition["extreme_price"]), high), 6
            )
            pullback = float(condition["secondary_trigger_value"])
            return close <= float(condition["extreme_price"]) * (1 - pullback / 100)
        return False

    def state(self) -> Dict:
        point = self.current_point
        current_price = float(point["close"])
        position_value = self.total_shares * current_price
        remaining_cost = sum(lot.total_cost for lot in self.lots)
        equity = self.cash + position_value
        total_pnl = equity - self.config.initial_capital
        partial_bar = self._partial_daily_bar()
        market_indicators = compute_revealed_indicators(
            self.historical_daily,
            partial_bar,
            point,
            self.prev_close,
        )
        daily_history = [dict(row) for row in self.historical_daily]
        daily_history.append(partial_bar)
        revealed = [dict(point) for point in self.points[: self.cursor + 1]]
        return {
            "code": self.code,
            "name": self.name,
            "date": self.current_date,
            "time": point["time"],
            "prev_close": round(self.prev_close, 6),
            "minute_points": revealed,
            "daily_bars": daily_history,
            "orders": [dict(order) for order in self.orders],
            "pending_orders": [dict(order) for order in self.pending_orders],
            "conditional_orders": [dict(order) for order in self.conditional_orders],
            "progress": {
                "revealed": self.cursor + 1,
                "total": len(self.points),
                "day_complete": self.cursor == len(self.points) - 1,
                "has_next_day": self.day_index < len(self.dates) - 1,
                "active_condition_count": sum(
                    order["status"] in LIVE_CONDITION_STATUSES
                    for order in self.conditional_orders
                ),
                "pending_order_count": sum(
                    order["status"] == "pending"
                    for order in self.pending_orders
                ),
            },
            "account": {
                "initial_capital": round(self.config.initial_capital, 2),
                "cash": round(self.cash, 2),
                "available_cash": round(self.available_cash, 2),
                "reserved_cash": round(self.reserved_cash, 2),
                "total_shares": self.total_shares,
                "settled_shares": self.settled_shares,
                "available_shares": self.available_shares,
                "reserved_shares": self.reserved_shares,
                "locked_shares": self.total_shares - self.settled_shares,
                "position_value": round(position_value, 2),
                "cost_basis": round(remaining_cost, 2),
                "average_cost": (
                    round(remaining_cost / self.total_shares, 6)
                    if self.total_shares else None
                ),
                "unrealized_pnl": round(position_value - remaining_cost, 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "total_fees": round(self.total_fees, 2),
                "equity": round(equity, 2),
                "total_pnl": round(total_pnl, 2),
                "return_pct": round(
                    total_pnl / self.config.initial_capital * 100, 4
                ),
            },
            "market": {
                "price": round(current_price, 6),
                "change_pct": round((current_price / self.prev_close - 1) * 100, 4),
                "high": partial_bar["high"],
                "low": partial_bar["low"],
                "benchmark_return_pct": round(
                    (current_price / self.start_reference_price - 1) * 100, 4
                ),
                "indicators": market_indicators,
            },
            "assumptions": {
                "settlement": "T+1",
                "fill_reference": "当前已显示分钟收盘价",
                "limit_fill": "触价按限价成交；跳空时按不劣于限价的开盘价成交",
                "future_data_exposed": False,
                "execution": asdict(self.config.execution),
            },
        }

    def _load_current_day(self) -> None:
        payload = self.day_loader(self.code, self.current_date)
        points = list(payload.get("points") or [])
        if not points:
            raise ValueError(f"{self.code} 在 {self.current_date} 没有分钟数据")
        self.points = points
        self.prev_close = float(payload["prev_close"])
        self.cursor = 0
        self.historical_daily = [
            dict(row) for row in self.history_loader(
                self.code, self.current_date, self.config.history_days
            )
            if str(row["date"]) < self.current_date
        ][-self.config.history_days:]

    def _partial_daily_bar(self) -> Dict:
        visible = self.points[: self.cursor + 1]
        close = round(float(visible[-1]["close"]), 6)
        return {
            "date": self.current_date,
            "open": round(float(visible[0]["open"]), 6),
            "high": round(max(float(row["high"]) for row in visible), 6),
            "low": round(min(float(row["low"]) for row in visible), 6),
            "close": close,
            "pre_close": round(self.prev_close, 6),
            "change_pct": round((close / self.prev_close - 1) * 100, 4),
            "amount": round(sum(float(row.get("amount") or 0) for row in visible), 2),
            "volume": round(sum(float(row.get("volume") or 0) for row in visible), 2),
            # 当日换手率必须等收盘事实或实时流通股本才能准确计算，训练中不填全天值。
            "turnover_rate": None,
            "partial": self.cursor < len(self.points) - 1,
        }

    def _consume_available_lots(self, shares: int) -> float:
        remaining = shares
        allocated_cost = 0.0
        kept: List[InventoryLot] = []
        for lot in self.lots:
            if remaining <= 0 or lot.buy_date >= self.current_date:
                kept.append(lot)
                continue
            consumed = min(remaining, lot.shares)
            cost = lot.total_cost * consumed / lot.shares
            allocated_cost += cost
            lot.shares -= consumed
            lot.total_cost -= cost
            remaining -= consumed
            if lot.shares > 0:
                kept.append(lot)
        if remaining:
            raise RuntimeError("可用持仓成本分配失败")
        self.lots = kept
        return allocated_cost
