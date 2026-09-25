import json
import re
import unittest

import numpy as np
import pandas as pd

from backtest import holiday_effect as he
from backtest.holiday_report import build_holiday_page


def _days():
    # 2022 独立中秋（09-12 周一休市）+ 国庆；2023 中秋国庆合并 09-29~10-06
    d = pd.bdate_range("2022-08-01", "2023-11-30")
    closed = pd.to_datetime(["2022-09-12", "2022-10-03", "2022-10-04", "2022-10-05",
                             "2022-10-06", "2022-10-07", "2023-09-29", "2023-10-02",
                             "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06"])
    return d.difference(closed)


REG = [
    he.HolidaySpec("mid_autumn", "中秋", {2022: "2022-09-10", 2023: "2023-09-29"}),
    he.HolidaySpec("national", "国庆", {2022: "2022-10-01", 2023: "2023-10-01"}),
]


class HolidayEventTest(unittest.TestCase):
    def test_merged_closure_counted_once_with_both_tags(self):
        evs = he.identify_events(_days(), 2022, 2023, registry=REG)
        self.assertEqual(len(evs), 3)
        merged = [e for e in evs if e.kind == he.MERGED]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].holidays, "中秋+国庆")
        self.assertEqual((merged[0].t0, merged[0].t1), (pd.Timestamp("2023-09-28"), pd.Timestamp("2023-10-09")))
        mid = [e for e in evs if e.kind == "中秋"][0]
        self.assertEqual((mid.t0, mid.t1), (pd.Timestamp("2022-09-09"), pd.Timestamp("2022-09-13")))

    def test_weekend_only_anchor_is_noted_not_counted(self):
        # 2015 中秋 09-27 周日，没有额外休市 → 不形成事件，写入说明
        days = pd.bdate_range("2015-09-01", "2015-10-31").difference(pd.bdate_range("2015-10-01", "2015-10-07"))
        reg = [he.HolidaySpec("mid_autumn", "中秋", {2015: "2015-09-27"}),
               he.HolidaySpec("national", "国庆", {2015: "2015-10-01"})]
        evs, notes = he.identify(days, 2015, 2015, reg)
        self.assertEqual([e.holidays for e in evs], ["国庆"])
        self.assertTrue(any("只落在周末" in n for n in notes))

    def test_new_holiday_is_a_single_registry_entry(self):
        # 清明 2023-04-05（周三）单日休市：追加一项即可识别，并自动成为分组
        days = pd.bdate_range("2023-03-01", "2023-05-31").difference(pd.to_datetime(["2023-04-05"]))
        reg = [he.HolidaySpec("qingming", "清明", {2023: "2023-04-05"})]
        evs = he.identify_events(days, 2023, 2023, registry=reg)
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].t0, pd.Timestamp("2023-04-04"))
        ev = pd.DataFrame([e.as_dict() for e in evs])
        self.assertIn("清明", he.group_names(ev, reg))

    def test_anchor_on_trading_day_raises(self):
        with self.assertRaisesRegex(ValueError, "是交易日"):
            he.identify_events(pd.bdate_range("2023-03-01", "2023-05-31"), 2023, 2023,
                               registry=[he.HolidaySpec("x", "测试", {2023: "2023-04-05"})])

    def test_default_registry_has_five_holidays(self):
        self.assertEqual([s.name for s in he.HOLIDAY_REGISTRY], ["元旦", "春节", "五一", "中秋", "国庆"])
        for spec in he.HOLIDAY_REGISTRY:
            self.assertTrue(all(y in spec.anchors for y in range(2015, 2027)), spec.name)

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
        self.assertAlmostEqual(m.at[i, "节前量比"], (5 * 100 + 50) / 6 / 100, places=9)

    def test_groups_and_exclude_years(self):
        ev = pd.DataFrame({"类型": ["国庆", he.MERGED, "春节"], "节日": ["国庆", "中秋+国庆", "春节"],
                           "年份": [2020, 2024, 2021], "休市自然日": [7, 8, 3], "节前量比": [0.8, 1.2, 0.9]})
        self.assertEqual(int(he.group_mask(ev, "国庆").sum()), 2)            # 国庆含合并
        self.assertEqual(int(he.group_mask(ev, "国庆", [2024]).sum()), 1)
        self.assertEqual(int(he.group_mask(ev, he.LONG_GROUP).sum()), 2)
        base = pd.DataFrame({"节前量比": np.linspace(0.5, 1.5, 100)})
        s = he.summarize(ev, base, ["节前量比"], [he.ALL_GROUP], [2024], n_boot=0)
        row_all = s[(s["口径"] == "all")].iloc[0]
        row_ex = s[(s["口径"] == "ex")].iloc[0]
        self.assertEqual((row_all["样本数"], row_ex["样本数"]), (3, 2))
        self.assertAlmostEqual(row_all["胜率%"], 200 / 3)                  # 量比胜率 = 缩量占比

    def test_page_embeds_json_and_escapes_script_end(self):
        html = build_holiday_page({"meta": {"note": "</script>"}, "groups": ["全部"]})
        raw = re.search(r'<script id="holiday-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(raw.replace("<\\/", "</"))["meta"]["note"], "</script>")
        self.assertIn("/assets/chart-touch.js", html)
        self.assertIn("不构成投资建议", html)


if __name__ == "__main__":
    unittest.main()
