import tempfile
import unittest
from pathlib import Path

from data.market_news import FetchResult, MarketNewsRepository, classify_phase
from scripts.services.market_news import build_html


class FakeNewsClient:
    def __init__(self):
        self.calls = 0

    def fetch_day(self, market_date):
        self.calls += 1
        rows = [
            self.item("n1", market_date, "08:45:00", "隔夜与政策要闻"),
            self.item("n2", market_date, "11:31:00", "A股午间收评"),
            self.item("n3", market_date, "15:12:00", "A股收盘综述"),
        ]
        return FetchResult(
            items=rows,
            source_newest_at=f"{market_date}T15:12:00",
            source_oldest_at=f"{market_date}T08:45:00",
            complete=True,
            message="测试数据已加载",
        )

    @staticmethod
    def item(news_id, market_date, clock, title):
        published_at = f"{market_date}T{clock}"
        return {
            "id": news_id,
            "seq": news_id,
            "title": title,
            "digest": title + "摘要",
            "url": f"https://example.test/{news_id}",
            "source": "测试源",
            "published_at": published_at,
            "market_date": market_date,
            "phase": classify_phase(published_at),
            "tags": ["A股"],
            "importance": 3,
        }


class MarketNewsRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.client = FakeNewsClient()
        self.repo = MarketNewsRepository(
            Path(self.tempdir.name) / "news.sqlite3", client=self.client
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_as_of_filter_prevents_news_lookahead(self):
        result = self.repo.day("2026-08-25", as_of="11:00")
        self.assertEqual([item["id"] for item in result["items"]], ["n1"])
        self.assertEqual(result["counts"], {"pre_open": 1, "midday": 0, "close": 0})
        self.assertEqual(self.client.calls, 1)

        full_day = self.repo.day("2026-08-25")
        self.assertEqual(len(full_day["items"]), 3)
        self.assertEqual(self.client.calls, 1, "完整历史日期应直接复用缓存")

    def test_impact_record_is_persistent_and_soft_deletable(self):
        self.repo.day("2026-08-25")
        impact = self.repo.add_impact(
            news_id="n2",
            market_date="2026-08-25",
            decision_time="12:05",
            code="520500",
            action="select",
            stance="bullish",
            note="午间政策信息促使我把标的加入观察，但仍等待量价确认。",
        )
        self.assertEqual(impact["code"], "sh520500")
        self.assertEqual(len(self.repo.impacts(market_date="2026-08-25")), 1)
        self.repo.delete_impact(impact["id"])
        self.assertEqual(self.repo.impacts(market_date="2026-08-25"), [])
        self.assertEqual(
            len(self.repo.impacts(market_date="2026-08-25", include_deleted=True)), 1
        )

    def test_rejects_impact_for_different_market_date(self):
        self.repo.day("2026-08-25")
        with self.assertRaisesRegex(ValueError, "发布日期一致"):
            self.repo.add_impact(
                news_id="n1", market_date="2026-08-26", action="watch",
                stance="uncertain", note="日期不一致",
            )

    def test_rejects_decision_time_before_news_was_published(self):
        self.repo.day("2026-08-25")
        with self.assertRaisesRegex(ValueError, "不能早于资讯发布时间"):
            self.repo.add_impact(
                news_id="n2", market_date="2026-08-25", decision_time="10:00",
                action="watch", stance="uncertain", note="不应允许时间穿越",
            )


class MarketNewsPageTest(unittest.TestCase):
    def test_page_contains_no_lookahead_and_impact_endpoints(self):
        html = build_html()
        self.assertIn("无剧透", html)
        self.assertIn("/api/news/day", html)
        self.assertIn("/api/news/impact", html)


if __name__ == "__main__":
    unittest.main()
