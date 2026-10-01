import unittest

import numpy as np
import pandas as pd

from scripts.reports import sector_lead_stats as sls
from scripts.reports.gen_sector_corr_cloud import attach_graph, render_html
from scripts.reports.sector_style import STYLES, compute_style_tags, sector_pe, style_spread


def _toy(n=300, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-06-02", periods=n)
    b = rng.normal(0, 1.0, n)
    cols = {}
    for k, beta in enumerate([1.8, 1.7, 1.6, 0.3, 0.25, 0.2, 1.0, 1.0, 1.0, 1.0]):
        cols[f"s{k}"] = beta * b + rng.normal(0, 0.4 + 0.3 * beta, n)
    return pd.DataFrame(cols, index=idx), pd.Series(b, index=idx)


class SectorStyleTest(unittest.TestCase):
    def test_rules_and_overrides(self):
        daily, bench = _toy()
        l1 = {f"s{k}": "电子" for k in range(10)}
        l1["s6"] = "钢铁"
        l1["s9"] = "银行"
        pe = pd.DataFrame({"pe": [80, 70, 60, 10, 9, 8, 15, 90, 12, 6], "loss": [0.1] * 10}, index=[f"s{k}" for k in range(10)])
        cfg = {"min_obs": 200, "l1_overrides": {"银行": "防御"}, "overrides": {"s0": "价值"}}
        out = compute_style_tags(daily, bench, l1, pe, cfg)
        t = out["tags"]
        self.assertEqual(t["s1"]["p"], "进攻")          # 高 beta / 高波动
        self.assertEqual(t["s4"]["p"], "防御")          # 低 beta / 低波动
        self.assertEqual(t["s6"]["p"], "周期")          # 中等风险 + 一级行业在周期名单
        self.assertEqual(t["s7"]["p"], "成长")          # 中等风险 + 高市盈率分位
        self.assertEqual(t["s8"]["p"], "价值")          # 中等风险 + 低市盈率分位
        self.assertEqual(t["s9"]["p"], "防御")          # 一级行业覆盖
        self.assertEqual(t["s9"]["src"], "l1_override")
        self.assertEqual(t["s0"]["p"], "价值")          # 二级覆盖优先，规则结果降为副标签
        self.assertEqual(t["s0"]["s"], "进攻")
        self.assertEqual(t["s0"]["src"], "override")
        self.assertGreater(t["s1"]["beta"], t["s4"]["beta"])
        self.assertEqual(sum(out["counts"].values()), 10)
        self.assertEqual(out["styles"], STYLES)

    def test_spread_and_pe(self):
        daily, _ = _toy(40)
        tags = {c: {"p": "进攻" if c in ("s0", "s1") else ("防御" if c in ("s3", "s4") else "价值")} for c in daily.columns}
        sp = style_spread(daily, tags)
        exp = daily[["s0", "s1"]].mean(axis=1) - daily[["s3", "s4"]].mean(axis=1)
        self.assertTrue(np.allclose(sp.to_numpy(), exp.to_numpy()))
        basic = pd.DataFrame({"bare": ["1", "2", "3", "1"], "日期": ["2026-09-29", "2026-09-30", "2026-09-30", "2026-09-30"],
                              "市盈率_动态": [5, 20, -3, 10]})
        mp = pd.DataFrame({"bare": ["1", "2", "3"], "sector": ["甲", "甲", "乙"]})
        pe = sector_pe(basic, mp)
        self.assertEqual(pe.loc["甲", "pe"], 15)       # 只用最新日期的正市盈率中位数
        self.assertEqual(pe.loc["乙", "loss"], 1.0)

    def test_page_and_config(self):
        nodes = [{"id": s, "l1": "电子", "last": 0.5} for s in ("甲", "乙", "丙")]
        payload = attach_graph({"nodes": nodes, "edges": [{"source": "甲", "target": "乙", "corr": 0.8, "abs": 0.8, "sign": 1}], "n_sectors": 3})
        payload["style"] = {"styles": STYLES, "colors": {k: "#888888" for k in STYLES}, "counts": {"进攻": 1, "防御": 2},
                            "tags": {"甲": {"p": "进攻", "s": None}, "乙": {"p": "防御", "s": None}, "丙": {"p": "防御", "s": None}}, "view": {"links": True}}
        html = render_html({"start": "2022-01-01", "end": "2026-09-30", **payload})
        for needle in ('id="readGuide"', 'popover="auto"', 'id="guideBtn"', 'id="cmStyle"', 'id="styleLinkBtn"', 'id="rpRA"',
                       "function updateRiskAppetite(", "const styleLinks", "hidePopover", 'class="lg-colhd"'):
            self.assertIn(needle, html, needle)
        legend = html[html.index('<details class="legend"'):html.index('</details>', html.index('<details class="legend"'))]
        self.assertNotIn('id="lgScale"', legend)  # 编码说明已移入读图说明
        cfg = sls.load_config()
        st = cfg["style"]
        for key in ("lookback_days", "risk_hi", "risk_lo", "cyclical_l1", "growth_pe_pct", "l1_overrides", "overrides", "links", "colors"):
            self.assertIn(key, st)
        self.assertEqual(st["l1_overrides"].get("银行"), "防御")
        self.assertEqual(st["overrides"].get("半导体"), "进攻")
        self.assertEqual(set(st["colors"]), set(STYLES))
        self.assertIn("style", cfg["signals"]["type_caps"])


if __name__ == "__main__":
    unittest.main()
