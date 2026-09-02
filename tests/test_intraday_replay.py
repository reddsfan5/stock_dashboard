import unittest

from scripts.services.intraday_replay import inject_intraday_replay
from scripts.services.minute_viewer import build_html


class IntradayReplayPageTest(unittest.TestCase):
    def test_shared_controller_replaces_both_template_markers(self):
        html = inject_intraday_replay(
            "<style>__INTRADAY_REPLAY_CSS__</style>"
            "<script>__INTRADAY_REPLAY_JS__</script>"
        )
        self.assertNotIn("__INTRADAY_REPLAY_", html)
        self.assertIn("createIntradayReplay", html)
        self.assertIn(".intraday-replay", html)

    def test_minute_page_has_fixed_axis_dynamic_replay_controls(self):
        points = []
        for index, close in enumerate((10.0, 10.1, 9.9)):
            points.append({
                "time": f"09:{31 + index:02d}",
                "open": close,
                "high": close + 0.01,
                "low": close - 0.01,
                "close": close,
                "vwap": close,
                "change_pct": (close / 10 - 1) * 100,
                "volume": 1_000,
                "amount": close * 1_000,
                "intraday_volume_ratio": 1.0,
                "direction": 1,
            })
        html = build_html({
            "code": "sh600000",
            "name": "测试股",
            "date": "2026-08-25",
            "dates": ["2026-08-25"],
            "prev_close": 10.0,
            "points": points,
            "summary": {},
        })

        self.assertIn('id="replayPlay"', html)
        self.assertIn("▶ 动态分时", html)
        self.assertIn("createIntradayReplay", html)
        self.assertIn("固定全天时间轴", html)
        self.assertIn("close:null", html)
        self.assertNotIn("__INTRADAY_REPLAY_", html)


if __name__ == "__main__":
    unittest.main()
