"""观察池、次日跟踪与板块强度单元测试（无网络）。"""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from data.watchlist import (
    WatchlistRepository,
    compute_next_day_return,
    rank_sector_strength,
)


class WatchlistRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = WatchlistRepository(Path(self.temp.name) / "watchlist.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_add_status_lifecycle(self):
        item = self.repo.add(
            code="sh600519",
            name="贵州茅台",
            status="watching",
            source_module="continuity",
            screen_date="2026-09-01",
            thesis="连续性候选",
        )
        self.assertEqual(item["status"], "watching")
        self.assertEqual(item["status_label"], "观察中")
        planned = self.repo.set_status(item["id"], "planned")
        self.assertEqual(planned["status"], "planned")
        bought = self.repo.set_status(item["id"], "bought", note="已按计划买入")
        self.assertEqual(bought["status"], "bought")
        dropped = self.repo.set_status(item["id"], "dropped")
        self.assertEqual(dropped["status"], "dropped")

    def test_upsert_active_same_code(self):
        a = self.repo.add(code="sz000001", status="watching", thesis="一")
        b = self.repo.add(code="sz000001", status="planned", thesis="二", screen_date="2026-09-02")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(b["status"], "planned")
        self.assertEqual(b["thesis"], "二")

    def test_tracking_refresh_with_mock_kline(self):
        item = self.repo.add(
            code="sh600000",
            status="watching",
            source_module="sideways",
            screen_date="2026-09-01",
        )
        kline = pd.DataFrame({
            "日期": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
            "收盘": [10.0, 9.5, 9.8],
        })

        def loader(code):
            self.assertEqual(code, "sh600000")
            return kline

        result = self.repo.refresh_tracking(loader, fail_threshold_pct=-3.0)
        self.assertEqual(result["tracked"], 1)
        tracks = self.repo.list_tracks(track_date="2026-09-02")
        self.assertEqual(len(tracks), 1)
        self.assertAlmostEqual(tracks[0]["return_pct"], -5.0)
        self.assertTrue(tracks[0]["pattern_failed"])
        self.assertEqual(tracks[0]["item_id"], item["id"])


class NextDayReturnTest(unittest.TestCase):
    def test_compute_and_fail_flag(self):
        kline = pd.DataFrame({
            "日期": pd.to_datetime(["2026-08-28", "2026-08-29"]),
            "收盘": [20.0, 20.5],
        })
        ok = compute_next_day_return(kline, code="sz000002", screen_date="2026-08-28")
        self.assertEqual(ok["track_date"], "2026-08-29")
        self.assertAlmostEqual(ok["return_pct"], 2.5)
        self.assertFalse(ok["pattern_failed"])

        missing = compute_next_day_return(kline, code="sz000002", screen_date="2026-08-29")
        self.assertIsNone(missing["track_date"])
        self.assertIn("尚无次日", missing["message"])


class SectorStrengthTest(unittest.TestCase):
    def test_rank_sector_strength(self):
        daily = pd.DataFrame({
            "代码": ["sh600001", "sh600002", "sz000001", "sz000002"],
            "日期": pd.to_datetime(["2026-09-03"] * 4),
            "收盘": [11.0, 10.5, 9.8, 9.5],
            "前收": [10.0, 10.0, 10.0, 10.0],
        })
        info = pd.DataFrame({
            "代码": ["sh600001", "sh600002", "sz000001", "sz000002"],
            "申万1级": ["白酒", "白酒", "银行", "银行"],
        })
        ranked = rank_sector_strength(daily, info, market_date="2026-09-03", top_n=5)
        self.assertEqual(ranked["market_date"], "2026-09-03")
        self.assertEqual(ranked["sectors"][0]["sector"], "白酒")
        self.assertGreater(ranked["sectors"][0]["avg_change_pct"], ranked["sectors"][1]["avg_change_pct"])
        self.assertEqual(len(ranked["sectors"]), 2)


if __name__ == "__main__":
    unittest.main()
