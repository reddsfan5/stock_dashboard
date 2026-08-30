"""资金模拟的统一绩效指标。

指标只依赖结构化权益曲线和成交记录，不依赖报告层。这样命令行、测试、
参数搜索与 HTML 报告使用同一套口径。
"""

from math import sqrt
from typing import Iterable, Sequence

import numpy as np

from backtest.sim_types import EquityPoint, SimulationMetrics, Trade


def _finite_or_none(value: float):
    return float(value) if np.isfinite(value) else None


def calculate_metrics(
    equity_curve: Sequence[EquityPoint],
    trades: Iterable[Trade],
    initial_capital: float,
    final_equity: float,
    periods_per_year: int = 252,
) -> SimulationMetrics:
    """按日频权益计算收益、波动、Sharpe、最大回撤与交易统计。"""
    trades = list(trades)
    initial = float(initial_capital)
    final = float(final_equity)

    if initial <= 0:
        raise ValueError("initial_capital 必须大于 0")

    total_return = (final / initial - 1.0) * 100
    values = [initial] + [float(point.equity) for point in equity_curve]
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values) & (values > 0)]

    max_drawdown = 0.0
    annualized_return = None
    annualized_volatility = None
    sharpe = None
    calmar = None

    if values.size:
        peaks = np.maximum.accumulate(values)
        drawdowns = values / peaks - 1.0
        max_drawdown = abs(float(np.min(drawdowns))) * 100

    if values.size >= 2:
        returns = values[1:] / values[:-1] - 1.0
        returns = returns[np.isfinite(returns)]
        periods = len(returns)
        if periods > 0 and final > 0:
            annualized_return = (final / initial) ** (periods_per_year / periods) - 1.0
            annualized_return *= 100
        if returns.size >= 2:
            std = float(np.std(returns, ddof=1))
            annualized_volatility = std * sqrt(periods_per_year) * 100
            if std > 0:
                sharpe = float(np.mean(returns) / std * sqrt(periods_per_year))

    if annualized_return is not None and max_drawdown > 0:
        calmar = annualized_return / max_drawdown

    wins = [trade for trade in trades if trade.pnl > 0]
    losses = [trade for trade in trades if trade.pnl < 0]
    gross_profit = sum(trade.pnl for trade in wins)
    gross_loss = abs(sum(trade.pnl for trade in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    return SimulationMetrics(
        total_return_pct=round(total_return, 4),
        annualized_return_pct=(round(annualized_return, 4)
                               if annualized_return is not None else None),
        annualized_volatility_pct=(round(annualized_volatility, 4)
                                   if annualized_volatility is not None else None),
        sharpe_ratio=(round(sharpe, 4) if sharpe is not None else None),
        max_drawdown_pct=round(max_drawdown, 4),
        calmar_ratio=(round(calmar, 4) if calmar is not None else None),
        trade_count=len(trades),
        win_rate_pct=round(len(wins) / len(trades) * 100, 4) if trades else 0.0,
        profit_factor=(_finite_or_none(round(profit_factor, 4))
                       if profit_factor is not None else None),
        avg_trade_return_pct=(round(sum(t.return_pct for t in trades) / len(trades), 4)
                              if trades else 0.0),
    )
