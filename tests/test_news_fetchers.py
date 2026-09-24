"""新闻抓取适配器单元测试（全部 mock，不访问外网）。"""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from data import news_fetchers as nf
from data.news_fetchers import (
    EastmoneyFlashNewsClient,
    PoliteHttpSession,
    WallstreetcnLiveClient,
    fetch_source,
)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class PoliteSessionTests(unittest.TestCase):
    def setUp(self):
        nf._host_last_request.clear()

    def test_rejects_aggressive_interval(self):
        with self.assertRaises(ValueError):
            PoliteHttpSession(min_interval=0.2)

    def test_enforces_min_interval_and_max_requests(self):
        sleeps: list[float] = []
        clock = FakeClock()
        session = MagicMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None
        session.get.return_value = response

        def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            clock.advance(seconds)

        http = PoliteHttpSession(
            min_interval=1.5,
            max_requests=2,
            max_retries=0,
            session=session,
            sleeper=sleeper,
            clock=clock,
        )
        http.get_json("https://polite-test.example/a")
        clock.advance(0.2)
        http.get_json("https://polite-test.example/b")
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 1.3, places=4)
        with self.assertRaises(RuntimeError):
            http.get_json("https://polite-test.example/c")


class EastmoneyClientTests(unittest.TestCase):
    def test_filters_target_day_and_normalizes(self):
        http = MagicMock()
        http.request_count = 1
        http.get_json.return_value = {
            "news": [
                {
                    "newsid": "n1",
                    "title": "测试标题",
                    "digest": "摘要",
                    "url_w": "http://finance.eastmoney.com/a/n1.html",
                    "showtime": "2026-09-23 10:00:00",
                    "sort": "1",
                },
                {
                    "newsid": "n0",
                    "title": "昨天",
                    "digest": "",
                    "url_unique": "https://finance.eastmoney.com/a/n0.html",
                    "showtime": "2026-09-22 23:00:00",
                    "sort": "0",
                },
            ],
            "PageCount": 1,
        }
        client = EastmoneyFlashNewsClient(http=http, max_pages=2)
        result = client.fetch_day("2026-09-23")
        self.assertEqual(result.source_key, "eastmoney")
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item["id"], "n1")
        self.assertEqual(item["title"], "测试标题")
        self.assertTrue(item["url"].startswith("https://"))
        self.assertEqual(item["published_at"], "2026-09-23T10:00:00")
        self.assertEqual(item["market_date"], "2026-09-23")


class WallstreetcnClientTests(unittest.TestCase):
    def test_uses_content_text_when_title_empty(self):
        http = MagicMock()
        http.request_count = 1
        sh = ZoneInfo("Asia/Shanghai")
        moment = datetime(2026, 9, 23, 9, 30, tzinfo=sh)
        http.get_json.return_value = {
            "data": {
                "items": [
                    {
                        "id": 42,
                        "title": "",
                        "content_text": "日经高开",
                        "content": "<p>日经高开</p>",
                        "display_time": int(moment.timestamp()),
                        "uri": "https://wallstreetcn.com/livenews/42",
                        "tags": [{"name": "宏观"}],
                        "channels": ["global-channel"],
                        "score": 2,
                    }
                ],
                "next_cursor": None,
            }
        }
        client = WallstreetcnLiveClient(http=http, max_pages=2)
        result = client.fetch_day("2026-09-23")
        self.assertEqual(len(result.items), 1)
        item = result.items[0]
        self.assertEqual(item["id"], "42")
        self.assertIn("日经", item["title"])
        self.assertEqual(item["importance"], 2)
        self.assertIn("宏观", item["tags"])


class FetchSourceTests(unittest.TestCase):
    def test_unknown_source(self):
        with self.assertRaises(ValueError):
            fetch_source("not-a-source", "2026-09-23")


if __name__ == "__main__":
    unittest.main()
