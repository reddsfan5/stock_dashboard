import tempfile
import unittest
from pathlib import Path

import pandas as pd

from pipeline.daily_update import DailyUpdatePipeline


class DailyCoverageTest(unittest.TestCase):
    def test_bj_provider_gap_does_not_hide_or_block_hs_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily.parquet"
            rows = []
            for date in ("2026-09-04", "2026-09-07"):
                rows.extend(
                    {"代码": code, "日期": pd.Timestamp(date)}
                    for code in ("sh600001", "sz000001")
                )
            rows.extend(
                {"代码": code, "日期": pd.Timestamp("2026-09-04")}
                for code in ("bj920001", "bj920002")
            )
            pd.DataFrame(rows).to_parquet(path, index=False)

            coverage = DailyUpdatePipeline._daily_coverage(
                str(path), pd.Timestamp("2026-09-07"),
                required_markets=("sh", "sz"),
            )

        self.assertEqual(coverage["coverage"], 1.0)
        self.assertEqual(coverage["overall_coverage"], 0.5)
        self.assertEqual(coverage["markets"]["bj"]["covered"], 0)
        self.assertEqual(coverage["markets"]["bj"]["expected"], 2)
        self.assertEqual(coverage["required_markets"], ["sh", "sz"])


if __name__ == "__main__":
    unittest.main()
