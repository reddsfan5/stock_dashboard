import unittest

from scripts.services.minute_viewer import build_grid_payload


class FakeMinuteRepository:
    def payload(self, code, date):
        return {
            "code": "sh518880",
            "name": "黄金ETF",
            "date": "2026-08-28",
            "dates": ["2026-08-28"],
            "prev_close": 8.765,
            "points": [
                {
                    "time": "09:31",
                    "open": 8.8,
                    "high": 8.8,
                    "low": 8.8,
                    "close": 8.8,
                }
            ],
        }


class MinuteGridPayloadTest(unittest.TestCase):
    def test_grid_payload_preserves_previous_close_for_chart_percentage(self):
        result = build_grid_payload(FakeMinuteRepository(), {})

        self.assertEqual(result["prev_close"], 8.765)
        self.assertEqual(result["code"], "sh518880")


if __name__ == "__main__":
    unittest.main()
