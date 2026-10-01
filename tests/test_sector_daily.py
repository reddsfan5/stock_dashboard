import unittest

import pandas as pd

from scripts.reports import sector_lead_stats as sls
from scripts.reports.gen_sector_corr_cloud import attach_graph, render_html
from scripts.reports.sector_daily import sector_daily_returns
from scripts.reports.sector_graph import GRAPH_DEFAULTS


def _bars():
    days = pd.bdate_range("2026-09-01", periods=6)
    rows = []
    for k, d in enumerate(days):
        # A：前收缺失（NaN）→ 回退上一收盘；B：前收齐全；C：第 4 天异常跳变 → 剔除
        rows.append({"代码": "sh600001", "日期": d, "收盘": 10.0 * 1.01 ** (k + 1), "前收": float("nan")})
        rows.append({"代码": "sz000002", "日期": d, "收盘": 20.0 * 0.98, "前收": 20.0})
        rows.append({"代码": "sz000003", "日期": d, "收盘": 50.0 if k == 3 else 10.0, "前收": 10.0})
    return pd.DataFrame(rows)


class SectorDailyTest(unittest.TestCase):
    cfg = {"replay_days": 3, "periods": [1, 2], "max_abs_ret": 35.0, "min_members": 1}

    def _run(self):
        mapping = pd.DataFrame({"代码": ["600001", "000002", "000003"], "sector": ["甲", "乙", "丙"]})
        return sector_daily_returns(_bars(), mapping, ["甲", "乙", "丙", "空"], self.cfg)

    def test_window_and_compact_bp(self):
        d = self._run()
        self.assertEqual(len(d["dates"]), 3 + 2 - 1)  # replay_days + max(periods) - 1
        self.assertEqual(d["replay_days"], 3)
        self.assertEqual(d["ids"], ["甲", "乙", "丙", "空"])
        self.assertEqual(len(d["bp"]), 4)
        for row in d["bp"]:
            self.assertEqual(len(row), len(d["dates"]))
            self.assertTrue(all(v is None or isinstance(v, int) for v in row))

    def test_prev_close_fallback_outlier_and_missing(self):
        d = self._run()
        self.assertTrue(all(v == 100 for v in d["bp"][0]))   # 甲：+1.00% = 100bp（上一收盘回退）
        self.assertTrue(all(v == -200 for v in d["bp"][1]))  # 乙：-2.00%
        i3 = d["dates"].index(pd.bdate_range("2026-09-01", periods=6)[3].strftime("%Y-%m-%d"))
        self.assertIsNone(d["bp"][2][i3])                     # 丙：+400% 被剔除
        self.assertTrue(all(v is None for v in d["bp"][3]))   # 无成分股

    def test_empty_bars(self):
        d = sector_daily_returns(pd.DataFrame(), pd.DataFrame(columns=["代码", "sector"]), ["甲"], self.cfg)
        self.assertEqual(d["dates"], [])
        self.assertEqual(d["replay_days"], 0)

    def test_page_has_halo_replay_controls(self):
        nodes = [{"id": s, "l1": "电子", "last": 0.5} for s in ("甲", "乙", "丙")]
        edges = [{"source": "甲", "target": "乙", "corr": 0.8, "abs": 0.8, "sign": 1}]
        payload = attach_graph({"nodes": nodes, "edges": edges, "n_sectors": 3})
        payload["daily"] = self._run()
        html = render_html({"start": "2022-01-01", "end": "2026-09-30", **payload})
        for needle in ('id="replayBar"', 'id="rpPlay"', 'id="rpSlider"', 'id="rpDate"', 'id="rpPeriod"',
                       "function setRetTargets(", "function updateCommSummary(", "function updateWebColors(",
                       "function playReplay(", "function setDay(", "function setPeriod(", "HALO_UP", '"daily"'):
            self.assertIn(needle, html, needle)

    def test_config_keys(self):
        cfg = sls.load_config()
        d = cfg["display"]
        for key in ("default_period", "replay_step_ms", "replay_tween_ms", "replay_loop", "halo_max_scale",
                    "halo_max_opacity", "halo_up_color", "halo_down_color", "breathe_top_n", "breathe_max",
                    "breathe_period_ms", "breathe_amp", "edge_tint", "edge_tint_mix"):
            self.assertIn(key, d)
        self.assertLess(d["replay_tween_ms"], d["replay_step_ms"])
        self.assertEqual(sorted(cfg["daily"]["periods"]), [1, 5, 20])
        self.assertIn(d["default_period"], cfg["daily"]["periods"])

    def test_palette_avoids_red_green(self):
        import colorsys
        for hx in GRAPH_DEFAULTS["palette"]:
            r, g, b = (int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5))
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            hue = h * 360
            if s < 0.25:
                continue
            self.assertFalse(hue < 15 or hue > 340, f"{hx} 偏红")
            self.assertFalse(90 < hue < 170, f"{hx} 偏绿")


if __name__ == "__main__":
    unittest.main()
