import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data.journal import JournalRepository
from scripts.services.minute_viewer import MinuteRepository
from scripts.services.stock_journal import DailyKlineRepository, build_html


class JournalRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "journal.sqlite3"
        self.repo = JournalRepository(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_case_lifecycle_is_append_only_and_persists(self):
        case = self.repo.create_case(
            code="sh600001", title="平台突破观察", thesis="等待量价确认", tags="趋势,平台"
        )
        self.assertEqual(case["status"], "watching")
        planned = self.repo.add_entry(
            case_id=case["id"], event_type="entry_plan", market_date="2026-08-25",
            reason="等待站稳平台", trigger_condition="收盘突破10.50",
            invalidation_condition="跌破9.80", next_review_date="2026-08-26",
        )
        self.assertEqual(planned["event_type"], "entry_plan")
        self.assertEqual(self.repo.get_case(case["id"])["status"], "planned")

        self.repo.add_entry(
            case_id=case["id"], event_type="entry", market_date="2026-08-26",
            reason="放量突破后按计划进场", price=10.55,
        )
        reopened = JournalRepository(self.db_path)
        persisted = reopened.get_case(case["id"])
        self.assertEqual(persisted["status"], "holding")
        self.assertEqual(len(persisted["entries"]), 2)
        self.assertEqual(persisted["entries"][0]["reason"], "等待站稳平台")

    def test_due_review_only_uses_latest_open_case_entry(self):
        case = self.repo.create_case(code="sz000001", title="等待支撑")
        self.repo.add_entry(
            case_id=case["id"], event_type="watch", market_date="2026-08-20",
            reason="观察缩量", next_review_date="2026-08-21",
        )
        self.assertEqual(len(self.repo.due_reviews(as_of="2026-08-22")), 1)
        self.repo.add_entry(
            case_id=case["id"], event_type="review", market_date="2026-08-22",
            reason="已经复查，继续等待",
        )
        self.assertEqual(self.repo.due_reviews(as_of="2026-08-23"), [])

    def test_soft_delete_hides_entry_rolls_back_status_and_can_restore(self):
        case = self.repo.create_case(code="sz000002", title="回收站测试")
        planned = self.repo.add_entry(
            case_id=case["id"], event_type="entry_plan", market_date="2026-08-25",
            reason="等待突破",
        )
        entered = self.repo.add_entry(
            case_id=case["id"], event_type="entry", market_date="2026-08-26",
            reason="已经买入", price=10.2,
        )
        self.assertEqual(self.repo.get_case(case["id"])["status"], "holding")

        deleted = self.repo.soft_delete_entry(entered["id"], "日期选错")
        self.assertIsNotNone(deleted["deleted_at"])
        self.assertEqual(deleted["deleted_reason"], "日期选错")
        self.assertEqual(self.repo.get_case(case["id"])["status"], "planned")
        self.assertEqual(
            [row["id"] for row in self.repo.list_entries(case_id=case["id"])],
            [planned["id"]],
        )
        self.assertEqual(
            len(self.repo.list_entries(case_id=case["id"], include_deleted=True)), 2
        )

        restored = self.repo.restore_entry(entered["id"])
        self.assertIsNone(restored["deleted_at"])
        self.assertEqual(self.repo.get_case(case["id"])["status"], "holding")

    def test_rejects_invalid_event_and_creates_consistent_backup(self):
        case = self.repo.create_case(code="sh600001", title="测试")
        with self.assertRaisesRegex(ValueError, "事件类型"):
            self.repo.add_entry(
                case_id=case["id"], event_type="rewrite_history",
                market_date="2026-08-25", reason="不允许",
            )
        with self.assertRaisesRegex(ValueError, "有限数字"):
            self.repo.add_entry(
                case_id=case["id"], event_type="watch",
                market_date="2026-08-25", reason="不允许非有限价格",
                price=float("nan"),
            )
        with self.assertRaisesRegex(ValueError, "整数"):
            self.repo.add_entry(
                case_id=case["id"], event_type="watch",
                market_date="2026-08-25", reason="持有天数应是整数",
                planned_holding_days=1.5,
            )
        backup = self.repo.backup(Path(self.temp.name) / "backups")
        copied = JournalRepository(backup)
        self.assertEqual(copied.get_case(case["id"])["title"], "测试")


class DailyKlineRepositoryTest(unittest.TestCase):
    def test_minute_availability_is_lightweight_and_date_specific(self):
        repository = MinuteRepository.__new__(MinuteRepository)
        with patch.object(
            repository, "available_dates", return_value=["2026-08-25", "2026-08-26"]
        ):
            found = repository.availability("600519", "2026-08-25")
            missing = repository.availability("sh600519", "2026-08-27")
        self.assertTrue(found["available"])
        self.assertFalse(missing["available"])
        self.assertEqual(missing["latest_date"], "2026-08-26")
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            repository.availability("600519", "20260825")

    def test_filters_single_symbol_and_calculates_moving_averages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.parquet"
            dates = pd.bdate_range("2026-01-02", periods=70)
            frame = pd.DataFrame({
                "代码": "sh600001", "日期": dates,
                "开盘": range(1, 71), "最高": range(2, 72),
                "最低": [value - 0.5 for value in range(1, 71)],
                "收盘": [value + 0.5 for value in range(1, 71)],
                "前收": range(1, 71), "成交量": 1000,
                "成交额": 100000, "换手率%": 1.2,
            })
            frame.to_parquet(path, index=False)
            repository = DailyKlineRepository({"sh600001": "测试股"})
            with patch.object(repository, "_source", return_value=(path, "sh600001")):
                payload = repository.payload("600001", 60)
            self.assertEqual(payload["name"], "测试股")
            self.assertEqual(len(payload["bars"]), 60)
            self.assertIsNotNone(payload["bars"][-1]["ma60"])
            self.assertEqual(payload["latest"]["date"], dates[-1].strftime("%Y-%m-%d"))

    def test_missing_source_previous_close_falls_back_to_prior_daily_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.parquet"
            dates = pd.bdate_range("2026-01-02", periods=35)
            closes = [10.0 + index for index in range(35)]
            frame = pd.DataFrame({
                "代码": "sh600001", "日期": dates,
                "开盘": closes, "最高": [value + 0.2 for value in closes],
                "最低": [value - 0.2 for value in closes], "收盘": closes,
                "前收": None, "成交量": 1000, "成交额": 100000,
                "换手率%": 1.2,
            })
            frame.to_parquet(path, index=False)
            repository = DailyKlineRepository({"sh600001": "测试股"})
            with patch.object(repository, "_source", return_value=(path, "sh600001")):
                payload = repository.payload("600001", 30)

            first = payload["bars"][0]
            self.assertEqual(first["pre_close"], closes[4])
            self.assertAlmostEqual(
                first["change_pct"], (closes[5] / closes[4] - 1) * 100, places=3
            )

    def test_page_contains_chart_case_and_append_only_controls(self):
        html = build_html()
        self.assertIn('id="dailyChart"', html)
        self.assertIn("研究案例", html)
        self.assertIn("继续持有", html)
        self.assertIn("记录采用追加模式", html)
        self.assertIn("回收站", html)
        self.assertIn("/api/journal/entry/delete", html)
        self.assertIn("/api/journal/entry/restore", html)
        self.assertIn("/api/minute/available", html)
        self.assertIn("/minute_view.html?", html)
        self.assertIn('id="minutePanel"', html)
        self.assertIn("syncDiaryFields", html)
        self.assertIn("openMinuteForBar", html)
        self.assertIn("区间涨跌", html)
        self.assertIn("最新收", html)
        self.assertIn("v===null", html)
        self.assertIn('id="minuteReplayPlay"', html)
        self.assertIn("createIntradayReplay", html)
        self.assertIn("尚未播放", html)
        self.assertNotIn("__INTRADAY_REPLAY_", html)


if __name__ == "__main__":
    unittest.main()
