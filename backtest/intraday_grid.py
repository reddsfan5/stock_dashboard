"""银河证券风格的日内 T+0 网格模拟核心。

``transaction_driven`` 在基准价两侧预埋限价单；一侧全部成交后撤销另一侧，
以成交价更新基准并重新双向挂单。``price_triggered`` 到价后才提交委托，不提前
冻结资金和证券，并以配置的滑点近似触发后的实际成交价格。

分钟 K 线无法知道真实分笔路径，模拟器使用确定性的 OHLC 路径假设决定同一分钟
的触发先后。该模块用于理解和回放，不等同于券商实盘成交结果。
"""

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Tuple


class IntradayGridError(ValueError):
    """网格参数或分钟数据不合法。"""


TRANSACTION_DRIVEN = "transaction_driven"
PRICE_TRIGGERED = "price_triggered"
GRID_MODES = {TRANSACTION_DRIVEN, PRICE_TRIGGERED}


@dataclass(frozen=True)
class IntradayGridConfig:
    mode: str = TRANSACTION_DRIVEN
    initial_cash: float = 100_000.0
    initial_shares: int = 1_000
    base_price: Optional[float] = None
    step_mode: str = "pct"
    grid_pct: float = 0.1
    buy_step: Optional[float] = None
    sell_step: Optional[float] = None
    lot_shares: int = 100
    buy_lot_shares: Optional[int] = None
    sell_lot_shares: Optional[int] = None
    multiplier_enabled: bool = False
    buy_multiplier: int = 1
    sell_multiplier: int = 1
    price_floor: Optional[float] = None
    price_ceiling: Optional[float] = None
    min_position: int = 0
    max_position: int = 5_000
    cage_to_market: bool = False
    price_cage_pct: float = 2.0
    rebound_enabled: bool = False
    rebound_value: float = 0.1
    pullback_enabled: bool = False
    pullback_value: float = 0.1
    turn_mode: str = "pct"
    floor_trigger_enabled: bool = False
    order_price_mode: str = "counterparty"
    order_offset_bps: float = 0.0
    base_update_timing: str = "filled"
    base_update_price: str = "grid"
    auto_cancel_enabled: bool = False
    auto_cancel_minutes: int = 1
    monitor_price_mode: str = "ohlc"
    after_close_update_base: bool = False
    validity_days: int = 90
    commission_rate: float = 0.0001
    min_commission: float = 0.0
    sell_tax_rate: float = 0.0
    slippage_rate: float = 0.0
    max_trades: int = 2_000

    def validate(self) -> None:
        if self.mode not in GRID_MODES:
            raise IntradayGridError("网格模式必须是成交驱动型或到价触发型")
        if self.step_mode not in {"pct", "diff"}:
            raise IntradayGridError("涨跌幅方式必须是比例或差价")
        if not 0 <= self.initial_cash <= 1_000_000_000:
            raise IntradayGridError("初始现金必须在 0～10亿元之间")
        if self.base_price is not None and self.base_price <= 0:
            raise IntradayGridError("初始基准价必须大于0")
        buy_step = self.buy_step if self.buy_step is not None else self.grid_pct
        sell_step = self.sell_step if self.sell_step is not None else self.grid_pct
        if self.step_mode == "pct":
            if not 0.01 <= buy_step <= 20 or not 0.01 <= sell_step <= 20:
                raise IntradayGridError("上涨和下跌比例必须在 0.01%～20% 之间")
        elif buy_step <= 0 or sell_step <= 0:
            raise IntradayGridError("上涨和下跌差价必须大于0")
        for name, value in (
            ("初始持仓", self.initial_shares),
            ("每格数量", self.lot_shares),
            ("每格买入数量", self.buy_lot_shares if self.buy_lot_shares is not None else self.lot_shares),
            ("每格卖出数量", self.sell_lot_shares if self.sell_lot_shares is not None else self.lot_shares),
            ("保留底仓", self.min_position),
            ("最大持仓", self.max_position),
        ):
            if value < 0 or value % 100 != 0:
                raise IntradayGridError(f"{name}必须是非负且为100的整数倍")
        if self.lot_shares <= 0:
            raise IntradayGridError("每格数量必须大于0")
        if self.buy_lot_shares == 0 or self.sell_lot_shares == 0:
            raise IntradayGridError("买入和卖出数量必须大于0")
        if not 1 <= self.buy_multiplier <= 100 or not 1 <= self.sell_multiplier <= 100:
            raise IntradayGridError("委托倍数必须在1～100之间")
        if self.price_floor is not None and self.price_floor <= 0:
            raise IntradayGridError("有效价格下限必须大于0")
        if self.price_ceiling is not None and self.price_ceiling <= 0:
            raise IntradayGridError("有效价格上限必须大于0")
        if self.price_floor is not None and self.price_ceiling is not None:
            if self.price_floor >= self.price_ceiling:
                raise IntradayGridError("有效价格下限必须小于上限")
        if self.min_position > self.initial_shares:
            raise IntradayGridError("保留底仓不能大于初始持仓")
        if self.initial_shares > self.max_position:
            raise IntradayGridError("初始持仓不能大于最大持仓")
        if not 0 <= self.commission_rate <= 0.02:
            raise IntradayGridError("佣金率范围不正确")
        if not 0 <= self.sell_tax_rate <= 0.02:
            raise IntradayGridError("卖出税费率范围不正确")
        if not 0 <= self.slippage_rate <= 0.01:
            raise IntradayGridError("滑点范围不正确")
        if not 0 <= self.min_commission <= 10_000:
            raise IntradayGridError("最低佣金范围不正确")
        if not 1 <= self.max_trades <= 20_000:
            raise IntradayGridError("最大成交笔数范围不正确")
        if not 0.1 <= self.price_cage_pct <= 20:
            raise IntradayGridError("模拟价格笼子范围必须在0.1%～20%之间")
        if self.turn_mode not in {"pct", "diff"}:
            raise IntradayGridError("累计反弹/回落方式必须是比例或差价")
        for name, enabled, value in (
            ("累计反弹", self.rebound_enabled, self.rebound_value),
            ("累计回落", self.pullback_enabled, self.pullback_value),
        ):
            if enabled and value <= 0:
                raise IntradayGridError(f"{name}必须大于0")
            if enabled and self.turn_mode == "pct" and value > 20:
                raise IntradayGridError(f"{name}比例不能超过20%")
        if self.order_price_mode not in {"counterparty", "trigger", "passive"}:
            raise IntradayGridError("委托价格方式不正确")
        if not 0 <= self.order_offset_bps <= 1_000:
            raise IntradayGridError("排队价偏移必须在0～1000基点之间")
        if self.base_update_timing not in {"triggered", "filled"}:
            raise IntradayGridError("基准价更新时间点不正确")
        if self.base_update_price not in {"grid", "trigger", "fill"}:
            raise IntradayGridError("基准价更新价格不正确")
        if self.base_update_timing == "triggered" and self.base_update_price == "fill":
            raise IntradayGridError("触发委托后更新基准时不能使用尚未产生的成交价")
        if not 1 <= self.auto_cancel_minutes <= 240:
            raise IntradayGridError("自动撤单等待时间必须在1～240分钟之间")
        if self.monitor_price_mode not in {"ohlc", "close"}:
            raise IntradayGridError("监控行情方式不正确")
        if not 1 <= self.validity_days <= 1_095:
            raise IntradayGridError("有效期必须在1～1095天之间")


