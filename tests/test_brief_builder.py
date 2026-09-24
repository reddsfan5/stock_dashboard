"""规则保底简报生成器测试（不访问外网）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from data.brief_builder import build_brief, _score_event, _pick_events, is_sensitive_event
from data.market_briefs import MarketBriefRepository


class ScoreTests(unittest.TestCase):
    def test_higher_importance_and_sources_rank_first(self):
        low = {"title": "a", "importance": 1, "verification_status": "reported", "source_count": 1, "platforms": []}
        high = {"title": "b", "importance": 9, "verification_status": "verified", "source_count": 3, "platforms": [1, 2, 3]}
        self.assertGreater(_score_event(high), _score_event(low))

    def test_pick_events_dedupes_similar_titles(self):
        events = [
            {"title": "央行降准 1", "importance": 8, "verification_status": "verified", "source_count": 2, "platforms": []},
            {"title": "央行降准 1", "importance": 7, "verification_status": "reported", "source_count": 1, "platforms": []},
            {"title": "地方债发行", "importance": 6, "verification_status": "reported", "source_count": 1, "platforms": []},
        ]
        picked = _pick_events(events, limit=8)
        self.assertEqual(len(picked), 2)

    def test_sensitive_events_are_skipped(self):
        events = [
            {
                "title": "某地发生恐怖袭击",
                "digest": "详情省略",
                "importance": 99,
                "verification_status": "verified",
                "source_count": 5,
                "platforms": [],
            },
            {
                "title": "央行开展逆回购",
                "digest": "流动性投放",
                "importance": 6,
                "verification_status": "reported",
                "source_count": 1,
                "platforms": [],
            },
        ]
        self.assertTrue(is_sensitive_event(events[0]))
        self.assertFalse(is_sensitive_event(events[1]))
        picked = _pick_events(events, limit=8)
        self.assertEqual(len(picked), 1)
        self.assertIn("逆回购", picked[0]["title"])


class BuildBriefTests(unittest.TestCase):
    def test_partial_when_fewer_than_eight_events(self):
        fake_day = {
            "events": [
                {
                    "event_id": f"e{i}",
                    "title": f"事件{i}",
                    "digest": f"摘要{i}",
                    "importance": 5,
                    "verification_status": "reported",
                    "verification_label": "单源报道",
                    "source_count": 1,
                    "platforms": [{"name": "测试", "url": "https://example.test/x", "published_at": "2026-09-24T07:00:00+08:00"}],
                    "tags": ["测试"],
                    "first_published_at": "2026-09-24T07:00:00+08:00",
                    "original_source": "测试",
                }
                for i in range(3)
            ]
        }
        repo = MagicMock()
        repo.day.return_value = fake_day
        with patch("data.brief_builder.build_market_context", return_value={"a_share": [], "overseas": []}):
            payload = build_brief(kind="morning", brief_date="2026-09-24", news_repo=repo)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(len(payload["sections"]["news"]), 3)
        # must validate
        clean = MarketBriefRepository.validate(payload, kind="morning")
        self.assertEqual(clean["kind"], "morning")
        self.assertEqual(clean["status"], "partial")

    def test_complete_close_style_with_eight_events(self):
        fake_day = {
            "events": [
                {
                    "event_id": f"e{i}",
                    "title": f"收盘事件{i}",
                    "digest": f"摘要{i}",
                    "importance": 8 - i % 3,
                    "verification_status": "corroborated" if i % 2 == 0 else "reported",
                    "verification_label": "交叉印证",
                    "source_count": 2,
                    "platforms": [
                        {"name": "东财", "url": "https://example.test/a", "published_at": "2026-09-23T15:00:00+08:00"},
                        {"name": "见闻", "url": "https://example.test/b", "published_at": "2026-09-23T15:10:00+08:00"},
                    ],
                    "tags": ["科技", "芯片"] if i % 2 == 0 else ["消费"],
                    "first_published_at": "2026-09-23T15:00:00+08:00",
                    "original_source": "测试",
                }
                for i in range(10)
            ]
        }
        repo = MagicMock()
        repo.day.return_value = fake_day
        with patch("data.brief_builder.build_market_context", return_value={
            "a_share": [{"name": "上证指数", "change_pct": 0.5, "price": 3900}],
            "overseas": [],
        }), patch("data.brief_builder.MarketBriefRepository") as MR:
            MR.return_value.day.return_value = {"items": []}
            payload = build_brief(kind="close_style", brief_date="2026-09-23", news_repo=repo)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(len(payload["sections"]["news"]), 8)
        self.assertTrue(payload["sections"]["news_attribution"])
        self.assertTrue(payload["sections"]["morning_review"])
        clean = MarketBriefRepository.validate(payload, kind="close_style")
        self.assertEqual(clean["status"], "complete")


    def test_charts_filled_from_context(self):
        fake_day = {"events": [
            {
                "event_id": "e1",
                "title": "央行开展逆回购",
                "digest": "流动性投放",
                "importance": 6,
                "verification_status": "reported",
                "verification_label": "单源报道",
                "source_count": 1,
                "platforms": [{"name": "测试", "url": "https://example.test/x", "published_at": "2026-09-23T15:00:00+08:00"}],
                "tags": ["金融"],
                "first_published_at": "2026-09-23T15:00:00+08:00",
                "original_source": "测试",
            }
        ]}
        repo = MagicMock()
        repo.day.return_value = fake_day
        context = {
            "a_share": [{"name": "上证指数", "change_pct": 0.5, "price": 3900, "bar_date": "2026-09-23", "time": "15:00", "source": "test"}],
            "overseas": [{"name": "恒生指数", "change_pct": -0.8, "bar_date": "2026-09-23", "time": "15:00", "source": "test"}],
        }
        with patch("data.brief_builder.build_market_context", return_value=context), \
             patch("data.brief_builder._index_close_series", return_value={}), \
             patch("data.brief_builder._sector_strength", return_value=[{"name": "电子", "value": 1.2}]):
            payload = build_brief(kind="close_style", brief_date="2026-09-23", news_repo=repo)
        charts = payload.get("charts") or {}
        self.assertIn("asset_performance", charts)
        self.assertEqual(charts["asset_performance"][0]["name"], "上证指数")
        self.assertIn("sector_strength", charts)
        clean = MarketBriefRepository.validate(payload, kind="close_style")
        self.assertIn("asset_performance", clean.get("charts") or {})


if __name__ == "__main__":
    unittest.main()
