import unittest
from contextlib import ExitStack
from unittest.mock import patch

import pandas as pd

from backtest.metrics import calculate_metrics
from backtest.sim_core import buy_total_cost, compute_lots, record_equity
from backtest.sim_engine import SimEngine
from backtest.sim_types import EquityPoint, Position
from backtest.strategy import DayContext, SimStrategy


class FakeData:
    def __init__(self, rows):
        self._cache = pd.DataFrame(rows)
        self._cache["日期"] = pd.to_datetime(self._cache["日期"])

    @property
    def cache(self):
        return self._cache


class OneTradeStrategy(SimStrategy):
    def __init__(self, signal_date="2026-08-25"):
        self.signal_date = pd.Timestamp(signal_date)

    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        return pd.DataFrame([{
            "代码": "sh600000", "名称": "测试股",
            "触发日": self.signal_date,
        }])

    def process_day(self, ctx: DayContext):
        code = ctx.today_signals[0]["代码"]
        _, low, close, _ = ctx.kline_idx[(code, ctx.date)]
        shares = 100
        cost = buy_total_cost(
            close, shares, ctx.commission_rate, ctx.min_commission,
        )
        ctx.cash -= cost
        return [Position(
            code=code, shares=shares, buy_price=close,
            total_cost=cost, target_price=11.0, stop_price=9.0,
            buy_date=ctx.date, buy_day_low=low, max_hold_days=0,
        )]


class NoSignalStrategy(OneTradeStrategy):
    def scan_signals(self, data, code_to_name, start_date, **kwargs):
        return pd.DataFrame(columns=["代码", "名称", "触发日"])


class SeedCaptureStrategy(OneTradeStrategy):
    def __init__(self):
        super().__init__()
        self.draw = None

    def process_day(self, ctx: DayContext):
        self.draw = ctx.rng.random()
        return []


class SimEngineTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"代码": "sh600000", "日期": "2026-08-25", "开盘": 10,
             "最高": 10, "最低": 10, "收盘": 10},
            {"代码": "sh600000", "日期": "2026-08-26", "开盘": 10,
             "最高": 12, "最低": 8, "收盘": 10},
        ]

    def run_engine(self, policy="stop_first"):
        data = FakeData(self.rows)
        info = pd.DataFrame([{"代码": "sh600000", "名称": "测试股"}])
        with ExitStack() as stack:
            stock_info = stack.enter_context(patch("backtest.sim_engine.StockInfo"))
            stock_info.return_value.df = info
            stack.enter_context(patch(
                "backtest.sim_engine.tqdm", side_effect=lambda it, **_: it,
            ))
            stack.enter_context(patch(
                "backtest.sim_core.tqdm", side_effect=lambda it, **_: it,
            ))
            result = SimEngine(OneTradeStrategy()).run(
                capital=10_000, start_date="2026-08-25",
                commission_rate=0, stamp_tax=0,
                intrabar_exit_policy=policy, return_result=True,
                data=data,
            )
        return result

    def test_equity_is_marked_to_current_close(self):
        position = Position(
            code="sh600000", shares=100, buy_price=10, total_cost=1000,
            target_price=12, buy_date="2026-08-25", buy_day_low=10,
        )
        point = record_equity(
            pd.Timestamp("2026-08-26"), 500, [position],
            kline_idx={("sh600000", pd.Timestamp("2026-08-26")):
                       (12, 8, 11, 10)},
        )
        self.assertEqual(point.position_value, 1100)
        self.assertEqual(point.equity, 1600)

    def test_same_bar_conflict_is_explicit_and_configurable(self):
        conservative = self.run_engine("stop_first")
        optimistic = self.run_engine("target_first")

        self.assertEqual(conservative.trades[0].exit_reason, "stop")
        self.assertEqual(conservative.trades[0].sell_price, 9.0)
        self.assertEqual(optimistic.trades[0].exit_reason, "target")
        self.assertEqual(optimistic.trades[0].sell_price, 11.0)

    def test_no_signal_does_not_mutate_shared_cache(self):
        data = FakeData(self.rows + [{
            "代码": "sz300001", "日期": "2026-08-25", "开盘": 20,
            "最高": 21, "最低": 19, "收盘": 20,
        }])
        before = data.cache.copy(deep=True)
        info = pd.DataFrame([{"代码": "sh600000", "名称": "测试股"}])
        with patch("backtest.sim_engine.StockInfo") as stock_info:
            stock_info.return_value.df = info
            result = SimEngine(NoSignalStrategy()).run(
                data=data, return_result=True,
            )
        pd.testing.assert_frame_equal(data.cache, before)
        self.assertEqual(result.final_equity, result.initial_capital)
        self.assertEqual(result.trades, [])

    def test_minimum_commission_is_included_in_position_sizing(self):
        self.assertIsNone(compute_lots(1000, 10, 1.0, 0.0001, 5))
        self.assertEqual(compute_lots(1005, 10, 1.0, 0.0001, 5), 1)
        self.assertEqual(buy_total_cost(10, 100, 0.0001, 5), 1005)

    def test_metrics_use_mark_to_market_curve(self):
        curve = [
            EquityPoint("2026-08-25", 110, 110, 0),
            EquityPoint("2026-08-26", 88, 88, 0),
            EquityPoint("2026-08-27", 100, 100, 0),
        ]
        metrics = calculate_metrics(curve, [], 100, 100)
        self.assertAlmostEqual(metrics.max_drawdown_pct, 20.0)
        self.assertAlmostEqual(metrics.total_return_pct, 0.0)

    def test_result_contains_reproducibility_assumptions(self):
        result = self.run_engine()
        self.assertEqual(result.random_seed, 42)
        self.assertEqual(result.assumptions["mark_to_market"], "daily_close")
        self.assertEqual(result.assumptions["intrabar_exit_policy"], "stop_first")
        self.assertIn("metrics", result.to_dict())

    def test_same_seed_replays_strategy_randomness(self):
        draws = []
        info = pd.DataFrame([{"代码": "sh600000", "名称": "测试股"}])
        for _ in range(2):
            strategy = SeedCaptureStrategy()
            with patch("backtest.sim_engine.StockInfo") as stock_info:
                stock_info.return_value.df = info
                SimEngine(strategy).run(
                    data=FakeData(self.rows), random_seed=20260825,
                    return_result=True,
                )
            draws.append(strategy.draw)
        self.assertEqual(draws[0], draws[1])


if __name__ == "__main__":
    unittest.main()
