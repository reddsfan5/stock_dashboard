import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from features.snapshot import (
    build_decision_snapshot,
    enrich_screening_results,
    snapshot_lookup,
)
from pipeline.reporter import build_screening_html, build_screening_mobile


def make_daily():
    dates = pd.bdate_range("2026-05-01", periods=65)
    rows = []
    for code, growth in (("sh600000", 0.20), ("sz000001", 0.05)):
        closes = 10 + np.arange(len(dates)) * growth
        for index, (date, close) in enumerate(zip(dates, closes), start=1):
            rows.append({
                "代码": code,
                "日期": date,
                "开盘": close - 0.05,
                "最高": close + 0.20,
                "最低": close - 0.20,
                "收盘": close,
                "前收": close - growth,
                "成交量": index * 100,
                "成交额": index * 1_000,
                "换手率%": 1.0 + index / 100,
            })
    return pd.DataFrame(rows), dates[-1]


class DecisionSnapshotTest(unittest.TestCase):
    def test_ratios_exclude_current_day_and_basic_fields_join(self):
        daily, latest_date = make_daily()
        basic = pd.DataFrame([{
            "代码": "sh600000",
            "日期": latest_date,
            "供应商量比": 1.8,
            "市盈率_动态": 12.5,
            "流通市值": 25_000_000_000,
        }])
        snapshot = build_decision_snapshot(daily, basic)
        stock = snapshot.set_index("代码").loc["sh600000"]

        # 第65天应除以前20天（45..64）的均值，不能把当前日放进分母。
        self.assertAlmostEqual(stock["成交量比20"], 65 / np.mean(range(45, 65)), places=4)
        self.assertAlmostEqual(stock["成交额比20"], 65 / np.mean(range(45, 65)), places=4)
        self.assertEqual(stock["指标日期"], latest_date.strftime("%Y-%m-%d"))
        self.assertEqual(stock["动态PE"], 12.5)
        self.assertEqual(stock["流通市值(亿)"], 250.0)
        self.assertEqual(stock["供应商量比"], 1.8)

    def test_relative_strength_and_result_enrichment(self):
        daily, _ = make_daily()
        snapshot = build_decision_snapshot(daily)
        relative = snapshot.set_index("代码")["市场相对强弱20%"]
        self.assertGreater(relative["sh600000"], 0)
        self.assertLess(relative["sz000001"], 0)
        self.assertAlmostEqual(relative.sum(), 0, places=3)

        results = {"demo": pd.DataFrame({"代码": ["sz000001"], "名称": ["平安银行"]})}
        enriched = enrich_screening_results(results, snapshot)["demo"]
        self.assertIn("成交量比20", enriched)
        self.assertEqual(enriched.iloc[0]["指标日期"], snapshot.iloc[1]["指标日期"])

    def test_snapshot_lookup_is_strict_json(self):
        daily, _ = make_daily()
        lookup = snapshot_lookup(build_decision_snapshot(daily))
        json.dumps(lookup, ensure_ascii=False, allow_nan=False)

    def test_dashboard_exposes_metrics_and_decision_filters(self):
        daily, _ = make_daily()
        snapshot = build_decision_snapshot(daily)
        module = SimpleNamespace(id="demo", title="示例策略")
        results = enrich_screening_results(
            {"demo": pd.DataFrame({"代码": ["sh600000"], "名称": ["浦发银行"]})},
            snapshot,
        )
        with patch("scripts.reports.gen_index.generate"):
            desktop = build_screening_html(
                [module], results, decision_snapshot=snapshot
            )
        mobile = build_screening_mobile(
            [module], results, decision_snapshot=snapshot
        )
        self.assertIn('id="fVolume"', desktop)
        self.assertIn("成交量比20", desktop)
        self.assertIn("指标口径", desktop)
        self.assertIn("区间涨幅", desktop)
        self.assertIn("klineRangePctToIndex", desktop)
        self.assertIn("'区间'", desktop)
        # 候选数据作为预生成 JSON 缓存嵌入；首屏不再预建全部 td，
        # DataTables 只为当前分页创建 DOM，避免每日更新后首次打开卡顿。
        self.assertIn('id="screen-data-demo"', desktop)
        self.assertIn("deferRender:true", desktop)
        self.assertIn("<tbody></tbody>", desktop)
        self.assertNotIn('<td class="code-sh', desktop)
        self.assertIn("location.protocol==='file:'", desktop)
        self.assertIn("http://127.0.0.1:8765/dashboard.html", desktop)
        self.assertIn("function copyScreeningList()", desktop)
        self.assertIn("rows({search:'applied',order:'applied'})", desktop)
        self.assertIn(r"replace(/[\r\n\t]+/g", desktop)
        self.assertIn(r"item.code+'\t'+item.name", desktop)
        self.assertIn("可粘贴到同花顺", desktop)
        self.assertIn("navigator.clipboard", desktop)
        self.assertIn("document.execCommand('copy')", desktop)
        self.assertNotIn("link.download=", desktop)
        self.assertRegex(desktop, r'/assets/workbench\.js\?v=\d+')
        # 手机版地址已统一跳转到同一响应式 dashboard，指标只维护一份。
        self.assertIn('href="dashboard.html"', mobile)
        self.assertIn('location.replace("dashboard.html"', mobile)


if __name__ == "__main__":
    unittest.main()
