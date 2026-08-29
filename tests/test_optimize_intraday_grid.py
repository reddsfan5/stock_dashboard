import argparse
import unittest

import pandas as pd

from backtest.intraday_grid import PRICE_TRIGGERED, TRANSACTION_DRIVEN
from scripts.research.optimize_intraday_grid import (
    Candidate,
    account_for_day,
    build_candidates,
    create_parser,
    filter_candidates_for_tick,
    normalize_code,
    run_one_day,
    split_dates,
    validate_minute_frame,
)


def frame(prices):
    times = pd.date_range("2026-08-25 09:31", periods=len(prices), freq="min")
    return pd.DataFrame({
        "时间": times,
        "开盘": prices,
        "最高": prices,
        "最低": prices,
        "收盘": prices,
    })


class OptimizeIntradayGridTest(unittest.TestCase):
    def test_cli_help_can_be_formatted(self):
        self.assertIn("比例单位为%", create_parser().format_help())

    def test_code_normalization(self):
        self.assertEqual(normalize_code("520500"), "sh520500")
        self.assertEqual(normalize_code("sz159915"), "sz159915")
        with self.assertRaises(ValueError):
            normalize_code("abc")

    def test_chronological_split(self):
        train, validation = split_dates(["d1", "d2", "d3", "d4"], 1)
        self.assertEqual(train, ["d1", "d2", "d3"])
        self.assertEqual(validation, ["d4"])

    def test_data_quality_rejects_duplicate_time(self):
        data = frame([1.0, 1.1, 1.2])
        data.loc[1, "时间"] = data.loc[0, "时间"]
        with self.assertRaisesRegex(ValueError, "重复分钟"):
            validate_minute_frame(data, "2026-08-25", min_bars=2)

    def test_candidate_space_is_conditional(self):
        candidates = build_candidates(
            "diff", [0.002], [500], ["counterparty", "passive"], [0, 0.001],
            ["grid", "fill"],
        )
        transaction = [item for item in candidates if item.mode == TRANSACTION_DRIVEN]
        triggered = [item for item in candidates if item.mode == PRICE_TRIGGERED]
        self.assertEqual(len(transaction), 1)
        self.assertEqual(len(triggered), 8)
        self.assertEqual(transaction[0].order_price_mode, "-")

    def test_tick_filter(self):
        candidates = [
            Candidate(TRANSACTION_DRIVEN, "diff", 0.002, 500),
            Candidate(TRANSACTION_DRIVEN, "diff", 0.0025, 500),
            Candidate(PRICE_TRIGGERED, "diff", 0.002, 500, "passive", 0.0015, "fill"),
        ]
        accepted, rejected = filter_candidates_for_tick(candidates, 0.001, 1.4)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 2)

    def test_daily_episode_preserves_requested_capital(self):
        cash, shares, maximum = account_for_day(100_000, 0.5, 1.4)
        self.assertAlmostEqual(cash + shares * 1.4, 100_000)
        self.assertEqual(shares % 100, 0)
        self.assertGreater(maximum, shares)

    def test_one_day_smoke(self):
        args = argparse.Namespace(
            capital=100_000.0,
            inventory_ratio=0.5,
            passive_offset_bps=0.0,
            commission_bps=1.0,
            min_commission=0.0,
            sell_tax_bps=0.0,
            slippage_bps=2.0,
            max_trades=2_000,
        )
        candidate = Candidate(TRANSACTION_DRIVEN, "diff", 0.01, 500)
        result = run_one_day(candidate, "2026-08-25", frame([1.40, 1.39, 1.40]), args)
        self.assertEqual(result["trade_count"], 2)
        self.assertAlmostEqual(result["initial_equity"], 100_000)
        self.assertGreater(result["turnover_pct"], 0)


if __name__ == "__main__":
    unittest.main()
