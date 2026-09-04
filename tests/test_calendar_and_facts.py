"""交易日历与涨跌停事实单元测试（无网络）。"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.calendar import HALF_DAY_CLOSE, FULL_DAY_CLOSE, TradingCalendar
from data.stock_facts import infer_limit_flags, limit_pct_for_code


class TradingCalendarTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # 空缓存 → 工作日近似
        self.cal = TradingCalendar(Path(self.temp.name) / "missing.parquet")

    def tearDown(self):
        self.temp.cleanup()

    def test_half_day_and_session_close(self):
        self.assertTrue(self.cal.is_half_day("2026-09-30"))
        self.assertEqual(self.cal.session_close("2026-09-30"), HALF_DAY_CLOSE)
        self.assertFalse(self.cal.is_half_day("2026-09-04"))
        self.assertEqual(self.cal.session_close("2026-09-04"), FULL_DAY_CLOSE)

    def test_weekday_fallback_without_cache(self):
        # 2026-09-04 是周五
        self.assertTrue(self.cal.is_trading_day("2026-09-04"))
        self.assertFalse(self.cal.is_trading_day("2026-09-05"))  # 周六
        self.assertEqual(self.cal.next_trading_day("2026-09-04"), "2026-09-07")
        self.assertEqual(self.cal.prev_trading_day("2026-09-07"), "2026-09-04")
        days = self.cal.range("2026-09-04", "2026-09-08")
        self.assertEqual(days, ["2026-09-04", "2026-09-07", "2026-09-08"])

    def test_cached_dates_preferred(self):
        path = Path(self.temp.name) / "calendar.parquet"
        frame = pd.DataFrame({
            "日期": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-04"]),
            "半日市": [False, False, False],
        })
        frame.to_parquet(path, index=False)
        cal = TradingCalendar(path)
        self.assertTrue(cal.is_trading_day("2026-09-01"))
        self.assertFalse(cal.is_trading_day("2026-09-03"))  # 缓存未收录
        self.assertEqual(cal.next_trading_day("2026-09-01"), "2026-09-02")
        self.assertEqual(cal.prev_trading_day("2026-09-04"), "2026-09-02")


class LimitFactsTest(unittest.TestCase):
    def test_limit_pct_for_code(self):
        self.assertEqual(limit_pct_for_code("sh600519"), 10.0)
        self.assertEqual(limit_pct_for_code("sz000001"), 10.0)
        self.assertEqual(limit_pct_for_code("sz300750"), 20.0)
        self.assertEqual(limit_pct_for_code("sh688981"), 20.0)
        self.assertEqual(limit_pct_for_code("sh600519", is_st=True), 5.0)
        self.assertEqual(limit_pct_for_code("600519"), 10.0)

    def test_infer_limit_up(self):
        prev = 10.0
        flags = infer_limit_flags(
            close=11.0, prev_close=prev, high=11.0, low=10.5, limit_pct=10.0
        )
        self.assertTrue(flags["limit_up"])
        self.assertFalse(flags["limit_down"])
        self.assertFalse(flags["suspended"])

        one_word = infer_limit_flags(
            close=11.0, prev_close=prev, high=11.0, low=11.0, limit_pct=10.0
        )
        self.assertTrue(one_word["one_word_limit_up"])

    def test_infer_suspend_on_zero_volume(self):
        flags = infer_limit_flags(
            close=10.0, prev_close=10.0, high=10.0, low=10.0,
            limit_pct=10.0, amount=0.0, volume=0.0,
        )
        self.assertTrue(flags["suspended"])
        self.assertFalse(flags["limit_up"])


if __name__ == "__main__":
    unittest.main()
