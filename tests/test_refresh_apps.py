import unittest
from unittest.mock import patch

from scripts.reports.refresh_apps import _refresh_daily_ops, main


class RefreshAppsTest(unittest.TestCase):
    @patch("scripts.reports.gen_daily_ops.generate")
    def test_daily_refresh_rebuilds_sector_snapshot(self, generate):
        _refresh_daily_ops()

        generate.assert_called_once_with(skip_sector=False)

    @patch("scripts.reports.refresh_apps._refresh_daily_ops")
    @patch("scripts.reports.gen_reading.generate")
    @patch("scripts.reports.gen_sector_atlas.generate")
    @patch("scripts.reports.refresh_apps._run_write_app")
    def test_shortlist_history_failure_fails_daily_refresh(
        self, run_write_app, generate_sector_atlas, generate_reading, refresh_daily_ops
    ):
        refresh_daily_ops.side_effect = RuntimeError("历史库写入失败")

        result = main()

        self.assertEqual(result, 1)
        self.assertEqual(run_write_app.call_count, 7)
        self.assertIn(
            ("市场简报", "scripts.services.market_brief"),
            [call.args for call in run_write_app.call_args_list],
        )
        generate_sector_atlas.assert_called_once_with()
        generate_reading.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
