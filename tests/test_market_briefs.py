import tempfile
import unittest
from pathlib import Path

from data.market_briefs import MarketBriefRepository
from scripts.services.market_brief import build_html


def sample_payload(**overrides):
    payload = {
        "brief_date": "2026-09-21",
        "market_date": "2026-09-18",
        "kind": "morning",
        "status": "complete",
        "data_as_of": "2026-09-21T07:55:00+08:00",
        "summary": {"headline": "盘前测试", "risk_level": "中等"},
        "metrics": [{"label": "纳指", "value": "+0.40%"}],
        "sections": {
            "watch_variables": [{"title": "开盘量价"}],
            "news": [
                {"title": f"重要资讯{i}", "publisher": "测试源"}
                for i in range(1, 9)
            ],
        },
        "charts": {"asset_performance": [{"name": "纳指", "change_pct": 0.4}]},
        "sources": [{"title": "测试来源", "url": "https://example.test/news"}],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


class MarketBriefRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "briefs.sqlite3"
        self.repo = MarketBriefRepository(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_publish_is_idempotent_and_changed_content_revises(self):
        first = self.repo.publish(sample_payload())
        same = self.repo.publish(sample_payload())
        changed = self.repo.publish(sample_payload(summary={"headline": "盘前修订"}))
        self.assertEqual(first["revision"], 1)
        self.assertTrue(same["idempotent"])
        self.assertEqual(changed["revision"], 2)

        day = MarketBriefRepository(self.path, read_only=True).day("2026-09-21")
        self.assertEqual(day["items"][0]["summary"]["headline"], "盘前修订")
        self.assertEqual(len(day["revisions"]), 2)
        old = self.repo.day("2026-09-21", kind="morning", revision=1)
        self.assertEqual(old["items"][0]["summary"]["headline"], "盘前测试")

    def test_kinds_can_share_a_day_and_latest_is_separate(self):
        self.repo.publish(sample_payload())
        self.repo.publish(sample_payload(
            kind="close_style", market_date="2026-09-21",
            status="partial", summary={"headline": "收盘风格"},
        ))
        day = self.repo.day("2026-09-21")
        self.assertEqual({row["kind"] for row in day["items"]}, {"morning", "close_style"})
        self.assertEqual(self.repo.latest("close_style")["summary"]["headline"], "收盘风格")
        self.assertEqual(self.repo.dates()["dates"][0]["close_style"], 1)

    def test_rejects_unsafe_source_and_future_market_date(self):
        with self.assertRaisesRegex(ValueError, "http/https"):
            self.repo.publish(sample_payload(sources=[{"url": "javascript:alert(1)"}]))
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            self.repo.publish(sample_payload(market_date="2026-09-22"))

    def test_complete_brief_requires_exactly_eight_news_items(self):
        with self.assertRaisesRegex(ValueError, "8 条重要资讯"):
            self.repo.publish(sample_payload(sections={"news": [{"title": "仅一条"}]}))
        partial = self.repo.publish(sample_payload(
            status="partial", sections={"news": [{"title": "可信来源暂不足"}]},
        ))
        self.assertEqual(partial["status"], "partial")

    def test_complete_close_brief_requires_attribution_and_morning_review(self):
        close = sample_payload(
            kind="close_style", market_date="2026-09-21",
            sections={"news": [{"title": f"收盘资讯{i}"} for i in range(1, 9)]},
        )
        with self.assertRaisesRegex(ValueError, "资讯驱动"):
            self.repo.publish(close)
        close["sections"]["news_attribution"] = [{"event": "政策信号"}]
        with self.assertRaisesRegex(ValueError, "晨间判断复盘"):
            self.repo.publish(close)
        close["sections"]["morning_review"] = [{"statement": "风险偏好回升"}]
        saved = self.repo.publish(close)
        self.assertEqual(saved["status"], "complete")

    def test_read_only_missing_database_is_clear(self):
        with self.assertRaisesRegex(LookupError, "尚未建立"):
            MarketBriefRepository(self.path.with_name("missing.sqlite3"), read_only=True).dates()


class MarketBriefPageTest(unittest.TestCase):
    def test_page_has_both_briefs_charts_and_read_only_apis(self):
        html = build_html()
        self.assertIn("08:00 盘前", html)
        self.assertIn("19:15 收盘", html)
        self.assertIn("盘前最重要的 8 条资讯", html)
        self.assertIn("资讯驱动与行情归因", html)
        self.assertIn("偏差来源", html)
        self.assertIn("次日修正", html)
        self.assertIn("/api/market-brief/dates", html)
        self.assertIn("/api/market-brief/day", html)
        self.assertIn("quadrantChart", html)
        self.assertIn("原始资讯", html)


if __name__ == "__main__":
    unittest.main()
