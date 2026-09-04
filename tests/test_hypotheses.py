"""假设生命周期仓储单元测试。"""

import tempfile
import unittest
from pathlib import Path

from data.hypotheses import HypothesisRepository, STATUSES


class HypothesisRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = HypothesisRepository(Path(self.temp.name) / "hypotheses.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_create_and_advance_status(self):
        item = self.repo.create(
            code="sh600519",
            title="茅台缓涨",
            thesis="低位缓涨可训练",
            status="hypothesis",
        )
        self.assertEqual(item["status"], "hypothesis")
        self.assertEqual(item["status_label"], "假设")
        for status in ("backtested", "training", "noted"):
            item = self.repo.set_status(item["id"], status)
            self.assertEqual(item["status"], status)
        rows = self.repo.list_for_code("sh600519")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "noted")

    def test_rejects_bad_status_and_code(self):
        with self.assertRaises(ValueError):
            self.repo.create(code="600519", title="x")
        item = self.repo.create(code="sz000001", title="一")
        with self.assertRaises(ValueError):
            self.repo.set_status(item["id"], "done")

    def test_soft_delete_hides_row(self):
        item = self.repo.create(code="sh600000", title="浦发")
        self.repo.soft_delete(item["id"])
        self.assertEqual(self.repo.list_for_code("sh600000"), [])


if __name__ == "__main__":
    unittest.main()