def _round(value, digits=4):
    return round(float(value), digits)


def _fee(amount: float, side: str, config: IntradayGridConfig) -> float:
    commission = max(amount * config.commission_rate, config.min_commission)
    sell_tax = amount * config.sell_tax_rate if side == "sell" else 0.0
    return commission + sell_tax


def _bar_prices(bar: Dict, index: int) -> Tuple[float, float, float, float]:
    try:
        open_price = float(bar.get("open") or bar["close"])
        high = float(bar.get("high") or bar["close"])
        low = float(bar.get("low") or bar["close"])
        close = float(bar["close"])
    except (KeyError, TypeError, ValueError):
        raise IntradayGridError(f"第 {index + 1} 根分钟线价格无效")
    if (
        min(open_price, high, low, close) <= 0
        or high < max(open_price, close, low)
        or low > min(open_price, close, high)
    ):
        raise IntradayGridError(f"第 {index + 1} 根分钟线 OHLC 不合法")
    return open_price, high, low, close


def _intrabar_path(open_price: float, high: float, low: float, close: float) -> List[float]:
    """缺少分笔数据时，以常见 OHLC 启发式确定分钟内路径。"""
    if close >= open_price:
        return [open_price, low, high, close]
    return [open_price, high, low, close]


def simulate_intraday_grid(points: Iterable[Dict], config: IntradayGridConfig) -> Dict:
    """模拟一个交易日并返回适合前端逐分钟回放的 JSON。"""
    config.validate()
    bars = list(points)
    if not bars:
        raise IntradayGridError("分钟数据为空")

    first_open, _, _, _ = _bar_prices(bars[0], 0)
    cash = float(config.initial_cash)
    shares = int(config.initial_shares)
    average_cost = first_open if shares else 0.0
    anchor = float(config.base_price) if config.base_price is not None else first_open
    if config.price_floor is not None and anchor < config.price_floor:
        raise IntradayGridError("初始基准价不能低于有效价格下限")
    if config.price_ceiling is not None and anchor > config.price_ceiling:
        raise IntradayGridError("初始基准价不能高于有效价格上限")

    initial_equity = cash + shares * first_open
    realized_pnl = 0.0
    total_fee = 0.0
    min_shares = shares
    max_shares = shares
    trades: List[Dict] = []
    events: List[Dict] = []
    timeline: List[Dict] = []
    pending_orders: List[Dict] = []
    peak_equity = initial_equity
    max_drawdown = 0.0
    buy_step = config.buy_step if config.buy_step is not None else config.grid_pct
    sell_step = config.sell_step if config.sell_step is not None else config.grid_pct
    base_buy_lot = config.buy_lot_shares if config.buy_lot_shares is not None else config.lot_shares
    base_sell_lot = config.sell_lot_shares if config.sell_lot_shares is not None else config.lot_shares
    if config.mode == TRANSACTION_DRIVEN and config.multiplier_enabled:
        base_buy_lot *= config.buy_multiplier
        base_sell_lot *= config.sell_multiplier
    if config.step_mode == "diff" and buy_step >= anchor:
        raise IntradayGridError("下跌差价必须小于初始基准价")

    previous_close = first_open
    sequence = 0
    buy_tracker = {"active": False, "grid": None, "extreme": None}
    sell_tracker = {"active": False, "grid": None, "extreme": None}

    def levels(anchor_value: Optional[float] = None) -> Tuple[float, float]:
        value = anchor if anchor_value is None else anchor_value
        if config.step_mode == "diff":
            return value - buy_step, value + sell_step
        return value * (1 - buy_step / 100.0), value * (1 + sell_step / 100.0)

    def pending_reservations(exclude_order_id: Optional[int] = None) -> Tuple[float, int, int, int]:
        reserved_cash = 0.0
        reserved_shares = 0
        pending_buy_shares = 0
        pending_sell_shares = 0
        for order in pending_orders:
            if order["id"] == exclude_order_id:
                continue
            if order["side"] == "buy":
                amount = order["order_price"] * order["shares"]
                reserved_cash += amount + _fee(amount, "buy", config)
                pending_buy_shares += order["shares"]
            else:
                reserved_shares += order["shares"]
                pending_sell_shares += order["shares"]
        return reserved_cash, reserved_shares, pending_buy_shares, pending_sell_shares

    def order_state() -> Dict:
        buy_price, sell_price = levels()
        reserved_cash, reserved_shares, pending_buy_shares, pending_sell_shares = pending_reservations()
        buy_amount = buy_price * base_buy_lot
        buy_fee = _fee(buy_amount, "buy", config)
        buy_active = (
            buy_price > 0
            and (config.price_floor is None or buy_price >= config.price_floor)
            and shares + pending_buy_shares + base_buy_lot <= config.max_position
            and cash - reserved_cash + 1e-9 >= buy_amount + buy_fee
        )
        sell_active = (
            (config.price_ceiling is None or sell_price <= config.price_ceiling)
            and shares - pending_sell_shares - base_sell_lot >= config.min_position
        )
        if config.mode == TRANSACTION_DRIVEN:
            reserved_cash = buy_amount + buy_fee if buy_active else 0.0
            reserved_shares = base_sell_lot if sell_active else 0
        return {
            "buy_price": buy_price,
            "sell_price": sell_price,
            "buy_active": buy_active,
            "sell_active": sell_active,
            "reserved_cash": reserved_cash,
            "reserved_shares": reserved_shares,
            "buy_shares": base_buy_lot,
            "sell_shares": base_sell_lot,
        }

    def add_event(index: int, bar: Dict, event_type: str, side: str, price: float,
                  reason: str, **extra) -> Dict:
        nonlocal sequence
        event = {
            "id": len(events),
            "sequence": sequence,
            "index": index,
            "time": str(bar.get("time", index)),
            "type": event_type,
            "side": side,
            "price": _round(price),
            "reason": reason,
        }
        event.update(extra)
        events.append(event)
        sequence += 1
        return event

    def reset_trackers() -> None:
        buy_tracker.update(active=False, grid=None, extreme=None)
        sell_tracker.update(active=False, grid=None, extreme=None)

    def update_anchor(order: Dict, fill_price: Optional[float] = None) -> None:
        nonlocal anchor
        if config.base_update_price == "grid":
            anchor = float(order["grid_price"])
        elif config.base_update_price == "trigger":
            anchor = float(order["trigger_price"])
        elif fill_price is not None:
            anchor = float(fill_price)
        reset_trackers()

    def record_fill(order: Dict, fill_price: float, index: int, bar: Dict,
                    reason: str) -> Tuple[bool, str]:
        nonlocal cash, shares, average_cost, anchor, realized_pnl, total_fee, sequence
        if len(trades) >= config.max_trades:
            return False, "达到最大成交笔数"
        side = order["side"]
        trade_shares = order["shares"]
        amount = fill_price * trade_shares
        fee = _fee(amount, side, config)
        _, _, pending_buy_shares, pending_sell_shares = pending_reservations(
            exclude_order_id=order.get("id")
        )
        if side == "buy":
            if shares + pending_buy_shares + trade_shares > config.max_position:
                return False, "成交时达到最大持仓"
            if cash + 1e-9 < amount + fee:
                return False, "触发后因成交价变化导致现金不足"
            old_cost = average_cost * shares
            cash -= amount + fee
            shares += trade_shares
            average_cost = (old_cost + amount + fee) / shares
        else:
            if shares - pending_sell_shares - trade_shares < config.min_position:
                return False, "成交时触及保留底仓"
            net = amount - fee
            trade_pnl = net - average_cost * trade_shares
            cash += net
            shares -= trade_shares
            realized_pnl += trade_pnl
            if shares == 0:
                average_cost = 0.0

        total_fee += fee
        anchor_before = anchor
        if config.mode == TRANSACTION_DRIVEN or config.base_update_timing == "filled":
            if config.mode == TRANSACTION_DRIVEN:
                anchor = fill_price
                reset_trackers()
            else:
                update_anchor(order, fill_price)
        close_price = float(bar["close"])
        trade = {
            "id": len(trades),
            "sequence": sequence,
            "index": index,
            "time": str(bar.get("time", index)),
            "side": side,
            "mode": config.mode,
            "order_type": order["order_type"],
            "grid_price": _round(order["grid_price"]),
            "trigger_price": _round(order["trigger_price"]),
            "order_price": _round(order["order_price"]),
            "price": _round(fill_price),
            "shares": trade_shares,
            "multiplier": order.get("multiplier", 1),
            "amount": _round(amount, 2),
            "fee": _round(fee, 2),
            "cash": _round(cash, 2),
            "position": shares,
            "equity": _round(cash + shares * close_price, 2),
            "realized_pnl": _round(realized_pnl, 2),
            "reason": reason,
            "trigger_reason": order.get("trigger_reason", ""),
            "anchor_before": _round(anchor_before),
            "anchor_after": _round(anchor),
            "cage_converted": order.get("cage_converted", False),
            "cancelled_side": order.get("cancelled_side", ""),
        }
        if side == "sell":
            trade["trade_pnl"] = _round(trade_pnl, 2)
        trades.append(trade)
        sequence += 1
        return True, ""

    def transaction_execute(side: str, trigger: float, observed_price: float,
                            index: int, bar: Dict) -> Tuple[bool, str]:
        state = order_state()
        if side == "buy" and not state["buy_active"]:
            if config.price_floor is not None and trigger < config.price_floor:
                return False, "低于有效价格下限"
            if shares + base_buy_lot > config.max_position:
                return False, "达到最大持仓"
            return False, "现金不足"
        if side == "sell" and not state["sell_active"]:
            if config.price_ceiling is not None and trigger > config.price_ceiling:
                return False, "高于有效价格上限"
            return False, "触及保留底仓"

        cage_gap_pct = abs(trigger / observed_price - 1) * 100
        cage_converted = config.cage_to_market and cage_gap_pct > config.price_cage_pct
        fill_price = observed_price if cage_converted else trigger
        reason = (
            "限价超出模拟价格笼子，转市价成交后撤单重挂"
            if cage_converted else
            "预埋限价单全部成交，撤销反向委托并重新双挂"
        )
        order = {
            "id": -1,
            "side": side,
            "shares": base_buy_lot if side == "buy" else base_sell_lot,
            "grid_price": trigger,
            "trigger_price": trigger,
            "order_price": trigger,
            "order_type": "笼子外转市价" if cage_converted else "预埋限价单",
            "cage_converted": cage_converted,
            "cancelled_side": "sell" if side == "buy" else "buy",
        }
        return record_fill(order, fill_price, index, bar, reason)

    def turn_target(side: str, extreme: float, grid_price: float) -> Tuple[float, bool]:
        value = config.rebound_value if side == "buy" else config.pullback_value
        if config.turn_mode == "pct":
            normal = extreme * (1 + value / 100.0) if side == "buy" else extreme * (1 - value / 100.0)
        else:
            normal = extreme + value if side == "buy" else extreme - value
        if not config.floor_trigger_enabled:
            return normal, False
        if side == "buy" and normal >= grid_price:
            return grid_price, True
        if side == "sell" and normal <= grid_price:
            return grid_price, True
        return normal, False

    def grid_multiplier(side: str, observed_extreme: float, anchor_before: float) -> int:
        if not config.multiplier_enabled:
            return 1
        step = buy_step if side == "buy" else sell_step
        distance = anchor_before - observed_extreme if side == "buy" else observed_extreme - anchor_before
        one_grid = step if config.step_mode == "diff" else anchor_before * step / 100.0
        crossed = max(1, int((distance + 1e-10) // one_grid))
        cap = config.buy_multiplier if side == "buy" else config.sell_multiplier
        return min(crossed, cap)

    def submit_price_order(side: str, grid_price: float, trigger_price: float,
                           observed_extreme: float, trigger_reason: str,
                           index: int, bar: Dict) -> Tuple[bool, str]:
        nonlocal anchor
        anchor_before = anchor
        multiplier = grid_multiplier(side, observed_extreme, anchor_before)
        if config.multiplier_enabled and multiplier > 1:
            step = buy_step if side == "buy" else sell_step
            if config.step_mode == "diff":
                grid_price = anchor_before + (-step if side == "buy" else step) * multiplier
            else:
                direction = -1 if side == "buy" else 1
                grid_price = anchor_before * (1 + direction * step / 100.0 * multiplier)
        base_lot = base_buy_lot if side == "buy" else base_sell_lot
        trade_shares = base_lot * multiplier
        direction = 1 if side == "buy" else -1
        if config.order_price_mode == "counterparty":
            order_price = trigger_price * (1 + direction * config.slippage_rate)
            order_type = "到价触发委托"
            immediate_fill = True
        elif config.order_price_mode == "trigger":
            order_price = trigger_price
            order_type = "到价触发-触发价限价"
            immediate_fill = True
        else:
            offset = config.order_offset_bps / 10_000.0
            order_price = trigger_price * (1 - offset if side == "buy" else 1 + offset)
            order_type = "到价触发-排队限价"
            immediate_fill = False

        reserved_cash, reserved_shares, pending_buy_shares, pending_sell_shares = pending_reservations()
        amount = order_price * trade_shares
        fee = _fee(amount, side, config)
        if len(trades) >= config.max_trades:
            return False, "达到最大成交笔数"
        if config.price_floor is not None and grid_price < config.price_floor:
            return False, "低于有效价格下限"
        if config.price_ceiling is not None and grid_price > config.price_ceiling:
            return False, "高于有效价格上限"
        if side == "buy":
            if shares + pending_buy_shares + trade_shares > config.max_position:
                return False, "达到最大持仓"
            if cash - reserved_cash + 1e-9 < amount + fee:
                return False, "可用现金不足"
        else:
            if shares - pending_sell_shares - trade_shares < config.min_position:
                return False, "触及保留底仓"

        order = {
            "id": len(events) + len(trades) + len(pending_orders),
            "side": side,
            "shares": trade_shares,
            "multiplier": multiplier,
            "grid_price": grid_price,
            "trigger_price": trigger_price,
            "order_price": order_price,
            "order_type": order_type,
            "trigger_reason": trigger_reason,
            "submitted_index": index,
            "submitted_time": str(bar.get("time", index)),
        }
        add_event(
            index, bar, "trigger", side, trigger_price, trigger_reason,
            grid_price=_round(grid_price), order_price=_round(order_price),
            shares=trade_shares, multiplier=multiplier,
            status="已报单" if not immediate_fill else "报单并成交",
        )
        reset_trackers()
        if config.base_update_timing == "triggered":
            update_anchor(order)
        if immediate_fill:
            reason = f"{trigger_reason}；按{'对手价滑点近似' if config.order_price_mode == 'counterparty' else '触发价限价'}整笔成交"
            return record_fill(order, order_price, index, bar, reason)
        pending_orders.append(order)
        return True, ""

    def resolve_pending(index: int, bar: Dict, high: float, low: float) -> Tuple[List[int], str, bool]:
        """用后续分钟 OHLC 处理排队限价；返回成交ID、阻塞原因、是否仍在等待。"""
        minute_trade_ids: List[int] = []
        blocked = ""
        for order in list(pending_orders):
            age = index - order["submitted_index"]
            if config.auto_cancel_enabled and age >= config.auto_cancel_minutes:
                pending_orders.remove(order)
                add_event(index, bar, "cancel", order["side"], order["order_price"],
                          f"排队限价等待{config.auto_cancel_minutes}分钟未成交，自动撤单")
                continue
            crossed = (
                order["side"] == "buy" and low <= order["order_price"]
            ) or (
                order["side"] == "sell" and high >= order["order_price"]
            )
            if age <= 0 or not crossed:
                continue
            pending_orders.remove(order)
            before = len(trades)
            filled, blocked = record_fill(
                order, order["order_price"], index, bar,
                f"{order['trigger_reason']}；排队限价在后续分钟全部成交",
            )
            if filled:
                minute_trade_ids.extend(range(before, len(trades)))
            else:
                add_event(index, bar, "reject", order["side"], order["order_price"], blocked)
        waiting = bool(pending_orders) and config.base_update_timing == "filled"
        return minute_trade_ids, blocked, waiting

    def arm_tracker(side: str, grid_price: float, extreme: float,
                    index: int, bar: Dict) -> None:
        tracker = buy_tracker if side == "buy" else sell_tracker
        if tracker["active"]:
            if side == "buy":
                tracker["extreme"] = min(tracker["extreme"], extreme)
            else:
                tracker["extreme"] = max(tracker["extreme"], extreme)
            return
        tracker.update(active=True, grid=grid_price, extreme=extreme)
        add_event(
            index, bar, "armed", side, grid_price,
            "跌破买入网格，开始跟踪最低价并等待累计反弹"
            if side == "buy" else
            "突破卖出网格，开始跟踪最高价并等待累计回落",
        )

    def process_price_segment(segment_start: float, segment_end: float,
                              index: int, bar: Dict) -> Tuple[List[int], str]:
        minute_trade_ids: List[int] = []
        blocked = ""
        cursor = segment_start
        guard = 0
        while cursor != segment_end and guard < config.max_trades + 4:
            guard += 1
            buy_grid, sell_grid = levels()
            candidates: List[Tuple[float, str, float, float, str]] = []
            if segment_end > cursor:
                if buy_tracker["active"]:
                    target, floor_used = turn_target(
                        "buy", buy_tracker["extreme"], buy_tracker["grid"]
                    )
                    if cursor < target <= segment_end:
                        reason = "保底价触发买入" if floor_used else "累计反弹触发买入"
                        candidates.append((target, "buy", buy_tracker["grid"], buy_tracker["extreme"], reason))
                if not config.pullback_enabled and cursor < sell_grid <= segment_end:
                    trigger = segment_end if config.multiplier_enabled else sell_grid
                    reason = "跳格到价触发卖出" if config.multiplier_enabled else "到达卖出网格"
                    candidates.append((trigger, "sell", sell_grid, segment_end, reason))
                if candidates:
                    target, side, grid_price, extreme, reason = min(candidates, key=lambda item: item[0])
                    before = len(trades)
                    submitted, blocked = submit_price_order(
                        side, grid_price, target, extreme, reason, index, bar
                    )
                    if not submitted:
                        add_event(index, bar, "reject", side, target, blocked)
                        break
                    minute_trade_ids.extend(range(before, len(trades)))
                    cursor = target
                    if config.base_update_timing == "filled" and pending_orders:
                        break
                    continue
                if config.pullback_enabled and segment_end >= sell_grid:
                    arm_tracker("sell", sell_grid, segment_end, index, bar)
                elif sell_tracker["active"]:
                    sell_tracker["extreme"] = max(sell_tracker["extreme"], segment_end)
            else:
                if sell_tracker["active"]:
                    target, floor_used = turn_target(
                        "sell", sell_tracker["extreme"], sell_tracker["grid"]
                    )
                    if segment_end <= target < cursor:
                        reason = "保底价触发卖出" if floor_used else "累计回落触发卖出"
                        candidates.append((target, "sell", sell_tracker["grid"], sell_tracker["extreme"], reason))
                if not config.rebound_enabled and segment_end <= buy_grid < cursor:
                    trigger = segment_end if config.multiplier_enabled else buy_grid
                    reason = "跳格到价触发买入" if config.multiplier_enabled else "到达买入网格"
                    candidates.append((trigger, "buy", buy_grid, segment_end, reason))
                if candidates:
                    target, side, grid_price, extreme, reason = max(candidates, key=lambda item: item[0])
                    before = len(trades)
                    submitted, blocked = submit_price_order(
                        side, grid_price, target, extreme, reason, index, bar
                    )
                    if not submitted:
                        add_event(index, bar, "reject", side, target, blocked)
                        break
                    minute_trade_ids.extend(range(before, len(trades)))
                    cursor = target
                    if config.base_update_timing == "filled" and pending_orders:
                        break
                    continue
                if config.rebound_enabled and segment_end <= buy_grid:
                    arm_tracker("buy", buy_grid, segment_end, index, bar)
                elif buy_tracker["active"]:
                    buy_tracker["extreme"] = min(buy_tracker["extreme"], segment_end)
            break
        return minute_trade_ids, blocked

    for index, bar in enumerate(bars):
        open_price, high, low, close = _bar_prices(bar, index)
        minute_trade_ids: List[int] = []
        blocked = ""
        waiting_for_fill = False
        if config.mode == PRICE_TRIGGERED:
            resolved_ids, blocked, waiting_for_fill = resolve_pending(index, bar, high, low)
            minute_trade_ids.extend(resolved_ids)

        if config.monitor_price_mode == "close" and config.mode == PRICE_TRIGGERED:
            path = [previous_close, close]
            if index == 0 and anchor != previous_close:
                path.insert(0, anchor)
        else:
            path = _intrabar_path(open_price, high, low, close)
            if index == 0 and anchor != open_price:
                path.insert(0, anchor)
            elif index and previous_close != open_price:
                path.insert(0, previous_close)

        if not waiting_for_fill:
            for segment_start, segment_end in zip(path, path[1:]):
                if config.mode == TRANSACTION_DRIVEN:
                    cursor = segment_start
                    guard = 0
                    while cursor != segment_end and guard < config.max_trades + 2:
                        guard += 1
                        state = order_state()
                        if segment_end > cursor:
                            side, trigger = "sell", state["sell_price"]
                            crossed = cursor < trigger <= segment_end
                        else:
                            side, trigger = "buy", state["buy_price"]
                            crossed = segment_end <= trigger < cursor
                        if not crossed:
                            break
                        before = len(trades)
                        filled, blocked = transaction_execute(
                            side, trigger, segment_end, index, bar
                        )
                        if not filled:
                            break
                        minute_trade_ids.extend(range(before, len(trades)))
                        cursor = trigger
                else:
                    ids, blocked = process_price_segment(segment_start, segment_end, index, bar)
                    minute_trade_ids.extend(ids)
                if blocked:
                    break

        equity = cash + shares * close
        hold_equity = config.initial_cash + config.initial_shares * close
        peak_equity = max(peak_equity, equity)
        drawdown = equity / peak_equity - 1 if peak_equity else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        min_shares = min(min_shares, shares)
        max_shares = max(max_shares, shares)
        state = order_state()
        buy_target = None
        sell_target = None
        if buy_tracker["active"]:
            buy_target, _ = turn_target("buy", buy_tracker["extreme"], buy_tracker["grid"])
        if sell_tracker["active"]:
            sell_target, _ = turn_target("sell", sell_tracker["extreme"], sell_tracker["grid"])
        timeline.append({
            "index": index,
            "time": str(bar.get("time", index)),
            "price": _round(close),
            "cash": _round(cash, 2),
            "available_cash": _round(cash - state["reserved_cash"], 2),
            "reserved_cash": _round(state["reserved_cash"], 2),
            "shares": shares,
            "available_shares": shares - state["reserved_shares"],
            "reserved_shares": state["reserved_shares"],
            "average_cost": _round(average_cost),
            "equity": _round(equity, 2),
            "hold_equity": _round(hold_equity, 2),
            "realized_pnl": _round(realized_pnl, 2),
            "anchor": _round(anchor),
            "next_buy": _round(state["buy_price"]),
            "next_sell": _round(state["sell_price"]),
            "next_buy_shares": state["buy_shares"],
            "next_sell_shares": state["sell_shares"],
            "buy_order_active": state["buy_active"],
            "sell_order_active": state["sell_active"],
            "buy_tracking": buy_tracker["active"],
            "buy_extreme": _round(buy_tracker["extreme"]) if buy_tracker["active"] else None,
            "buy_turn_target": _round(buy_target) if buy_target is not None else None,
            "sell_tracking": sell_tracker["active"],
            "sell_extreme": _round(sell_tracker["extreme"]) if sell_tracker["active"] else None,
            "sell_turn_target": _round(sell_target) if sell_target is not None else None,
            "pending_count": len(pending_orders),
            "pending_orders": [
                {
                    "side": order["side"],
                    "price": _round(order["order_price"]),
                    "shares": order["shares"],
                    "submitted_time": order["submitted_time"],
                }
                for order in pending_orders
            ],
            "trade_ids": minute_trade_ids,
            "blocked": blocked,
        })
        previous_close = close

    final_price = float(bars[-1]["close"])
    final_equity = cash + shares * final_price
    hold_equity = config.initial_cash + config.initial_shares * final_price
    unrealized_pnl = shares * (final_price - average_cost) if shares else 0.0
    mode_name = "成交驱动型" if config.mode == TRANSACTION_DRIVEN else "到价触发型"
    assumptions = [
        "假设标的允许日内T+0买卖，模拟器不自动核验交易所规则",
        "分钟内按阳线 O→L→H→C、阴线 O→H→L→C 推定触发先后",
        "分钟K线不含盘口、排队和部分成交，所有成交按整笔处理",
        "收盘不强制平仓，剩余持仓按最后一分钟价格计入净值",
    ]
    if config.mode == TRANSACTION_DRIVEN:
        assumptions.insert(1, "成交驱动型模拟双侧预埋限价单，占用资金和证券；一侧全成后撤另一侧并重挂")
    else:
        assumptions.insert(1, "到价触发型在触价前不占用资券；触发后的排队限价才冻结对应资金或证券")
        if config.monitor_price_mode == "close":
            assumptions.append("监控行情选择分钟收盘价，分钟内短暂触价将被忽略")
        else:
            assumptions.append("监控行情使用分钟OHLC路径代理实时最新价，不等同于银河柜台逐笔Level-1行情")
        if config.order_price_mode == "passive":
            assumptions.append("排队限价仅从下一分钟起用OHLC判断能否整笔成交，无法还原盘口队列和部分成交")
    if config.cage_to_market:
        assumptions.append("超笼子转市价使用分钟端点和设定笼子比例近似，真实判定需要逐笔盘口")
    if config.after_close_update_base:
        assumptions.append("次日基准价更新仅计算下一交易日基准，本页单日回放不会继续跨日撮合")

    return {
        "config": asdict(config),
        "mode_name": mode_name,
        "summary": {
            "start_price": _round(first_open),
            "final_price": _round(final_price),
            "initial_equity": _round(initial_equity, 2),
            "final_equity": _round(final_equity, 2),
            "total_pnl": _round(final_equity - initial_equity, 2),
            "return_pct": _round(
                (final_equity / initial_equity - 1) * 100 if initial_equity else 0, 3
            ),
            "hold_equity": _round(hold_equity, 2),
            "excess_vs_hold": _round(final_equity - hold_equity, 2),
            "realized_pnl": _round(realized_pnl, 2),
            "unrealized_pnl": _round(unrealized_pnl, 2),
            "total_fee": _round(total_fee, 2),
            "trade_count": len(trades),
            "trigger_count": sum(event["type"] == "trigger" for event in events),
            "cancel_count": sum(event["type"] == "cancel" for event in events),
            "pending_count": len(pending_orders),
            "buy_count": sum(t["side"] == "buy" for t in trades),
            "sell_count": sum(t["side"] == "sell" for t in trades),
            "final_cash": _round(cash, 2),
            "final_shares": shares,
            "min_shares": min_shares,
            "max_shares": max_shares,
            "max_drawdown_pct": _round(max_drawdown * 100, 3),
            "closing_anchor": _round(anchor),
            "next_day_anchor": _round(final_price if config.after_close_update_base else anchor),
        },
        "timeline": timeline,
        "trades": trades,
        "events": events,
        "assumptions": assumptions,
    }
