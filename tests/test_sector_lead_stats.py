import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.reports import sector_lead_stats as sls
from scripts.reports.gen_sector_corr_cloud import load_benchmark, render_html


def _cfg(**lead):
    cfg = sls.load_config("/nonexistent.yaml")
    cfg["lead"].update(lead)
    return cfg


class BHTest(unittest.TestCase):
    def test_bh_qvalues_match_reference(self):
        p = np.array([0.01, 0.04, 0.03, 0.20, np.nan])
        q = sls.bh_qvalues(p)
        np.testing.assert_allclose(q[:4], [0.04, 0.16 / 3, 0.16 / 3, 0.20])
        self.assertTrue(np.isnan(q[4]))

    def test_perm_pvalues_are_conservative(self):
        pool = np.sort(np.array([0.1, 0.2, 0.3, 0.4]))
        p = sls.perm_pvalues(np.array([0.35, 0.5]), pool)
        np.testing.assert_allclose(p, [2 / 5, 1 / 5])


class EventStatsTest(unittest.TestCase):
    def test_outcome_is_aligned_to_lag_week(self):
        rng = np.random.default_rng(1)
        a = rng.normal(size=300)
        b = np.roll(a, 2)  # b_{t+2} = a_t
        ev = sls.event_stats(a, b, 2, 1, thr_hi=np.quantile(a, 0.8), thr_lo=np.quantile(a, 0.2))
        self.assertEqual(ev["hit"], 1.0)
        self.assertTrue(sls.direction_agrees(ev, 1))
        self.assertFalse(sls.direction_agrees(ev, -1))
        # 每个事件对应唯一结果周：事件数不超过可用周数的 40%（两个 20% 分位）
        self.assertLessEqual(ev["n_events"], int(0.4 * 298) + 2)

    def test_oos_window_uses_given_thresholds_only(self):
        a = np.arange(20, dtype=float)
        b = np.r_[np.zeros(1), a[:-1]] - 9.5
        ev = sls.event_stats(a, b, 1, 1, thr_hi=100.0, thr_lo=-100.0, t0=10, t1=20)
        self.assertEqual(ev["n_events"], 0)


class SelectionTest(unittest.TestCase):
    def test_planted_lead_passes_fdr_and_noise_does_not(self):
        rng = np.random.default_rng(7)
        t, n = 240, 12
        w = rng.normal(size=(t, n))
        w[1:, 1] = 0.7 * w[:-1, 0] + 0.5 * rng.normal(size=t - 1)  # 0 → 1, lag 1
        names = [f"s{i}" for i in range(n)]
        edges = sls.select_edges(w, names, _cfg(min_events=10))
        passed = {(e["source"], e["target"]) for e in edges if e["fdr_pass"]}
        self.assertIn(("s0", "s1"), passed)
        self.assertLessEqual(len(passed - {("s0", "s1")}), 1)
        top = next(e for e in edges if (e["source"], e["target"]) == ("s0", "s1"))
        self.assertEqual(top["lag"], 1)
        self.assertEqual(top["m_tests"], n * (n - 1))

    def test_walk_forward_reports_oos_for_planted_edge(self):
        rng = np.random.default_rng(3)
        t, n = 200, 6
        w = rng.normal(size=(t, n))
        w[2:, 3] = 0.8 * w[:-2, 2] + 0.4 * rng.normal(size=t - 2)
        cfg = _cfg(min_events=8)
        cfg["oos"].update(min_train_weeks=100, test_weeks=25, step_weeks=25)
        wf = sls.walk_forward(w, [f"s{i}" for i in range(n)], cfg)
        rec = wf["per_edge"][("s2", "s3")]
        self.assertGreater(rec["n_events"], 10)
        self.assertGreater(rec["lift"], 0.2)


