import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from scripts.reports.gen_sector_corr_cloud import _latest_market_snapshot, render_html


class SectorCloudSnapshotTest(unittest.TestCase):
    def test_latest_board_uses_daily_kline_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stocks.parquet"
            pd.DataFrame([
                {"代码": "sh600001", "日期": "2026-09-21", "收盘": 10.0, "前收": 9.8},
                {"代码": "sh600002", "日期": "2026-09-21", "收盘": 20.0, "前收": 20.0},
                {"代码": "sh600001", "日期": "2026-09-22", "收盘": 10.5, "前收": 10.0},
                {"代码": "sh600002", "日期": "2026-09-22", "收盘": 19.0, "前收": 20.0},
            ]).assign(日期=lambda frame: pd.to_datetime(frame["日期"])).to_parquet(path, index=False)
            info = pd.DataFrame([
                {"代码": "sh600001", "申万2级": "设备"},
                {"代码": "sh600002", "申万2级": "设备"},
            ])
            with patch("scripts.reports.gen_sector_corr_cloud.STOCK_KLINE", path):
                result = _latest_market_snapshot(info, "申万2级", {"设备"})

        self.assertEqual(result["as_of"], "2026-09-22")
        self.assertAlmostEqual(result["stock_last"]["600001"], 5.0)
        self.assertAlmostEqual(result["stock_last"]["600002"], -5.0)
        self.assertAlmostEqual(result["sector_last"]["设备"], 0.0)
        self.assertEqual(result["coverage"], 1.0)

    def test_footer_distinguishes_relationship_and_quote_dates(self):
        html = render_html({
            "start": "2022-01-01", "end": "2026-08-14", "market_as_of": "2026-09-22",
            "n_sectors": 0, "corr_thr": 0.4, "lead_thr": 0.1, "horizon": 5,
            "generated_at": "2026-09-22 18:40", "nodes": [], "edges": [],
        })
        self.assertIn("关系样本 2022-01-01 → 2026-08-14", html)
        self.assertIn("涨跌榜 2026-09-22", html)


if __name__ == "__main__":
    unittest.main()
