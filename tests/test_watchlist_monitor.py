"""观察池按加入批次收益监控的本地回归测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import data.watchlist_monitor as monitor
from data.watchlist import WatchlistRepository
from data.watchlist_monitor import WatchlistMonitorRepository, WatchlistMonitorService


class WatchlistMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.stock = root / "stock.parquet"
        self.index = root / "index.parquet"
        dates = pd.to_datetime(["2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10"])
        rows = pd.DataFrame({
            "代码": ["sh600000"] * len(dates), "日期": dates,
            "开盘": [9, 10, 11, 12, 11], "最高": [10, 11, 12, 13, 12],
            "最低": [8, 9, 10, 11, 10], "收盘": [9.5, 10.5, 11.5, 12.5, 11.5],
        })
        rows.to_parquet(self.stock)
        benchmark = rows.copy(); benchmark["代码"] = "sh000300"
        benchmark[["开盘", "最高", "最低", "收盘"]] = [[100, 101, 99, 100]] * len(rows)
        benchmark.to_parquet(self.index)
        monitor.STOCK_CACHE_FILE = self.stock
        monitor.INDEX_CACHE_FILE = self.index
        monitor.ETF_CACHE_FILE = root / "missing-etf.parquet"
        self.db = root / "watchlist.sqlite3"
        self.watch = WatchlistRepository(self.db)
        self.outcomes = WatchlistMonitorRepository(self.db)
        self.service = WatchlistMonitorService(repository=self.outcomes, watchlist=self.watch)

    def tearDown(self):
        self.temp.cleanup()

    def _item(self, created_at="2026-09-06T09:00:00+08:00", user_id=1):
        item = self.watch.add(user_id=user_id, code="sh600000", screen_date="2026-09-04")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE watch_item SET created_at=? WHERE id=?", (created_at, item["id"]))
        return item

    def test_joined_date_skips_weekend_and_calculates_window(self):
        self._item()
        result = self.service.refresh(user_id=1, as_of="2026-09-10")
        self.assertEqual(result["rows"], 5)
        item = self.service.query(user_id=1, horizon=3)["items"][0]
        self.assertEqual(item["joined_date"], "2026-09-06")
        self.assertEqual(item["screen_date"], "2026-09-04")
        self.assertEqual(item["entry_date"], "2026-09-07")
        self.assertEqual(item["entry_open"], 10.0)
        self.assertAlmostEqual(item["peak_return_pct"], 30.0)
        self.assertAlmostEqual(item["close_return_pct"], 25.0)
        self.assertEqual(item["result_status"], "complete")

    def test_pending_and_user_isolation(self):
        self._item(created_at="2026-09-11T09:00:00+08:00")
        self._item(user_id=2)
        self.service.refresh(user_id=1, as_of="2026-09-10")
        self.service.refresh(user_id=2, as_of="2026-09-10")
        first = self.service.query(user_id=1, horizon=5)["items"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["result_status"], "pending")
        self.assertEqual(len(self.service.query(user_id=2, horizon=5)["items"]), 1)

    def test_status_source_batch_filters(self):
        self._item()
        self.service.refresh(user_id=1, as_of="2026-09-10")
        self.watch.set_status(1, "bought", user_id=1)
        bought = self.service.query(user_id=1, horizon=1, status="bought", batch="2026-09-06")
        self.assertEqual(len(bought["items"]), 1)
        self.assertEqual(bought["items"][0]["current_status"], "bought")


if __name__ == "__main__":
    unittest.main()
