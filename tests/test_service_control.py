import unittest
from unittest.mock import patch

from scripts import serve


class ServiceControlTest(unittest.TestCase):
    def test_default_command_starts_all(self):
        args = serve.build_parser().parse_args([])
        self.assertEqual(args.action, "start")
        self.assertEqual(args.target, "all")
        self.assertEqual(serve.resolve_targets(args.target), ["web", "reports"])

    def test_page_targets_share_web_process(self):
        for target in ("minute", "grid", "trainer", "journal", "news", "interactive"):
            with self.subTest(target=target):
                self.assertEqual(serve.resolve_targets(target), ["web"])

    def test_health_requires_all_interactive_features(self):
        good = {
            "status": "ok",
            "service": "stock-interactive-web",
            "features": ["minute", "grid", "trainer", "journal", "news", "market_context"],
        }
        with patch.object(serve, "_http_json", return_value=(200, good)):
            self.assertTrue(serve.web_is_healthy())
        incomplete = dict(good, features=["minute", "grid", "trainer", "journal"])
        with patch.object(serve, "_http_json", return_value=(200, incomplete)):
            self.assertFalse(serve.web_is_healthy())

    def test_conflict_replacement_rejects_unrelated_process(self):
        with patch.object(serve, "_process_command", return_value="python unrelated.py"), patch.object(
            serve, "_process_cwd", return_value=str(serve.PROJECT_DIR)
        ):
            self.assertFalse(serve._is_safe_project_listener(123, "web"))

    def test_legacy_project_web_listener_is_replaceable(self):
        with patch.object(
            serve, "_process_command", return_value="python scripts/serve.py 8765"
        ), patch.object(serve, "_process_cwd", return_value=str(serve.OUTPUT_DIR)):
            self.assertTrue(serve._is_safe_project_listener(123, "web"))


if __name__ == "__main__":
    unittest.main()
