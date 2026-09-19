import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from data.shortlist import ShortlistRepository
from data.shortlist_monitor import (
    ShortlistMonitorRepository,
    ShortlistMonitorService,
)


def _bars(code, rows):
    return pd.DataFrame(
        [
            {
                "代码": code,
                "日期": date,
                "开盘": open_price,
                "最高": high,
                "最低": low,
                "收盘": close,
            }
            for date, open_price, high, low, close in rows
        ]
    )


class ShortlistMonitorTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.db = root / "shortlist.sqlite3"
        self.stock = root / "stock.parquet"
        self.index = root / "index.parquet"
        self.etf = root / "etf.parquet"
        _bars("sh600000", [
            ("2026-09-04", 100, 100, 98, 100),
            ("2026-09-05", 101, 105, 99, 103),
            ("2026-09-08", 103, 108, 100, 104),
            ("2026-09-09", 104, 107, 102, 106),
        ]).to_parquet(self.stock, index=False)
        _bars("sh000300", [
            ("2026-09-04", 1000, 1005, 995, 1000),
            ("2026-09-05", 1001, 1010, 999, 1008),
            ("2026-09-08", 1008, 1020, 1005, 1015),
            ("2026-09-09", 1015, 1018, 1002, 1010),
        ]).to_parquet(self.index, index=False)
        pd.DataFrame(columns=["代码", "日期", "开盘", "最高", "最低", "收盘"]).to_parquet(self.etf, index=False)
        ShortlistRepository(self.db).save({
            "market_date": "2026-09-04",
            "generated_at": "2026-09-04T18:00:00+08:00",
            "candidate_count": 1,
            "cards": [{"rank": 1, "code": "sh600000", "name": "浦发银行", "sector": "银行", "score": 9.5}],
        })
        self.patchers = [
            patch("data.shortlist_monitor.STOCK_CACHE_FILE", str(self.stock)),
            patch("data.shortlist_monitor.INDEX_CACHE_FILE", str(self.index)),
            patch("data.shortlist_monitor.ETF_CACHE_FILE", str(self.etf)),
            patch("data.shortlist_monitor.MONITOR_CACHE", root / "monitor.json"),
        ]
        for item in self.patchers:
            item.start()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.tempdir.cleanup()

    def test_peak_close_drawdown_and_excess_use_next_open(self):
        service = ShortlistMonitorService(repository=ShortlistMonitorRepository(self.db))
        result = service.refresh(as_of="2026-09-09")
        self.assertEqual(result["rows"], 5)
        data = service.query(date_from="2026-09-04", date_to="2026-09-04", horizon=3, limit=10)
        row = data["items"][0]
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["entry_date"], "2026-09-05")
        self.assertEqual(row["entry_open"], 101.0)
        self.assertAlmostEqual(row["peak_high"], 108.0)
        self.assertAlmostEqual(row["peak_return_pct"], (108 / 101 - 1) * 100)
        self.assertAlmostEqual(row["close_value"], 106.0)
        self.assertAlmostEqual(row["adverse_return_pct"], (99 / 101 - 1) * 100)
        self.assertAlmostEqual(row["benchmark_close_return_pct"], (1010 / 1001 - 1) * 100)
        self.assertAlmostEqual(
            row["close_excess_pct"], row["close_return_pct"] - row["benchmark_close_return_pct"]
        )

    def test_pending_and_partial_are_not_reported_as_zero(self):
        service = ShortlistMonitorService(repository=ShortlistMonitorRepository(self.db))
        service.refresh(as_of="2026-09-04")
        pending = service.query(horizon=5, limit=10)["items"][0]
        self.assertEqual(pending["status"], "pending")
        self.assertIsNone(pending["peak_return_pct"])

        service.refresh(as_of="2026-09-05")
        partial = service.query(horizon=5, limit=10)["items"][0]
        self.assertEqual(partial["status"], "partial")
        self.assertIsNotNone(partial["peak_return_pct"])
        self.assertEqual(partial["available_through"], "2026-09-05")

    def test_filters_pagination_and_idempotent_refresh(self):
        service = ShortlistMonitorService(repository=ShortlistMonitorRepository(self.db))
        service.refresh(as_of="2026-09-09")
        first = service.query(horizon=1, sector="银行", limit=1)
        second = service.query(horizon=1, sector="不存在", limit=1)
        self.assertEqual(first["pagination"]["returned"], 1)
        self.assertFalse(first["pagination"]["has_more"])
        self.assertEqual(second["pagination"]["returned"], 0)
        service.refresh(as_of="2026-09-09")
        again = service.query(horizon=1, sector="银行", limit=1)["items"][0]
        self.assertEqual(again["peak_return_pct"], first["items"][0]["peak_return_pct"])

    def test_batch_selector_remains_available_when_view_is_filtered(self):
        ShortlistRepository(self.db).save({
            "market_date": "2026-09-05",
            "generated_at": "2026-09-05T18:00:00+08:00",
            "candidate_count": 1,
            "cards": [{"rank": 1, "code": "sh600000", "name": "浦发银行", "sector": "银行", "score": 9.0}],
        })
        service = ShortlistMonitorService(repository=ShortlistMonitorRepository(self.db))
        service.refresh(as_of="2026-09-09")
        result = service.query(date_from="2026-09-04", date_to="2026-09-04", horizon=5, limit=10)
        self.assertEqual(result["pagination"]["total"], 1)
        self.assertEqual(result["cohorts"][0]["market_date"], "2026-09-04")
        self.assertEqual(
            [(batch["market_date"], batch["count"]) for batch in result["batches"]],
            [("2026-09-05", 1), ("2026-09-04", 1)],
        )

    def test_refresh_failure_is_visible_until_next_success(self):
        repository = ShortlistMonitorRepository(self.db)
        repository.record_failure("缓存暂时不可用")
        service = ShortlistMonitorService(repository=repository)
        before = service.query(horizon=1, limit=1)
        self.assertTrue(any("缓存暂时不可用" in warning for warning in before["warnings"]))
        service.refresh(as_of="2026-09-09")
        after = service.query(horizon=1, limit=1)
        self.assertFalse(any("缓存暂时不可用" in warning for warning in after["warnings"]))


if __name__ == "__main__":
    unittest.main()