class RollingBetaTest(unittest.TestCase):
    def test_residuals_do_not_use_future_data(self):
        idx = pd.bdate_range("2024-01-01", periods=200)
        rng = np.random.default_rng(0)
        mkt = pd.Series(rng.normal(size=200), index=idx)
        piv = pd.DataFrame({"A": 1.5 * mkt + rng.normal(scale=0.1, size=200)}, index=idx)
        r1 = sls.rolling_residualize(piv, mkt, 60, 30)
        piv2 = piv.copy()
        piv2.iloc[150:] *= 5
        r2 = sls.rolling_residualize(piv2, mkt, 60, 30)
        pd.testing.assert_series_equal(r1["A"].iloc[:150], r2["A"].iloc[:150])
        self.assertTrue(r1["A"].iloc[:30].isna().all() and r1["A"].iloc[30:].notna().all())
        self.assertLess(r1["A"].iloc[100:].abs().mean(), 0.2)


class BenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.idx = pd.bdate_range("2024-01-01", periods=50)
        self.piv = pd.DataFrame({"A": np.linspace(-1, 1, 50), "B": np.linspace(1, -1, 50)}, index=self.idx)

    def test_uses_hs300_from_index_cache(self):
        cache = pd.DataFrame({"代码": "sh000300", "日期": self.idx, "收盘": np.linspace(100, 110, 50), "来源": "ak"})
        r, meta = load_benchmark(self.piv, index_cache=cache)
        self.assertEqual(meta["code"], "sh000300")
        self.assertTrue(meta["ok"])
        self.assertIsNone(meta["warning"])
        self.assertAlmostEqual(r.iloc[1], (100 + 10 / 49) / 100 * 100 - 100, places=6)

    def test_missing_benchmark_warns_instead_of_silent_fallback(self):
        r, meta = load_benchmark(self.piv, index_cache=pd.DataFrame(columns=["代码", "日期", "收盘"]))
        self.assertFalse(meta["ok"])
        self.assertIn("等权", meta["warning"])
        pd.testing.assert_series_equal(r, self.piv.mean(axis=1))


class PageTest(unittest.TestCase):
    def test_page_has_fdr_toggle_and_warning_bar(self):
        html = render_html({"start": "2022-01-01", "end": "2026-08-14", "n_sectors": 0, "nodes": [], "edges": []})
        self.assertIn('id="showFailed"', html)
        self.assertIn('id="warnBar"', html)
        self.assertIn("LEAD_DISPLAY.per_core_direction ?? 3", html)
        self.assertIn("if (!filterIds || !filterIds.size) return []", html)
        self.assertIn("python -m scripts.reports.gen_sector_corr_cloud --no-nav", html)


    def test_page_ux_controls_legend_and_local_three(self):
        html = render_html({"start": "2022-01-01", "end": "2026-08-14", "n_sectors": 0, "nodes": [], "edges": []})
        for needle in ('id="legend"', 'id="railHome"', 'id="expandBox"', 'id="lgScale"',
                       '/assets/chart-touch.js', '/assets/vendor/three/three.module.min.js',
                       'cdn.jsdelivr.net/npm/three@0.160.0/+esm', 'function exactMatches(q)',
                       'function refreshFocus()', 'camera.setViewOffset', 'LineDashedMaterial',
                       "CFG_UI.default_mode === 'lead' ? 'lead' : 'sync'", '领先·实验'):
            self.assertIn(needle, html, needle)

    def test_vendored_three_is_self_contained(self):
        root = Path(__file__).resolve().parents[1] / "scripts" / "services" / "static" / "vendor" / "three"
        self.assertTrue((root / "three.module.min.js").stat().st_size > 100_000)
        self.assertTrue((root / "LICENSE").exists())
        orbit = (root / "OrbitControls.js").read_text(encoding="utf-8")
        self.assertIn("from './three.module.min.js'", orbit)
        self.assertNotIn("from 'three'", orbit)

    def test_display_config_defaults_to_sync(self):
        cfg = sls.load_config()
        disp = cfg["display"]
        self.assertEqual(disp["default_mode"], "sync")
        for key in ("top_movers_labels", "label_limit", "color_clip_pct", "pick_radius_px",
                    "pick_radius_touch_px", "mobile_panel_vh", "auto_rotate"):
            self.assertIn(key, disp)
        self.assertGreater(disp["pick_radius_touch_px"], disp["pick_radius_px"])
        self.assertTrue(0.5 < disp["color_clip_pct"] <= 1)


if __name__ == "__main__":
    unittest.main()
