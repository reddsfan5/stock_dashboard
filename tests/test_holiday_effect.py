import unittest

import numpy as np
import pandas as pd

from backtest import holiday_effect as he


def _days():
    # 2023 年真实结构：09-29~10-06 中秋国庆合并休市；另造一个 2022 年独立中秋
    d = pd.bdate_range("2022-08-01", "2023-11-30")
    closed = pd.to_datetime(["2022-09-12", "2022-10-03", "2022-10-04", "2022-10-05",
                             "2022-10-06", "2022-10-07", "2023-09-29", "2023-10-02",
                             "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06"])
    return d.difference(closed)


class HolidayEventTest(unittest.TestCase):
    def test_merged_closure_counted_once_with_both_tags(self):
        evs = he.identify_events(_days(), start_year=2022, end_year=2023)
        self.assertEqual(len(evs), 3)
        merged = [e for e in evs if e.kind == "合并"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].holidays, "中秋+国庆")
        self.assertEqual(merged[0].t0, pd.Timestamp("2023-09-28"))
        self.assertEqual(merged[0].t1, pd.Timestamp("2023-10-09"))
        mid = [e for e in evs if e.kind == "中秋(单独)"][0]
        self.assertEqual((mid.t0, mid.t1), (pd.Timestamp("2022-09-09"), pd.Timestamp("2022-09-13")))

    def test_missing_closure_raises_instead_of_silently_skipping(self):
        # 日历里没有节日休市（只有周末）→ 必须报错，避免漏事件
        with self.assertRaisesRegex(ValueError, "普通周末|是交易日"):
            he.identify_events(pd.bdate_range("2022-08-01", "2022-10-31"), 2022, 2022)

    def test_window_metrics_match_hand_calculation(self):
        n = 60
        close = 100 * np.cumprod(np.full(n, 1.01))
        df = pd.DataFrame({"日期": pd.bdate_range("2024-01-01", periods=n), "开盘": close * 0.99,
                           "最高": close * 1.02, "最低": close * 0.98, "收盘": close,
                           "成交量(手)": [100.0] * 30 + [50.0] * 30})
        m = he.daily_metrics(df)
        i = 30
        self.assertAlmostEqual(m.at[i, "节前5日%"], (1.01 ** 5 - 1) * 100, places=6)
        self.assertAlmostEqual(m.at[i, "节后20日%"], (1.01 ** 20 - 1) * 100, places=6)
        self.assertAlmostEqual(m.at[i, "T1跳空%"], (close[i + 1] * 0.99 / close[i] - 1) * 100, places=6)
        self.assertAlmostEqual(m.at[i, "窗口最大回撤%"], 0.0, places=9)
        # T-5..T0 = 第25~30根：25~29 为100、30 为50 → 均值 550/6；分母 T-25..T-6 全为 100
        self.assertAlmostEqual(m.at[i, "节前量比"], (5 * 100 + 50) / 6 / 100, places=9)

    def test_summary_uses_volume_shrink_as_hit_rate(self):
        ev = pd.DataFrame({"类型": ["国庆(单独)", "合并"], "年份": [2020, 2021], "节前量比": [0.8, 1.2]})
        base = pd.DataFrame({"节前量比": np.linspace(0.5, 1.5, 100)})
        s = he.summarize(ev, base, ["节前量比"], n_boot=0)
        row = s[(s["分组"] == "全部休市")].iloc[0]
        self.assertEqual(row["样本数"], 2)
        self.assertAlmostEqual(row["胜率%"], 50.0)


if __name__ == "__main__":
    unittest.main()
