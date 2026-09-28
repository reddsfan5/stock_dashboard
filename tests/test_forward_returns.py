"""T+1 前向窗口口径：峰值不含买入日、1 日窗口无峰值、开盘涨停无法买入。"""

import unittest

import pandas as pd

from data.forward_returns import calculate_forward_window, entry_block_reason, price_limit_pct


def _frame(code, rows):
    return pd.DataFrame([
        {"代码": code, "日期": pd.Timestamp(day), "开盘": o, "最高": h, "最低": l, "收盘": c}
        for day, o, h, l, c in rows
    ])


BENCH = _frame("sh000300", [
    ("2026-09-07", 1000, 1100, 990, 1005),   # D1 最高价异常高，不应进入基准峰值
    ("2026-09-08", 1005, 1020, 1000, 1010),
    ("2026-09-09", 1010, 1030, 1004, 1025),
])


class ForwardWindowTest(unittest.TestCase):
    def test_peak_excludes_buy_day_high(self):
        future = _frame("sh600000", [
            ("2026-09-07", 10.0, 12.0, 9.0, 10.5),   # D1 最高 12 不可卖
            ("2026-09-08", 10.5, 10.8, 10.1, 10.6),
            ("2026-09-09", 10.6, 11.0, 10.2, 10.4),
        ])
        result = calculate_forward_window(future, BENCH, 3, prev_close=9.8, code="sh600000")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["entry_date"], "2026-09-07")
        self.assertAlmostEqual(result["peak_high"], 11.0)
        self.assertEqual(result["peak_high_date"], "2026-09-09")
        self.assertAlmostEqual(result["peak_return_pct"], 10.0)
        # 最大不利包含买入当天最低价
        self.assertAlmostEqual(result["low_value"], 9.0)
        self.assertEqual(result["low_date"], "2026-09-07")
        self.assertAlmostEqual(result["adverse_return_pct"], -10.0)
        self.assertAlmostEqual(result["close_return_pct"], 4.0)

    def test_benchmark_peak_excludes_buy_day(self):
        future = _frame("sh600000", [
            ("2026-09-07", 10.0, 10.2, 9.9, 10.1),
            ("2026-09-08", 10.1, 10.5, 10.0, 10.4),
            ("2026-09-09", 10.4, 10.6, 10.3, 10.5),
        ])
        result = calculate_forward_window(future, BENCH, 3)
        self.assertAlmostEqual(result["benchmark_entry_open"], 1000)
        self.assertAlmostEqual(result["benchmark_peak_high"], 1030)
        self.assertAlmostEqual(result["benchmark_peak_return_pct"], 3.0)
        self.assertAlmostEqual(result["peak_excess_pct"], 6.0 - 3.0)

    def test_one_day_window_has_no_peak(self):
        future = _frame("sh600000", [("2026-09-07", 10.0, 11.0, 9.5, 10.2)])
        result = calculate_forward_window(future, BENCH, 1)
        self.assertEqual(result["status"], "complete")
        self.assertIsNone(result["peak_high"])
        self.assertIsNone(result["peak_return_pct"])
        self.assertIsNone(result["peak_excess_pct"])
        self.assertIsNone(result["benchmark_peak_return_pct"])
        self.assertAlmostEqual(result["close_return_pct"], 2.0)
        self.assertAlmostEqual(result["adverse_return_pct"], -5.0)

    def test_only_buy_day_available_is_awaiting_sell(self):
        future = _frame("sh600000", [("2026-09-07", 10.0, 11.0, 9.5, 10.2)])
        result = calculate_forward_window(future, BENCH, 5)
        self.assertEqual(result["status"], "awaiting_sell")
        self.assertIsNone(result["peak_return_pct"])
        self.assertAlmostEqual(result["close_return_pct"], 2.0)

    def test_one_price_limit_up_cannot_buy(self):
        future = _frame("sz300001", [
            ("2026-09-07", 12.0, 12.0, 12.0, 12.0),
            ("2026-09-08", 12.5, 13.0, 12.1, 12.8),
        ])
        result = calculate_forward_window(future, BENCH, 3, prev_close=10.0, code="sz300001")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("一字涨停", result["entry_note"])
        self.assertIsNone(result["peak_return_pct"])
        self.assertIsNone(result["close_return_pct"])
        self.assertIsNone(result["adverse_return_pct"])

    def test_open_at_limit_is_blocked_but_near_limit_is_not(self):
        opened = _frame("sh600000", [("2026-09-07", 11.0, 11.0, 10.6, 10.8), ("2026-09-08", 10.8, 11.2, 10.7, 11.0)])
        result = calculate_forward_window(opened, BENCH, 3, prev_close=10.0, code="sh600000")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("开盘涨停", result["entry_note"])
        near = _frame("sh600000", [("2026-09-07", 10.9, 11.0, 10.6, 10.8), ("2026-09-08", 10.8, 11.2, 10.7, 11.0)])
        self.assertEqual(calculate_forward_window(near, BENCH, 3, prev_close=10.0, code="sh600000")["status"], "partial")
        # 创业板 10% 高开不是涨停
        gem = _frame("sz300001", [("2026-09-07", 11.0, 11.0, 11.0, 11.0), ("2026-09-08", 11.0, 11.2, 10.7, 11.0)])
        self.assertEqual(calculate_forward_window(gem, BENCH, 3, prev_close=10.0, code="sz300001")["status"], "partial")

    def test_price_limit_by_board(self):
        self.assertEqual(price_limit_pct("sh600000"), 0.10)
        self.assertEqual(price_limit_pct("sz000001"), 0.10)
        self.assertEqual(price_limit_pct("sz300750"), 0.20)
        self.assertEqual(price_limit_pct("sh688981"), 0.20)
        self.assertEqual(price_limit_pct("bj830799"), 0.30)
        self.assertEqual(price_limit_pct("sz000001", "*ST某某", "2026-07-03"), 0.05)
        self.assertEqual(price_limit_pct("sz000001", "*ST某某", "2026-07-06"), 0.10)
        self.assertEqual(price_limit_pct("sz300001", "ST某某", "2026-01-05"), 0.20)
        row = {"日期": pd.Timestamp("2026-06-01"), "开盘": 10.5, "最高": 10.5, "最低": 10.5, "收盘": 10.5}
        self.assertIsNotNone(entry_block_reason(row, 10.0, "sz000001", "ST某某"))
        self.assertIsNone(entry_block_reason(row, None, "sz000001", "ST某某"))


if __name__ == "__main__":
    unittest.main()
