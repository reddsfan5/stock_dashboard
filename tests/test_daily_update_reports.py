import subprocess
import unittest
from unittest.mock import patch

from pipeline.daily_update import DailyUpdatePipeline


class DailyUpdateReportsTest(unittest.TestCase):
    @patch("pipeline.daily_update.subprocess.run")
    def test_reports_stage_rebuilds_market_and_screening_pages(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout="行情完成\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="选股完成\n", stderr=""),
        ]

        ok, message, details = DailyUpdatePipeline._reports_stage()

        self.assertTrue(ok)
        self.assertIn("选股网页已刷新", message)
        self.assertEqual(run.call_count, 2)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-2:], ["-m", "scripts.reports.gen_market"])
        self.assertEqual(commands[1][-2:], ["-m", "scripts.screen"])
        self.assertEqual(details["选股仪表盘"]["returncode"], 0)

    @patch("pipeline.daily_update.subprocess.run")
    def test_reports_stage_stops_and_reports_failing_job(self, run):
        run.return_value = subprocess.CompletedProcess(
            [], 2, stdout="", stderr="生成失败"
        )

        ok, message, details = DailyUpdatePipeline._reports_stage()

        self.assertFalse(ok)
        self.assertIn("行情与板块报告刷新失败", message)
        self.assertIn("生成失败", message)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(details["行情与板块报告"]["returncode"], 2)


if __name__ == "__main__":
    unittest.main()
