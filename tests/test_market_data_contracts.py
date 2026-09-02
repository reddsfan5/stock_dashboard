import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from data.daily_basic import DailyBasicData
from data.enrichment import enrich_daily_caches
from data.schema import DAILY_BAR_COLUMNS, ensure_daily_bar_schema
from data.sources import normalize_source_daily_bars, tx_fetch_daily_snapshot
from data.kline import StockData
from data.etf import ETFData


class FakeResponse:
    def __init__(self, text):
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        return None


class MarketDataContractTest(unittest.TestCase):
    def test_source_normalization_preserves_volume_and_turnover_units(self):
        raw = pd.DataFrame([{
            "date": "2026-08-31",
            "open": "10",
            "high": "11",
            "low": "9",
            "close": "10.5",
            "volume": "123400",
            "amount": "1295700",
            "turnover": "0.0019",
        }])
        result = normalize_source_daily_bars(
            raw, "sh600001", turnover_scale=100
        )

        self.assertEqual(list(result.columns), list(DAILY_BAR_COLUMNS))
        self.assertEqual(result.loc[0, "成交量"], 123400)
        self.assertAlmostEqual(result.loc[0, "换手率%"], 0.19)

    @patch("data.sources.requests.get")
    def test_tencent_snapshot_splits_bar_and_daily_basic_fields(self, get):
        values = [""] * 50
        values[3] = "10.5"
        values[4] = "10.0"
        values[5] = "10.1"
        values[6] = "1234"
        values[30] = "20260831150000"
        values[33] = "10.8"
        values[34] = "9.9"
        values[35] = "10.5/1234/1295700"
        values[38] = "0.19"
        values[39] = "15.2"
        values[44] = "123.4"
        values[45] = "150.0"
        values[49] = "1.28"
        get.return_value = FakeResponse('v_sh600001="' + "~".join(values) + '";')

        frame, failed = tx_fetch_daily_snapshot(["sh600001"], retries=1)

        self.assertEqual(failed, [])
        self.assertEqual(frame.loc[0, "成交量"], 123400)
        self.assertEqual(frame.loc[0, "总市值"], 15_000_000_000)
        self.assertEqual(frame.loc[0, "供应商量比"], 1.28)

    def test_daily_basic_upsert_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DailyBasicData(os.path.join(directory, "basic.parquet"))
            row = pd.DataFrame([{
                "代码": "sh600001", "日期": "2026-08-31",
                "供应商量比": 1.2, "市盈率_动态": 10,
                "总市值": 100, "流通市值": 80,
            }])
            self.assertEqual(store.upsert(row), 1)
            row.loc[0, "供应商量比"] = 1.3
            self.assertEqual(store.upsert(row), 0)
            self.assertEqual(store.cache.loc[0, "供应商量比"], 1.3)

    def test_minute_enrichment_fills_missing_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            stock_path = os.path.join(directory, "stock.parquet")
            etf_path = os.path.join(directory, "etf.parquet")
            minute_path = os.path.join(directory, "minute.parquet")
            base = {
                "日期": "2026-08-31", "开盘": 10, "最高": 11,
                "最低": 9, "收盘": 10.5, "成交额": 10_000,
            }
            stock = ensure_daily_bar_schema(pd.DataFrame([
                {"代码": "sh600001", **base},
                {"代码": "sh600002", **base, "成交量": 999},
            ]))
            etf = ensure_daily_bar_schema(pd.DataFrame([
                {"代码": "510050", **base},
            ]))
            stock.to_parquet(stock_path, index=False)
            etf.to_parquet(etf_path, index=False)
            minute = pd.DataFrame([
                {"代码": "sh600001", "时间": "2026-08-31 09:31", "成交量": 100},
                {"代码": "sh600001", "时间": "2026-08-31 09:32", "成交量": 200},
                {"代码": "sh600002", "时间": "2026-08-31 09:31", "成交量": 500},
                {"代码": "sh510050", "时间": "2026-08-31 09:31", "成交量": 400},
            ])
            minute.to_parquet(minute_path, index=False)

            stats = enrich_daily_caches(minute_path, stock_path, etf_path)
            enriched_stock = pd.read_parquet(stock_path).set_index("代码")
            enriched_etf = pd.read_parquet(etf_path).set_index("代码")

        self.assertEqual(stats["stock"]["filled"], 1)
        self.assertEqual(enriched_stock.at["sh600001", "成交量"], 300)
        self.assertEqual(enriched_stock.at["sh600002", "成交量"], 999)
        self.assertEqual(enriched_etf.at["510050", "成交量"], 400)

    def test_forced_backfill_loads_existing_cache_before_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "stock.parquet")
            existing = ensure_daily_bar_schema(pd.DataFrame([
                {
                    "代码": "sh600001", "日期": "2026-08-29", "开盘": 10,
                    "最高": 11, "最低": 9, "收盘": 10.5, "成交额": 10_000,
                },
                {
                    "代码": "sh600002", "日期": "2026-08-29", "开盘": 20,
                    "最高": 21, "最低": 19, "收盘": 20.5, "成交额": 20_000,
                },
            ]))
            existing.to_parquet(path, index=False)

            def fetch(code, start, end):
                return ensure_daily_bar_schema(pd.DataFrame([{
                    "代码": code, "日期": "2026-08-31", "开盘": 11,
                    "最高": 12, "最低": 10, "收盘": 11.5,
                    "成交量": 100, "成交额": 11_000,
                }]))

            data = StockData(path)
            data.backfill_fields(
                pd.DataFrame({"代码": ["sh600001"]}),
                "2026-08-31", "2026-08-31",
                fetch_fn=fetch, threads=1, progress=False,
            )
            saved = pd.read_parquet(path)

        self.assertEqual(len(saved), 3)
        self.assertIn("sh600002", set(saved["代码"]))

    def test_stock_cache_rejects_accidental_large_row_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "stock.parquet")
            dates = pd.date_range("2023-01-01", periods=1_000)
            existing = ensure_daily_bar_schema(pd.DataFrame({
                "代码": ["sh600001"] * len(dates),
                "日期": dates,
                "开盘": 10,
                "最高": 11,
                "最低": 9,
                "收盘": 10.5,
                "成交额": 10_000,
            }))
            existing.to_parquet(path, index=False)
            data = StockData(path)
            data._cache = existing.head(10)

            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖日 K 缓存"):
                data._save_cache()

            self.assertEqual(len(pd.read_parquet(path)), 1_000)

    def test_etf_field_migration_does_not_erase_existing_turnover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "etf.parquet")
            existing = ensure_daily_bar_schema(pd.DataFrame([{
                "代码": "510050", "日期": "2026-08-31", "开盘": 3,
                "最高": 3.1, "最低": 2.9, "收盘": 3.05,
                "成交量": 100, "成交额": 1_000, "换手率%": 2.5,
            }]))
            existing.to_parquet(path, index=False)
            incoming = ensure_daily_bar_schema(pd.DataFrame([{
                "代码": "510050", "日期": "2026-08-31", "开盘": 3,
                "最高": 3.1, "最低": 2.9, "收盘": 3.05,
                "成交量": 200, "成交额": 2_000,
            }]))
            store = ETFData(path)
            store._update_existing_fields(incoming, ["成交量", "换手率%"])
            saved = pd.read_parquet(path)

        self.assertEqual(saved.loc[0, "成交量"], 200)
        self.assertEqual(saved.loc[0, "换手率%"], 2.5)


if __name__ == "__main__":
    unittest.main()
