import unittest

from scripts.reports.shortlist_cards import (
    assemble_shortlist,
    diversify_pick,
    extract_dashboard_payload,
    score_candidate,
)


class ShortlistCardsTest(unittest.TestCase):
    def test_extract_and_assemble_from_mini_html(self):
        html = """
        <script>
        var KLINE_META={"sh600000":{"name":"浦发银行","sector":"银行","metrics":{"指标日期":"2026-09-11","成交量比20":1.8,"成交额比20":1.5,"换手率%":1.2,"20日动量%":4.0,"市场相对强弱20%":3.0,"ATR14%":3.5,"距60日高点%":-5.0,"动态PE":6.0,"流通市值(亿)":2000}},
        "sz000001":{"name":"平安银行","sector":"银行","metrics":{"指标日期":"2026-09-11","成交量比20":1.1,"成交额比20":1.0,"换手率%":0.8,"20日动量%":1.0,"市场相对强弱20%":0.5,"ATR14%":4.0,"距60日高点%":-8.0,"动态PE":5.0,"流通市值(亿)":1800}},
        "sz002594":{"name":"比亚迪","sector":"汽车","metrics":{"指标日期":"2026-09-11","成交量比20":2.0,"成交额比20":2.2,"换手率%":2.5,"20日动量%":6.0,"市场相对强弱20%":5.0,"ATR14%":5.0,"距60日高点%":-3.0,"动态PE":20.0,"流通市值(亿)":7000}}};
        var TAB_CODES={"hammer":["sh600000","sz002594"],"sideways":["sz002594","sz000001"],"trend-down":["sz000001"]};
        </script>
        """
        meta, tabs = extract_dashboard_payload(html)
        self.assertIn("sh600000", meta)
        sector = {
            "sectors": [
                {"sector": "汽车", "avg_change_pct": 2.0},
                {"sector": "银行", "avg_change_pct": 0.5},
            ],
            "weak_sectors": [],
        }
        payload = assemble_shortlist(
            kline_meta=meta,
            tab_codes=tabs,
            sector_payload=sector,
            watch_by_code={"sh600000": {"status": "watching", "status_label": "观察中", "thesis": "金针观察"}},
            limit=10,
        )
        codes = [c["code"] for c in payload["cards"]]
        self.assertTrue(codes)
        self.assertIn("sz002594", codes)  # 双命中+强板块应靠前
        bydi = next(c for c in payload["cards"] if c["code"] == "sz002594")
        self.assertTrue(any("命中" in x for x in bydi["why"]))
        self.assertTrue(bydi["risks"])
        pf = next(c for c in payload["cards"] if c["code"] == "sh600000")
        self.assertTrue(pf["watchlist"])

    def test_down_only_excluded_by_default(self):
        meta = {"sh1": {"name": "x", "sector": "a", "metrics": {"成交量比20": 2}}}
        tabs = {"trend-down": ["sh1"]}
        payload = assemble_shortlist(kline_meta=meta, tab_codes=tabs, limit=10)
        self.assertEqual(payload["cards"], [])

    def test_diversify_caps_sector(self):
        ranked = [
            {"code": f"c{i}", "sector": "同一板块", "score": 100 - i}
            for i in range(6)
        ] + [{"code": "other", "sector": "别的", "score": 50}]
        picked = diversify_pick(ranked, limit=5, max_per_sector=2)
        same = [c for c in picked if c["sector"] == "同一板块"]
        self.assertLessEqual(len(same), 2)
        self.assertTrue(any(c["code"] == "other" for c in picked))

    def test_score_rewards_volume_and_rs(self):
        low = score_candidate(
            tabs=["sideways"],
            metrics={"成交量比20": 0.5, "市场相对强弱20%": -5},
            sector="x",
            sector_chg=-2,
            sector_rank=20,
            in_watchlist=False,
        )
        high = score_candidate(
            tabs=["hammer", "sideways"],
            metrics={"成交量比20": 2.0, "市场相对强弱20%": 8},
            sector="x",
            sector_chg=2,
            sector_rank=2,
            in_watchlist=True,
        )
        self.assertGreater(high, low)


if __name__ == "__main__":
    unittest.main()
