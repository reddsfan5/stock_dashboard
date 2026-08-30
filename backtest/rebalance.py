"""周期轮动策略的统一资金与撮合引擎。

选择器只能看到前一交易日 `signal_date`，订单在 `date` 执行，从接口层避免
“用当日收盘指标、又按同日收盘成交”的时间穿越。引擎对每个交易日盯市，而不是
只在调仓日记录净值。
"""

from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

import pandas as pd
from tqdm import tqdm

from backtest.execution import (
    BUY, SELL, ExecutionConfig, execute_order,
)
from backtest.metrics import calculate_metrics
from backtest.sim_types import EquityPoint, Position, SimulationResult, Trade


@dataclass(frozen=True)
class RebalanceConfig:
    capital: float = 50_000
    start_date: str = "2022-01-01"
    interval_days: int = 20
    top_n: int = 5
    execution_price: str = "close"  # close/open
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    liquidate_at_end: bool = True

    def __post_init__(self):
        if self.capital <= 0:
            raise ValueError("capital 必须大于 0")
        if self.interval_days <= 0 or self.top_n <= 0:
            raise ValueError("interval_days 和 top_n 必须大于 0")
        if self.execution_price not in {"close", "open"}:
            raise ValueError("execution_price 仅支持 close/open")


@dataclass(frozen=True)
class RebalanceContext:
    """选择器上下文；signal_date 永远早于实际成交日 date。"""

    date: pd.Timestamp
    signal_date: pd.Timestamp
    rebalance_number: int
    top_n: int


Selector = Callable[[RebalanceContext], Sequence[str]]


