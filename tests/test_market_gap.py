import unittest

import pandas as pd

from backtest.market_gap import MarketGapConfig, run_market_gap_study


def _daily():
    # 低开日 1 从 9.0 反弹到第 3 日 10.2；高开日 4 从 11.0 回落到第 3 日 10.4。
    return pd.DataFrame({
        "日期": pd.date_range("2024-01-02", periods=8, freq="B"),
        "开盘": [10.0, 9.0, 9.5, 9.8, 11.0, 10.8, 10.6, 10.5],
        "最高": [10.2, 9.6, 10.0, 10.3, 11.3, 11.0, 10.8, 10.7],
        "最低": [9.8, 8.8, 9.3, 9.7, 10.8, 10.5, 10.2, 10.3],
        "收盘": [10.0, 9.4, 9.8, 10.2, 11.1, 10.7, 10.4, 10.6],
        "来源": ["test"] * 8,
    })


class MarketGapStudyTest(unittest.TestCase):
    def test_signal_day_is_first_day_and_third_close_is_exit(self):
        result = run_market_gap_study(
            _daily(), MarketGapConfig(threshold_pct=0.7, horizon=3, start_date="2024-01-01")
        )
        low = next(row for row in result["events"] if row["date"] == "2024-01-03")
        high = next(row for row in result["events"] if row["date"] == "2024-01-08")
        self.assertEqual(low["date"], "2024-01-03")
        self.assertEqual(low["target_date"], "2024-01-05")
        self.assertAlmostEqual(low["return_pct"], (10.2 / 9.0 - 1) * 100, places=4)
        self.assertEqual(high["date"], "2024-01-08")
        self.assertEqual(high["target_date"], "2024-01-10")
        self.assertAlmostEqual(high["return_pct"], (10.4 / 11.0 - 1) * 100, places=4)

    def test_paths_never_extend_beyond_configured_horizon(self):
        result = run_market_gap_study(
            _daily(), MarketGapConfig(threshold_pct=0.7, horizon=2, start_date="2024-01-01")
        )
        self.assertEqual({row["label"] for row in result["paths"]}, {"T0", "T+1"})
        self.assertTrue(all(row["target_date"] <= "2024-01-11" for row in result["events"]))

    def test_invalid_parameters_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "缺口阈值"):
            run_market_gap_study(
                _daily(), MarketGapConfig(threshold_pct=0, horizon=3, start_date="2024-01-01")
            )

    def test_empty_signal_set_still_returns_stable_contract(self):
        quiet = _daily().copy()
        quiet["开盘"] = quiet["收盘"].shift(1).fillna(quiet["开盘"])
        result = run_market_gap_study(
            quiet, MarketGapConfig(threshold_pct=9, horizon=3, start_date="2024-01-01")
        )
        self.assertEqual(result["summary"]["low"]["samples"], 0)
        self.assertEqual(result["events"], [])
        self.assertEqual(len(result["severity"]), 6)


if __name__ == "__main__":
    unittest.main()
