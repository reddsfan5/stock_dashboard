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

    def test_kline_and_bands_are_embedded(self):
        # 日 K 紧凑数组 + 每次休市一条 T0..T1 色带（含合并休市、休市天数），并随页面内嵌
        from scripts.research.holiday_effect import bands_payload, kline_payload
        days = _days()
        hist = days[days <= "2023-09-27"]
        n = len(hist)
        main = pd.DataFrame({"日期": hist, "开盘": np.arange(n) + 100.0, "最高": np.arange(n) + 102.0,
                             "最低": np.arange(n) + 99.0, "收盘": np.arange(n) + 101.0,
                             "成交量(手)": np.full(n, 1234567.0)})
        ev = pd.DataFrame([
            {"年份": 2022, "节日": "国庆", "类型": "国庆", "T0": "2022-09-30", "T1": "2022-10-10",
             "状态": "完整", "休市自然日": 9},
            {"年份": 2023, "节日": "中秋+国庆", "类型": he.MERGED, "T0": "2023-09-28", "T1": "2023-10-09",
             "状态": "未到", "休市自然日": 10},
        ])
        k = kline_payload(main, days, ev, 2023)
        self.assertEqual(k["last"], "2023-09-27")
        self.assertEqual(k["d"][0], "2022-10-10")                      # 起点前一年 10-01 之后首个交易日
        self.assertEqual(len({len(k[c]) for c in "dohlcv"}), 1)
        self.assertIn("2023-10-09", k["d"])                            # 未来日期补到 T1 之后
        self.assertIsNone(k["c"][-1])
        self.assertEqual(k["v"][0], 123)                               # 万手
        bands = bands_payload(ev)
        self.assertEqual([b["tags"] for b in bands], [["国庆"], ["中秋", "国庆"]])
        self.assertEqual((bands[1]["t0"], bands[1]["t1"], bands[1]["days"]), ("2023-09-28", "2023-10-09", 10))
        html = build_holiday_page({"meta": {}, "groups": ["全部"], "kline": k, "bands": bands,
                                   "holidays": [{"key": "national", "name": "国庆", "color": "#e11d48"}],
                                   "merged_color": he.MERGED_COLOR})
        raw = re.search(r'<script id="holiday-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        data = json.loads(raw.replace("<\\/", "</"))
        self.assertEqual(data["kline"]["d"], k["d"])
        self.assertEqual(len(data["bands"]), 2)
        self.assertIn('id="hx-kline"', html)
        self.assertIn("candlestick", html)
        self.assertIn("markArea", html)

    def test_ticker_is_computed_from_payload(self):
        # 行情条 / 倒计时全部由 current / upcoming / bands / kline 计算：目标 = 最近一个还没到 T0 的休市，
        # 合并休市取最后一个存在的分组（中秋+国庆 → 国庆）；代理指数近10日相对强弱自动进入行情条
        from backtest.holiday_report import ticker_payload
        cur = [
            {"项目": "沪深300", "指标": "节前10日%", "当前值": -2.4, "样本数": 11, "历史节前中位数": -1.03, "历史分位%": 27.27, "基准分位%": 20.6},
            {"项目": "沪深300", "指标": "节前量比", "当前值": 0.94, "样本数": 11, "历史节前中位数": 0.83, "历史分位%": 81.8, "基准分位%": 46.0},
            {"项目": "中证1000−沪深300", "指标": "节前5日%", "当前值": 0.75, "样本数": 11, "历史节前中位数": -0.4, "历史分位%": 81.8, "基准分位%": 60.0},
            {"项目": "中证1000−沪深300", "指标": "节前10日%", "当前值": 2.17, "样本数": 11, "历史节前中位数": -1.37, "历史分位%": 90.9, "基准分位%": 75.0},
        ]
        payload = {
            "meta": {"index_name": "沪深300", "upcoming": [
                {"节日": "2026中秋", "T0": "2026-09-24", "T1": "2026-09-28", "状态": "节前已知，未复牌", "距T0交易日": 0},
                {"节日": "2026中秋+国庆", "T0": "2026-09-30", "T1": "2026-10-08", "状态": "未到", "距T0交易日": 3}]},
            "groups": ["全部", "中秋", "国庆", "合并"],
            "holidays": [{"name": "中秋", "color": "#8b5cf6"}, {"name": "国庆", "color": "#e11d48"}],
            "merged_color": "#14b8a6",
            "bands": [{"t0": "2026-09-24", "kind": "中秋", "tags": ["中秋"], "days": 3},
                      {"t0": "2026-09-30", "kind": he.MERGED, "tags": ["中秋", "国庆"], "days": 8}],
            "kline": {"d": ["2026-09-23", "2026-09-24", "2026-09-28"], "o": [4548.0, 4499.86, None],
                      "h": [4548.0, 4500.2, None], "l": [4513.0, 4439.14, None], "c": [4517.0, 4439.14, None]},
            "current": {"国庆": {"all": cur, "ex": cur[:1]}},
        }
        t = ticker_payload(payload)
        self.assertEqual(t["group"], "国庆")
        self.assertEqual((t["countdown"]["name"], t["countdown"]["gap"], t["countdown"]["days"]), ("2026中秋+国庆", 3, 8))
        self.assertEqual(t["countdown"]["color"], "#14b8a6")
        self.assertEqual(len(t["schedule"]), 2)
        self.assertEqual([i["label"] for i in t["items"]["all"]], ["近10日", "量比", "中证1000−300"])
        self.assertEqual(t["items"]["all"][2]["unit"], "pp")
        self.assertTrue(t["items"]["all"][1]["vol"])
        self.assertEqual(len(t["items"]["ex"]), 1)
        self.assertEqual(t["quote"]["date"], "2026-09-24")
        self.assertAlmostEqual(t["quote"]["chg"], (4439.14 / 4517 - 1) * 100, places=3)
        html = build_holiday_page(payload)
        raw = re.search(r'<script id="holiday-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(raw.replace("<\\/", "</"))["ticker"]["group"], "国庆")
        self.assertIn('id="hx-ticker"', html)
        self.assertIn("holidayTheme", html)                                   # 本页深色优先

    def test_ticker_tolerates_minimal_payload(self):
        from backtest.holiday_report import ticker_payload
        t = ticker_payload({"meta": {}, "groups": ["全部"]})
        self.assertEqual((t["group"], t["countdown"], t["quote"], t["items"]), ("全部", None, None, {}))

    def test_kline_is_inspectable_per_candle(self):
        # 日 K 逐根查看：信息条 + 点击选中 + 方向键；手机双指缩放 / 横向平移走共享 chart-touch.js（显式开启）
        html = build_holiday_page({"meta": {}, "groups": ["全部"]})
        for needle in ('id="hx-ktip"', "selectK(", "ArrowLeft", 'tabindex="0"', "pinchZoom:true", "panX:true",
                       "minSpan:20", 'data-z="120"'):
            self.assertIn(needle, html)

    def test_chart_touch_never_puts_cross_on_top_level_axis_pointer(self):
        # 顶层 axisPointer.type='cross' 会让 ECharts 悬停抛错（整张图悬停、滚轮缩放失效）
        from pathlib import Path
        js = (Path(__file__).resolve().parents[1] / "scripts/services/static/chart-touch.js").read_text(encoding="utf-8")
        self.assertIn("axisPointerType === 'cross' ? 'line' : axisPointerType", js)
        self.assertIn("pinchZoom", js)
        self.assertIn("panX", js)

    # ---------- 总体总结 digest / 结论可视化 ----------
    @staticmethod
    def _digest_payload():
        def row(g, v, m, mean, med, win, bmean, bwin, p=None, n=11):
            return {"分组": g, "口径": v, "指标": m, "样本数": n, "均值": mean, "中位数": med, "胜率%": win,
                    "基准均值": bmean, "基准中位数": 0.0, "基准胜率%": bwin, "均值差": mean - bmean, "p值": p}
        summ = []
        for g, n in (("全部", 20), ("国庆", 11), ("五一", 9)):
            k = 1.0 if g != "五一" else -1.0
            summ += [row(g, "all", "节前5日%", 1.0, -0.1, 45, 0.1, 53, 0.03, n),   # 均值/中位方向相反
                     row(g, "all", "T1跳空%", 0.3, 0.2, 70, -0.06, 45, 0.001, n),
                     row(g, "all", "节后5日%", 0.8 * k, 0.9 * k, 60, 0.09, 53, 0.4, n),
                     row(g, "all", "节后20日%", 0.6, 1.5, 57, 0.35, 51, 0.7, n),
                     row(g, "all", "节前量比", 0.9, 0.88, 75, 1.0, 56, None, n),
                     row(g, "ex", "节前5日%", 0.2, -0.1, 44, 0.1, 53, 0.9, n),
                     row(g, "ex", "T1跳空%", -0.1, 0.1, 64, -0.06, 45, 0.3, n),
                     row(g, "ex", "节后20日%", -0.4, 1.4, 57, 0.35, 51, 0.8, n)]
        rs = [{"代码": "sh000852", "代理": "中证1000", "分组": "全部", "口径": "all", "指标": m, "样本数": 20,
               "均值": v, "中位数": v, "胜率%": w, "基准均值": 0, "基准中位数": 0, "基准胜率%": 50, "均值差": v, "p值": 0.5}
              for m, v, w in (("节前5日%", -0.55, 43), ("节后5日%", 0.77, 62), ("节后10日%", 0.59, 57))]
        return {"meta": {"index_name": "沪深300", "generated": "2026-09-26 08:00", "sample": "2015-01-01 ~ 2026-09-24",
                         "exclude_years": [2015, 2024], "proxies": [{"code": "sh000852", "name": "中证1000"}],
                         "upcoming": [{"节日": "2026国庆", "T0": "2026-09-30", "T1": "2026-10-08", "状态": "未到", "距T0交易日": 3}]},
                "groups": ["全部", "五一", "国庆"], "holidays": [{"name": "五一"}, {"name": "国庆"}],
                "bands": [{"t0": "2026-09-30", "kind": "国庆", "tags": ["国庆"], "days": 8}],
                "summary": summ, "rs_summary": rs, "current": {}}

    def test_digest_numbers_come_from_summary(self):
        from backtest.holiday_report import digest_payload, rs_verdict
        d = digest_payload(self._digest_payload())
        self.assertEqual(d["id"], "holiday_effect")
        kp = {k["label"]: k for k in d["kpis"]}
        self.assertEqual(kp["完整事件"]["value"], "20")
        self.assertEqual(kp["复牌跳空胜率"]["value"], "70%")
        self.assertIn("平时 45%", kp["复牌跳空胜率"]["sub"])
        self.assertEqual(kp["节前缩量占比"]["value"], "75%")
        self.assertEqual(kp["中证1000 节后5日"]["value"], "+0.77pp")
        self.assertEqual(kp["距 2026国庆"]["value"], "3 交易日")
        self.assertIn("节前普遍缩量", d["headline"])
        self.assertIn("中证1000 节前去风险、节后再风险", d["headline"])
        self.assertIn("剔除 2015/2024 后仍显著：无", d["headline"])       # 显著性要经得起剔除异常年
        secs = {s["title"]: s["items"] for s in d["sections"]}
        win = secs["收益窗口（全部事件 · 全部年份）"]
        self.assertEqual(win[0]["tag"], "均值/中位背离")
        hol = secs["各节日对比"]
        self.assertIn("最强：国庆", hol[0]["text"])
        self.assertIn("最弱：五一", hol[1]["text"])
        self.assertIn("样本不足 10 次：五一", hol[2]["text"])
        rob = secs["稳健性（剔除异常年）"]
        self.assertTrue(any(i["tag"] == "方向翻转" and "节后20日" in i["text"] for i in rob))
        self.assertEqual(rs_verdict({"均值": -0.5, "胜率%": 40}, {"均值": 0.7, "胜率%": 60}), ("节前去风险", "节后再风险"))
        self.assertEqual(rs_verdict({"均值": 0.5, "胜率%": 40}, {"均值": -0.7, "胜率%": 30}), ("节前无一致方向", "节后继续偏防御"))

    def test_digest_tolerates_minimal_payload(self):
        from backtest.holiday_report import digest_payload
        d = digest_payload({"meta": {}, "groups": ["全部"]})
        self.assertEqual((d["kpis"], d["sections"]), ([], []))

    def test_research_digest_markdown_json_and_index_card(self):
        import tempfile
        from pathlib import Path
        from backtest import research_digest as rd
        from backtest.holiday_report import digest_payload
        d = digest_payload(self._digest_payload())
        with tempfile.TemporaryDirectory() as tmp:
            d["md_path"] = str(Path(tmp) / "summary.md")
            out = rd.save(d, Path(tmp) / "digests")
            self.assertTrue(out.exists())
            md = Path(d["md_path"]).read_text(encoding="utf-8")
            self.assertIn("# 节假日效应 · 总体总结", md)
            self.assertIn("| 复牌跳空胜率 | 70% |", md)
            self.assertIn("## 各节日对比（全部年份）", md)
            (Path(tmp) / "digests" / "broken.json").write_text("{", encoding="utf-8")   # 损坏文件被跳过
            other = {"id": "zz_other", "title": "另一个回测", "page": "/x.html", "order": 10, "kpis": []}
            rd.save(other, Path(tmp) / "digests")
            loaded = rd.load_all(Path(tmp) / "digests")
            self.assertEqual([x["id"] for x in loaded], ["zz_other", "holiday_effect"])   # 按 order 排序
            html = rd.index_section_html(loaded)
            self.assertIn('href="/holiday_effect.html#summary"', html)
            self.assertIn("研究结论速览", html)
        self.assertEqual(rd.index_section_html([]), "")
        with self.assertRaises(ValueError):
            rd.validate({"id": "x"})

    def test_page_has_visual_conclusions_and_summary_card(self):
        html = build_holiday_page(self._digest_payload())
        for needle in ('id="summary"', 'id="hx-sumkpis"', "holidaySummaryOpen", "function renderConcl",
                       "hx-wbar", "hx-rg", "hx-vchip", "均值/中位背离", 'id="hx-hol"', 'id="hx-yoy"', "文字版结论"):
            self.assertIn(needle, html)
        raw = re.search(r'<script id="holiday-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(raw.replace("<\\/", "</"))["digest"]["kpis"][0]["value"], "20")


if __name__ == "__main__":
    unittest.main()
