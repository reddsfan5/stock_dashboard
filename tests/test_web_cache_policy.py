import unittest

from scripts.services.minute_viewer import _cache_policy


class WebCachePolicyTest(unittest.TestCase):
    def test_market_and_write_apis_are_never_cached(self):
        self.assertEqual(_cache_policy("/api/minute/data?code=sh600519"), "no-store")
        self.assertEqual(_cache_policy("/api/trainer/state"), "no-store")

    def test_html_is_revalidated_and_assets_are_reused(self):
        self.assertEqual(_cache_policy("/dashboard.html"), "no-cache")
        self.assertIn("max-age=300", _cache_policy("/assets/workbench.js"))
        self.assertIn("immutable", _cache_policy("/vendor/jquery.min.js"))


if __name__ == "__main__":
    unittest.main()
