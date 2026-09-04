"""训练闭环仓储单元测试（无网络）。"""

import tempfile
import unittest
from pathlib import Path

from data.training_sessions import (
    TrainingSessionRepository,
    check_plan_violations,
    truncate_visible_context,
)


class TrainingSessionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "training_sessions.sqlite3"
        self.repo = TrainingSessionRepository(self.db_path)
        self.run = self.repo.create_run(
            session_token="tok-phase1",
            code="sh600519",
            name="贵州茅台",
            start_date="2026-08-25",
            capital=100_000,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_upsert_day_plan(self):
        plan = self.repo.upsert_day_plan(
            run_id=self.run["id"],
            market_date="2026-08-25",
            thesis="等待回踩再买",
            max_loss_pct=2.5,
            max_loss_amount=2500,
            entry_style="等待回踩",
            notes="不开板不追",
        )
        self.assertEqual(plan["thesis"], "等待回踩再买")
        self.assertEqual(plan["entry_style"], "等待回踩")
        self.assertAlmostEqual(plan["max_loss_pct"], 2.5)

        updated = self.repo.upsert_day_plan(
            run_id=self.run["id"],
            market_date="2026-08-25",
            thesis="改成不追涨",
            max_loss_pct=1.5,
            entry_style="不追涨",
        )
        self.assertEqual(updated["id"], plan["id"])
        self.assertEqual(updated["thesis"], "改成不追涨")
        self.assertEqual(updated["entry_style"], "不追涨")
        self.assertEqual(len(self.repo.list_day_plans(self.run["id"])), 1)

    def test_add_decision_requires_reason_for_buy_sell(self):
        with self.assertRaisesRegex(ValueError, "交易理由"):
            self.repo.add_decision(
                run_id=self.run["id"],
                event_type="buy",
                market_date="2026-08-25",
                as_of="10:30",
                emotion="自信",
                reason="",
                require_reason=True,
            )
        ok = self.repo.add_decision(
            run_id=self.run["id"],
            event_type="buy",
            market_date="2026-08-25",
            as_of="10:30",
            reason="站上均线后买入半仓",
            emotion="自信",
            side="buy",
            shares=100,
            price=1800.0,
            planned_stop=1750.0,
            planned_target=1900.0,
            context={"price": 1800.0},
        )
        self.assertEqual(ok["reason"], "站上均线后买入半仓")
        self.assertEqual(ok["emotion"], "自信")
        with self.assertRaisesRegex(ValueError, "情绪标签"):
            self.repo.add_decision(
                run_id=self.run["id"],
                event_type="sell",
                market_date="2026-08-25",
                as_of="14:00",
                reason="止盈",
                emotion="兴奋",
            )

    def test_mindset_marker(self):
        marker = self.repo.add_mindset_marker(
            run_id=self.run["id"],
            market_date="2026-08-25",
            as_of="11:00",
            tag="FOMO",
            note="涨太快不敢追",
        )
        self.assertEqual(marker["tag"], "FOMO")
        listed = self.repo.list_mindset_markers(self.run["id"], market_date="2026-08-25")
        self.assertEqual(len(listed), 1)
        deleted = self.repo.soft_delete_mindset_marker(marker["id"])
        self.assertIsNotNone(deleted["deleted_at"])
        self.assertEqual(
            self.repo.list_mindset_markers(self.run["id"], market_date="2026-08-25"),
            [],
        )

    def test_check_plan_violations(self):
        plan = {
            "max_loss_pct": 2.0,
            "entry_style": "不追涨",
        }
        decisions = [
            {
                "id": 1,
                "event_type": "buy",
                "emotion": "FOMO",
                "market_date": "2026-08-25",
                "as_of": "10:05",
            }
        ]
        violations = check_plan_violations(plan, decisions, day_return_pct=-3.5)
        codes = {item["code"] for item in violations}
        self.assertIn("max_loss_pct", codes)
        self.assertIn("no_chase", codes)

        wait_plan = {"entry_style": "等待回踩", "max_loss_pct": None}
        wait_v = check_plan_violations(wait_plan, decisions, day_return_pct=1.0)
        self.assertEqual(wait_v[0]["code"], "wait_pullback")
        self.assertEqual(check_plan_violations(None, decisions), [])

    def test_build_review(self):
        self.repo.upsert_day_plan(
            run_id=self.run["id"],
            market_date="2026-08-25",
            thesis="不追涨",
            max_loss_pct=2.0,
            entry_style="不追涨",
        )
        self.repo.add_decision(
            run_id=self.run["id"],
            event_type="buy",
            market_date="2026-08-25",
            as_of="10:30",
            reason="追高买入",
            emotion="追涨",
            side="buy",
            shares=100,
            price=100.0,
            context={
                "news_summary": [{"time": "09:31", "title": "消息A", "tags": ["宏观"]}],
                "a_share": [{"code": "sh000001", "name": "上证", "change_pct": 0.5}],
            },
        )
        self.repo.add_mindset_marker(
            run_id=self.run["id"],
            market_date="2026-08-25",
            as_of="10:31",
            tag="后悔",
            note="不该追",
        )
        review = self.repo.build_review(
            self.run["id"],
            market_date="2026-08-25",
            account={"return_pct": -1.0, "total_pnl": -1000, "unrealized_pnl": -1000},
            orders=[
                {
                    "date": "2026-08-25",
                    "side": "buy",
                    "filled_shares": 100,
                    "fill_price": 100.0,
                    "commission": 5.0,
                    "stamp_tax": 0.0,
                }
            ],
        )
        self.assertTrue(review["plan_adherence"]["has_plan"])
        self.assertFalse(review["plan_adherence"]["ok"])
        self.assertEqual(review["pnl_attribution"]["buy_notional"], 10000.0)
        self.assertEqual(review["pnl_attribution"]["fees"], 5.0)
        emotions = [row["emotion"] for row in review["emotion_timeline"]]
        self.assertIn("追涨", emotions)
        self.assertIn("后悔", emotions)
        self.assertTrue(review["linked_news"])

    def test_truncate_visible_context(self):
        payload = truncate_visible_context(
            market={
                "price": 10.5,
                "change_pct": 1.2,
                "indicators": {
                    "intraday_volume_ratio": 1.5,
                    "vwap_deviation_pct": 0.3,
                    "secret_future": 999,
                },
            },
            account={"available_cash": 50000, "equity": 100000, "password": "x"},
            news_items=[
                {"time": "09:31:00", "title": "T" * 200, "tags": ["a", "b", "c", "d"]}
                for _ in range(12)
            ],
            market_context={
                "a_share": [{"code": "sh000001", "name": "上证", "change_pct": 0.1, "price": 3000}],
                "overseas": [
                    {"code": f"idx{i}", "name": f"N{i}", "change_pct": i, "status": "closed"}
                    for i in range(10)
                ],
            },
            max_news=8,
        )
        self.assertEqual(payload["price"], 10.5)
        self.assertNotIn("secret_future", payload["indicators"])
        self.assertNotIn("password", payload["account"])
        self.assertLessEqual(len(payload["news_summary"]), 8)
        self.assertLessEqual(len(payload["overseas"]), 6)
        self.assertEqual(len(payload["news_summary"][0]["title"]), 120)
        self.assertLessEqual(len(payload["news_summary"]), 8)


if __name__ == "__main__":
    unittest.main()
