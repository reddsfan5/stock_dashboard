import json
import unittest

import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.rebalance import RebalanceConfig, RebalanceEngine


def make_bars(prices_by_code):
    rows = []
    for code, prices in prices_by_code.items():
        for offset, price in enumerate(prices):
            date = pd.Timestamp("2026-08-24") + pd.Timedelta(days=offset)
            rows.append({
                "代码": code, "日期": date, "开盘": price,
                "最高": price, "最低": price, "收盘": price,
                "成交额": 1_000_000,
            })
    return pd.DataFrame(rows)


class RebalanceEngineTest(unittest.TestCase):
    def test_selector_uses_previous_trading_day(self):
        seen = []
        bars = make_bars({"sh510001": [10, 10, 10]})

        def selector(ctx):
            seen.append((ctx.signal_date, ctx.date))
            return ["sh510001"]

        result = RebalanceEngine(RebalanceConfig(
            start_date="2026-08-25", interval_days=10,
        )).run(bars, selector, progress=False)
        self.assertEqual(seen[0][0], pd.Timestamp("2026-08-24"))
        self.assertEqual(seen[0][1], pd.Timestamp("2026-08-25"))
        self.assertEqual(result.assumptions["signal_lag_trading_days"], 1)

    def test_daily_mark_to_market_captures_between_rebalance_drawdown(self):
        bars = make_bars({"sh510001": [10, 10, 8, 8]})
        result = RebalanceEngine(RebalanceConfig(
            capital=10_000, start_date="2026-08-25", interval_days=10,
            execution=ExecutionConfig(commission_rate=0, stamp_tax_rate=0),
        )).run(bars, lambda _: ["sh510001"], progress=False)
        self.assertEqual(len(result.equity_curve), 3)
        self.assertAlmostEqual(result.metrics.max_drawdown_pct, 20.0)
        self.assertAlmostEqual(result.final_equity, 8000.0)

    def test_amount_capacity_produces_partial_fill_and_audit_event(self):
        bars = make_bars({"sh510001": [10, 10, 10]})
        config = RebalanceConfig(
            capital=100_000, start_date="2026-08-25", interval_days=10,
            execution=ExecutionConfig(
                commission_rate=0, stamp_tax_rate=0,
                max_amount_participation=0.01,
            ),
        )
        result = RebalanceEngine(config).run(
            bars, lambda _: ["sh510001"], progress=False,
        )
        buy_events = [e for e in result.events if e["side"] == "buy"]
        self.assertEqual(buy_events[0]["status"], "partial")
        self.assertEqual(buy_events[0]["filled_shares"], 1000)

    def test_missing_bar_rejects_sell_and_preserves_open_position(self):
        bars = make_bars({
            "sh510001": [10, 10],
            "sh510002": [10, 10, 10],
        })
        config = RebalanceConfig(
            capital=10_000, start_date="2026-08-25", interval_days=10,
            execution=ExecutionConfig(commission_rate=0, stamp_tax_rate=0),
        )
        result = RebalanceEngine(config).run(
            bars, lambda _: ["sh510001"], progress=False,
        )
        self.assertEqual(len(result.open_positions), 1)
        self.assertTrue(any(
            event["reason"] == "suspended" and event["side"] == "sell"
            for event in result.events
        ))
        json.dumps(result.to_dict(), allow_nan=False)

    def test_last_day_does_not_open_and_immediately_close(self):
        bars = make_bars({"sh510001": [10, 10]})
        result = RebalanceEngine(RebalanceConfig(
            start_date="2026-08-25", interval_days=1,
        )).run(bars, lambda _: ["sh510001"], progress=False)
        self.assertEqual(result.events, [])
        self.assertEqual(result.trades, [])


if __name__ == "__main__":
    unittest.main()
