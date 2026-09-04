import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from data.global_markets import GlobalMarketsData, OVERSEAS_INDEXES
from data.index import IndexData
from data.index_minute import IndexMinuteData
from data.market_context import (
    MarketContextService,
    last_known_daily_bar,
    market_session_status,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class SessionStatusTest(unittest.TestCase):
    def test_us_overnight_during_china_morning(self):
        # 中国上午 10:00：美股当地仍是前一晚，通常为 closed/overnight 边界
        moment = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)
        status = market_session_status(moment, "America/New_York", "09:30", "16:00")
        self.assertIn(status, {"closed", "overnight", "not_yet_open"})
        # 09-03 10:00 CST = 09-02 22:00 EDT → 美股已收盘
        self.assertEqual(status, "closed")

    def test_hk_open_during_china_morning(self):
        moment = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)
        self.assertEqual(
            market_session_status(moment, "Asia/Hong_Kong", "09:30", "16:00"),
            "open",
        )

    def test_korea_open_offset(self):
        # 中国 10:00 = 韩国 11:00，开盘中
        moment = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)
        self.assertEqual(
            market_session_status(moment, "Asia/Seoul", "09:00", "15:30"),
            "open",
        )
        # 中国 15:00 = 韩国 16:00，已收盘
        moment_close = datetime(2026, 9, 3, 15, 0, tzinfo=SHANGHAI)
        self.assertEqual(
            market_session_status(moment_close, "Asia/Seoul", "09:00", "15:30"),
            "closed",
        )


class LastKnownBarTest(unittest.TestCase):
    def test_does_not_reveal_same_day_before_close(self):
        bars = pd.DataFrame({
            "日期": pd.to_datetime(["2026-09-01", "2026-09-02", "2026-09-03"]),
            "收盘": [100.0, 101.0, 102.0],
            "开盘": [99.0, 100.0, 101.0],
            "最高": [101.0, 102.0, 103.0],
            "最低": [98.0, 99.0, 100.0],
        })
        # 港股 09-03 10:00 尚未收盘，可知到 09-02
        moment = datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)
        known = last_known_daily_bar(bars, moment, "Asia/Hong_Kong", "16:00")
        self.assertEqual(pd.Timestamp(known["日期"]).strftime("%Y-%m-%d"), "2026-09-02")
        self.assertEqual(float(known["收盘"]), 101.0)

        # 收盘后可知当日
        after = datetime(2026, 9, 3, 16, 5, tzinfo=SHANGHAI)
        known_after = last_known_daily_bar(bars, after, "Asia/Hong_Kong", "16:00")
        self.assertEqual(pd.Timestamp(known_after["日期"]).strftime("%Y-%m-%d"), "2026-09-03")


class MarketContextServiceTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)

        # stub index daily
        self.index = IndexData()
        idx_path = root / "index.parquet"
        daily = pd.DataFrame({
            "代码": ["sh000001"] * 3 + ["sz399001"] * 3,
            "日期": pd.to_datetime(
                ["2026-09-01", "2026-09-02", "2026-09-03"] * 2
            ),
            "开盘": [3000, 3010, 3020, 10000, 10010, 10020],
            "最高": [3010, 3020, 3030, 10010, 10020, 10030],
            "最低": [2990, 3000, 3010, 9990, 10000, 10010],
            "收盘": [3005, 3015, 3025, 10005, 10015, 10025],
            "成交量(手)": [1.0] * 6,
            "来源": ["ak"] * 6,
        })
        # expand to all INDEXES keys for stability
        from data.index import INDEXES
        rows = []
        for code in INDEXES:
            for i, day in enumerate(["2026-09-01", "2026-09-02", "2026-09-03"]):
                rows.append({
                    "代码": code,
                    "日期": pd.Timestamp(day),
                    "开盘": 1000 + i,
                    "最高": 1010 + i,
                    "最低": 990 + i,
                    "收盘": 1005 + i,
                    "成交量(手)": 1.0,
                    "来源": "ak",
                })
        daily = pd.DataFrame(rows)
        daily.to_parquet(idx_path, index=False)
        self.index.cache_path = idx_path  # unused; monkey via property override

        class StubIndex(IndexData):
            def __init__(self, frame):
                self._frame = frame
                self.last_failed = []

            @property
            def cache(self):
                return self._frame.copy()

        self.stub_index = StubIndex(daily)

        minute_rows = []
        for minute in ("09:31", "09:32", "10:15", "14:55"):
            minute_rows.append({
                "代码": "sh000001",
                "时间": pd.Timestamp(f"2026-09-03 {minute}"),
                "开盘": 1005.0,
                "最高": 1005.0,
                "最低": 1005.0,
                "收盘": 1005.0 + (1 if minute == "10:15" else 0) + (2 if minute == "14:55" else 0),
                "成交量": 1.0,
                "成交额": 1.0,
            })
        # only sh000001 has minutes; others fall back

        class StubMinute(IndexMinuteData):
            def __init__(self, frame):
                self._frame = frame
                self.codes = list(INDEXES)
                self.last_failed = []

            @property
            def cache(self):
                return self._frame.copy()

            def points_as_of(self, code, market_date, as_of):
                return IndexMinuteData.points_as_of(self, code, market_date, as_of)

        self.stub_minute = StubMinute(pd.DataFrame(minute_rows))

        overseas_rows = []
        for code, meta in OVERSEAS_INDEXES.items():
            for i, day in enumerate(["2026-09-01", "2026-09-02", "2026-09-03"]):
                overseas_rows.append({
                    "代码": code,
                    "名称": meta["name"],
                    "区域": meta["region"],
                    "日期": pd.Timestamp(day),
                    "开盘": 2000 + i,
                    "最高": 2010 + i,
                    "最低": 1990 + i,
                    "收盘": 2005 + i,
                })

        class StubGlobal(GlobalMarketsData):
            def __init__(self, frame):
                self._frame = frame
                self.indexes = OVERSEAS_INDEXES
                self.last_failed = []

            @property
            def cache(self):
                return self._frame.copy()

        self.stub_global = StubGlobal(pd.DataFrame(overseas_rows))
        self.service = MarketContextService(
            index_data=self.stub_index,
            index_minute=self.stub_minute,
            global_markets=self.stub_global,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_a_share_minute_truncated_no_lookahead(self):
        early = self.service.context("2026-09-03", "10:15")
        sh = next(x for x in early["a_share"] if x["code"] == "sh000001")
        self.assertEqual(sh["source"], "minute")
        self.assertEqual(sh["time"], "10:15")
        self.assertEqual(sh["price"], 1006.0)

        # later minute must not appear at 10:15
        self.assertNotEqual(sh["price"], 1007.0)

        late = self.service.context("2026-09-03", "14:55")
        sh_late = next(x for x in late["a_share"] if x["code"] == "sh000001")
        self.assertEqual(sh_late["price"], 1007.0)

    def test_overseas_hk_no_same_day_before_close(self):
        payload = self.service.context("2026-09-03", "10:15")
        hsi = next(x for x in payload["overseas"] if x["code"] == "HSI")
        self.assertEqual(hsi["status"], "open")
        self.assertEqual(hsi["bar_date"], "2026-09-02")
        self.assertEqual(hsi["price"], 2006.0)

    def test_us_shows_prior_session_during_china_day(self):
        payload = self.service.context("2026-09-03", "10:15")
        ndx = next(x for x in payload["overseas"] if x["code"] == "IXIC")
        # 中国 09-03 10:15 → 美东 09-02 晚间，09-02 已收盘可知；09-03 美股未开
        self.assertEqual(ndx["bar_date"], "2026-09-02")
        self.assertEqual(ndx["status"], "closed")


    def test_hk_minute_as_of_truncation(self):
        """港股分钟按上海 as_of 转当地后截断，不剧透后续点。"""
        from data.index import INDEXES
        rows = list(self.stub_minute.cache.to_dict("records"))
        for minute, price in (("09:31", 25000.0), ("10:15", 25100.0), ("14:00", 25200.0)):
            rows.append({
                "代码": "hkHSI",
                "时间": pd.Timestamp(f"2026-09-03 {minute}"),
                "开盘": 25000.0,
                "最高": price,
                "最低": 24900.0,
                "收盘": price,
                "成交量": 1.0,
                "成交额": 1.0,
            })
        frame = pd.DataFrame(rows)

        class StubMinute(IndexMinuteData):
            def __init__(self, frame):
                self._frame = frame
                self.codes = list(INDEXES) + ["hkHSI"]
                self.last_failed = []

            @property
            def cache(self):
                return self._frame.copy()

            def points_as_of(self, code, market_date, as_of):
                return IndexMinuteData.points_as_of(self, code, market_date, as_of)

        service = MarketContextService(
            index_data=self.stub_index,
            index_minute=StubMinute(frame),
            global_markets=self.stub_global,
        )
        early = service.context("2026-09-03", "10:15")
        hsi = next(x for x in early["overseas"] if x["code"] == "HSI")
        self.assertEqual(hsi["source"], "minute")
        self.assertEqual(hsi["time"], "10:15")
        self.assertEqual(hsi["price"], 25100.0)
        self.assertEqual(hsi["bar_date"], "2026-09-03")
        self.assertNotEqual(hsi["price"], 25200.0)

        # 无分钟时仍走日线可知逻辑
        late_no = self.service.context("2026-09-03", "10:15")
        hsi_daily = next(x for x in late_no["overseas"] if x["code"] == "HSI")
        self.assertEqual(hsi_daily["source"], "daily")
        self.assertEqual(hsi_daily["bar_date"], "2026-09-02")


class AlignTrainerDatesTest(unittest.TestCase):
    def test_intersection_prefers_overlap(self):
        from data.index_minute import align_trainer_dates
        dates, warned, code = align_trainer_dates(
            ["2026-08-25", "2026-09-01", "2026-09-02"],
            ["2026-09-01", "2026-09-02", "2026-09-03"],
        )
        self.assertEqual(dates, ["2026-09-01", "2026-09-02"])
        self.assertFalse(warned)
        self.assertIsNone(code)

    def test_empty_intersection_falls_back_with_warning(self):
        from data.index_minute import align_trainer_dates
        dates, warned, code = align_trainer_dates(
            ["2026-08-25", "2026-08-26"],
            ["2026-09-01"],
        )
        self.assertEqual(dates, ["2026-08-25", "2026-08-26"])
        self.assertTrue(warned)
        self.assertEqual(code, "index_minute_no_overlap")

    def test_empty_index_dates_falls_back(self):
        from data.index_minute import align_trainer_dates
        dates, warned, code = align_trainer_dates(["2026-08-25"], [])
        self.assertEqual(dates, ["2026-08-25"])
        self.assertTrue(warned)
        self.assertEqual(code, "index_minute_empty")



if __name__ == "__main__":
    unittest.main()