class RebalanceEngine:
    """等权周期调仓引擎。选择逻辑通过 selector 注入。"""

    def __init__(self, config: RebalanceConfig):
        self.config = config

    def run(self, bars: pd.DataFrame, selector: Selector,
            code_to_name: Optional[Mapping[str, str]] = None,
            progress: bool = True) -> SimulationResult:
        if "日期" not in bars.columns:
            raise ValueError("行情缺少字段: ['日期']")
        raw_dates = pd.to_datetime(bars["日期"])
        dates = sorted(raw_dates.unique())
        start = pd.Timestamp(self.config.start_date)
        execution_dates = [pd.Timestamp(date) for date in dates if date >= start]
        if not execution_dates:
            raise ValueError("回测区间内无交易日")

        date_position = {pd.Timestamp(date): i for i, date in enumerate(dates)}
        first_pos = date_position[execution_dates[0]]
        if first_pos == 0:
            raise ValueError("起始日前至少需要一个交易日用于生成无前视信号")

        # 执行层只需要起始日前一交易日至样本末尾；策略自己的历史窗口由 selector 管理。
        # 多保留约一个月，为停牌标的计算自己的最近前收，而不只依赖全市场前一交易日。
        required_from = pd.Timestamp(dates[max(0, first_pos - 30)])
        prepared = _prepare_bars(bars[raw_dates >= required_from])
        bar_index = _build_bar_index(prepared)
        rebalance_dates = set(execution_dates[::self.config.interval_days])
        last_date = execution_dates[-1]
        cash = float(self.config.capital)
        positions: Dict[str, Position] = {}
        last_prices: Dict[str, float] = {}
        trades: List[Trade] = []
        equity_curve: List[EquityPoint] = []
        events: List[dict] = []
        names = dict(code_to_name or {})
        rebalance_number = 0

        iterator = tqdm(execution_dates, desc="周期轮动") if progress else execution_dates
        for date in iterator:
            if date in rebalance_dates:
                signal_date = pd.Timestamp(dates[date_position[date] - 1])
                context = RebalanceContext(
                    date=date,
                    signal_date=signal_date,
                    rebalance_number=rebalance_number,
                    top_n=self.config.top_n,
                )
                rebalance_number += 1

                cash = self._sell_all(
                    date, cash, positions, bar_index, last_prices,
                    names, trades, events, "rebalance",
                )

                # 最后一个交易日不再新开仓，避免同日买入后立即期末清仓。
                if date != last_date:
                    selected = _unique(selector(context))[:self.config.top_n]
                    cash = self._buy_equal_weight(
                        date, selected, cash, positions, bar_index,
                        last_prices, events,
                    )

            position_value = 0.0
            for code, position in positions.items():
                bar = bar_index.get((code, date))
                if bar is not None:
                    last_prices[code] = float(bar["close"])
                price = last_prices.get(code, position.buy_price)
                position_value += position.shares * price
            equity_curve.append(EquityPoint(
                date=date.strftime("%Y-%m-%d"),
                equity=cash + position_value,
                cash=cash,
                positions=len(positions),
                position_value=position_value,
            ))

        if self.config.liquidate_at_end and positions:
            cash = self._sell_all(
                last_date, cash, positions, bar_index, last_prices,
                names, trades, events, "end_of_data",
            )
            position_value = sum(
                position.shares * last_prices.get(code, position.buy_price)
                for code, position in positions.items()
            )
            equity_curve[-1] = EquityPoint(
                date=last_date.strftime("%Y-%m-%d"),
                equity=cash + position_value,
                cash=cash,
                positions=len(positions),
                position_value=position_value,
            )

        final_equity = equity_curve[-1].equity
        metrics = calculate_metrics(
            equity_curve, trades, self.config.capital, final_equity,
        )
        assumptions = {
            "engine": "periodic_rebalance",
            "signal_lag_trading_days": 1,
            "mark_to_market": "daily_close",
            "execution_price": self.config.execution_price,
            "interval_days": self.config.interval_days,
            "top_n": self.config.top_n,
            "liquidate_at_end": self.config.liquidate_at_end,
            "execution": asdict(self.config.execution),
        }
        return SimulationResult(
            initial_capital=self.config.capital,
            final_equity=final_equity,
            start_date=execution_dates[0].strftime("%Y-%m-%d"),
            end_date=last_date.strftime("%Y-%m-%d"),
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
            random_seed=0,
            assumptions=assumptions,
            open_positions=list(positions.values()),
            events=events,
        )

    def _sell_all(self, date, cash, positions, bar_index, last_prices,
                  names, trades, events, exit_reason):
        for code, position in list(positions.items()):
            bar = bar_index.get((code, date))
            if bar is None:
                events.append({
                    "date": date.strftime("%Y-%m-%d"), "code": code,
                    "side": SELL, "status": "rejected", "reason": "suspended",
                })
                continue
            reference_price = float(bar[self.config.execution_price])
            fill = execute_order(
                SELL, position.shares, reference_price,
                self.config.execution,
                available_shares=position.shares, bar=bar,
            )
            events.append(_event(date, code, fill))
            if not fill.filled:
                continue

            original_shares = position.shares
            allocated_cost = position.total_cost * fill.filled_shares / original_shares
            cash += fill.cash_delta
            trades.append(Trade(
                code=code,
                name=names.get(code, ""),
                buy_date=str(position.buy_date)[:10],
                buy_price=round(position.buy_price, 4),
                sell_date=date.strftime("%Y-%m-%d"),
                sell_price=round(float(fill.fill_price), 4),
                return_pct=round((fill.cash_delta - allocated_cost) / allocated_cost * 100, 4),
                pnl=round(fill.cash_delta - allocated_cost, 4),
                filled=False,
                lots=fill.filled_shares // self.config.execution.lot_size,
                exit_reason=exit_reason,
            ))
            remaining = original_shares - fill.filled_shares
            if remaining <= 0:
                del positions[code]
            else:
                position.shares = remaining
                position.total_cost -= allocated_cost
            last_prices[code] = float(fill.fill_price)
        return cash

    def _buy_equal_weight(self, date, selected, cash, positions,
                          bar_index, last_prices, events):
        candidates = [code for code in selected if code not in positions]
        if not candidates:
            return cash
        per_budget = cash / len(candidates)
        for code in candidates:
            bar = bar_index.get((code, date))
            if bar is None:
                events.append({
                    "date": date.strftime("%Y-%m-%d"), "code": code,
                    "side": BUY, "status": "rejected", "reason": "suspended",
                })
                continue
            reference_price = float(bar[self.config.execution_price])
            requested = int(per_budget / reference_price)
            requested = (
                requested // self.config.execution.lot_size
                * self.config.execution.lot_size
            )
            fill = execute_order(
                BUY, requested, reference_price, self.config.execution,
                cash=min(cash, per_budget), bar=bar,
            )
            events.append(_event(date, code, fill))
            if not fill.filled:
                continue
            cash += fill.cash_delta
            low = float(bar.get("low", fill.fill_price))
            positions[code] = Position(
                code=code,
                shares=fill.filled_shares,
                buy_price=float(fill.fill_price),
                total_cost=-fill.cash_delta,
                target_price=0.0,
                buy_date=date.strftime("%Y-%m-%d"),
                buy_day_low=low,
                max_hold_days=0,
            )
            last_prices[code] = float(bar["close"])
        return cash


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"代码", "日期", "开盘", "最高", "最低", "收盘"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"行情缺少字段: {sorted(missing)}")
    columns = ["代码", "日期", "开盘", "最高", "最低", "收盘"]
    for optional in ("成交额", "前收"):
        if optional in bars.columns:
            columns.append(optional)
    prepared = bars[columns].copy()
    prepared["日期"] = pd.to_datetime(prepared["日期"])
    prepared = prepared.sort_values(["代码", "日期"])
    prepared = prepared.drop_duplicates(["代码", "日期"], keep="last")
    if "前收" not in prepared.columns:
        prepared["前收"] = prepared.groupby("代码")["收盘"].shift(1)
    return prepared


def _build_bar_index(bars: pd.DataFrame) -> Dict:
    result = {}
    has_amount = "成交额" in bars.columns
    columns = ["代码", "日期", "开盘", "最高", "最低", "收盘", "前收"]
    if has_amount:
        columns.append("成交额")
    for values in bars[columns].itertuples(index=False, name=None):
        code, date, open_, high, low, close, prev_close, *rest = values
        result[(code, pd.Timestamp(date))] = {
            "open": float(open_), "high": float(high), "low": float(low),
            "close": float(close),
            "prev_close": (float(prev_close) if pd.notna(prev_close) else None),
            "amount": (float(rest[0]) if rest and pd.notna(rest[0]) else None),
        }
    return result


def _event(date, code, fill):
    event = asdict(fill)
    event.update({"date": date.strftime("%Y-%m-%d"), "code": code})
    return event


def _unique(codes: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(str(code) for code in codes))
