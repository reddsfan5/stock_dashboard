import unittest

from scripts.reports.gen_index import DASHBOARD_URL, DAILY_OPS_URL, SERVICE_URLS


class IndexNavigationTest(unittest.TestCase):
    def test_interactive_cards_use_web_service_from_file_index(self):
        self.assertEqual(
            SERVICE_URLS["dashboard.html"],
            "http://127.0.0.1:8765/dashboard.html",
        )
        self.assertEqual(DASHBOARD_URL, SERVICE_URLS["dashboard.html"])
        self.assertEqual(DAILY_OPS_URL, "http://127.0.0.1:8765/daily_ops.html")


if __name__ == "__main__":
    unittest.main()
