import tempfile
import unittest
import json
from pathlib import Path

import numpy as np
import pandas as pd

from features.daily import compute_daily_features
from features.intraday import add_intraday_volume_ratio


def daily_frame(days=30):
    dates = pd.bdate_range("2026-01-01", periods=days)
    rows = []
    for code, scale in (("sh600001", 1.0), ("sz000001", 2.0)):
        for index, date in enumerate(dates, start=1):
            close = 10 + index / 10 * scale
            rows.append({
                "代码": code,
                "日期": date,
                "开盘": close - 0.1,
                "最高": close + 0.2,
                "最低": close - 0.2,
                "收盘": close,
                "成交量": index * 100 * scale,
                "成交额": index * 1_000 * scale,
                "换手率%": index / 10,
            })
    return pd.DataFrame(rows)


class DailyFeatureTest(unittest.TestCase):
    def test_volume_and_amount_ratio_exclude_current_day(self):
        frame = daily_frame()
        result = compute_daily_features(frame, use_cache=False, profile="liquidity")
        code = "sh600001"
        date = result["close"].index[20]
        expected_volume = 2_100 / np.mean(np.arange(1, 21) * 100)
        expected_amount = 21_000 / np.mean(np.arange(1, 21) * 1_000)

        self.assertAlmostEqual(result["volume_ratio_20"].at[date, code], expected_volume)
        self.assertAlmostEqual(result["amount_ratio_20"].at[date, code], expected_amount)
        self.assertEqual(
            result["vol_ratio_20"].at[date, code],
            result["amount_ratio_20"].at[date, code],
        )

    def test_research_profile_adds_risk_features(self):
        frame = daily_frame()
        core = compute_daily_features(frame, use_cache=False, profile="core")
        research = compute_daily_features(frame, use_cache=False, profile="research")

        self.assertNotIn("atr14_pct", core)
        self.assertNotIn("volume_ratio_20", core)
        self.assertIn(
            "volume_ratio_20",
            compute_daily_features(frame, use_cache=False, profile="liquidity"),
        )
        self.assertIn("atr14_pct", research)
        self.assertIn("market_relative_mom20", research)

    def test_feature_cache_invalidates_when_latest_values_change(self):
        frame = daily_frame()
        with tempfile.TemporaryDirectory() as cache_dir:
            first = compute_daily_features(frame, cache_dir=cache_dir)
            changed = frame.copy()
            mask = (
                (changed["代码"] == "sh600001")
                & (changed["日期"] == changed["日期"].max())
            )
            changed.loc[mask, "成交额"] *= 2
            second = compute_daily_features(changed, cache_dir=cache_dir)

        date = changed["日期"].max()
        self.assertNotEqual(
            first["amount_ratio_20"].at[date, "sh600001"],
            second["amount_ratio_20"].at[date, "sh600001"],
        )

    def test_compatibility_aliases_do_not_duplicate_cache_files(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            result = compute_daily_features(daily_frame(), cache_dir=cache_dir)
            directory = next(Path(cache_dir).iterdir())
            manifest = json.loads(
                (directory / "manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(manifest["aliases"]["vol"], "amount")
            self.assertEqual(
                manifest["aliases"]["vol_ratio_20"], "amount_ratio_20"
            )
            self.assertFalse((directory / "vol.parquet").exists())
            self.assertIs(result["vol"], result["amount"])


class IntradayFeatureTest(unittest.TestCase):
    def test_intraday_ratio_uses_prior_days_at_same_minute(self):
        rows = []
        for day, multiplier in zip(pd.date_range("2026-08-24", periods=4), (1, 2, 3, 4)):
            for clock, volume in (("09:31", 100), ("09:32", 200)):
                rows.append({
                    "代码": "sh600001",
                    "时间": f"{day.date()} {clock}",
                    "成交量": volume * multiplier,
                })
        result = add_intraday_volume_ratio(
            pd.DataFrame(rows), lookback=3, min_periods=3
        )
        last = result[result["时间"].dt.date == pd.Timestamp("2026-08-27").date()]

        self.assertTrue(np.allclose(last["盘中量比"], 2.0))


if __name__ == "__main__":
    unittest.main()
