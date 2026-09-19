import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data.shortlist import ShortlistRepository
from scripts.reports.gen_shortlist import main as generate_shortlist
from scripts.services.shortlist_history import render_saved_shortlist


def payload(market_date: str, codes: list[str]) -> dict:
    return {
        "market_date": market_date,
        "generated_at": f"{market_date}T18:00:00+08:00",
        "candidate_count": len(codes) + 5,
        "limit": 15,
        "notes": ["测试快照"],
        "cards": [
            {
                "rank": index,
                "code": code,
                "name": f"标的{index}",
                "sector": "测试板块",
                "score": 10 - index,
                "why": ["测试理由"],
                "risks": ["测试风险"],
            }
            for index, code in enumerate(codes, 1)
        ],
    }


class ShortlistHistoryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repository = ShortlistRepository(
            Path(self.tempdir.name) / "shortlist.sqlite3"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_saves_and_reads_exact_daily_snapshot(self):
        saved = self.repository.save(
            payload("2026-09-16", ["sh600000", "sz000001"])
        )
        self.assertEqual(saved["selected_count"], 2)

        result = self.repository.get("2026-09-16")
        self.assertEqual(result["market_date"], "2026-09-16")
        self.assertEqual(result["candidate_count"], 7)
        self.assertEqual(
            [card["code"] for card in result["cards"]],
            ["sh600000", "sz000001"],
        )
        self.assertEqual(result["cards"][0]["why"], ["测试理由"])

    def test_same_date_replaces_items_and_latest_is_newest_date(self):
        self.repository.save(payload("2026-09-16", ["sh600000", "sz000001"]))
        self.repository.save(payload("2026-09-17", ["sh600519"]))
        self.repository.save(payload("2026-09-16", ["sz002594"]))

        replaced = self.repository.get("2026-09-16")
        self.assertEqual([card["code"] for card in replaced["cards"]], ["sz002594"])
        self.assertEqual(self.repository.get()["market_date"], "2026-09-17")
        self.assertEqual(
            [item["market_date"] for item in self.repository.dates()],
            ["2026-09-17", "2026-09-16"],
        )

    def test_rejects_bad_dates_and_missing_days(self):
        with self.assertRaises(ValueError):
            self.repository.save(payload("2026/09/17", ["sh600000"]))
        with self.assertRaises(LookupError):
            self.repository.get("2026-09-15")

    def test_saved_history_renders_exact_requested_day(self):
        self.repository.save(payload("2026-09-16", ["sh600000"]))
        self.repository.save(payload("2026-09-17", ["sh600519"]))

        html = render_saved_shortlist("2026-09-16", db_path=self.repository.path)

        self.assertIn("2026-09-16 候选", html)
        self.assertIn("sh600000", html)
        self.assertNotIn("sh600519", html)
        self.assertIn("正在查看历史快照", html)

    def test_generator_persists_daily_snapshot(self):
        db_path = Path(self.tempdir.name) / "generated.sqlite3"
        json_path = Path(self.tempdir.name) / "shortlist.json"
        html_path = Path(self.tempdir.name) / "shortlist.html"
        generated = payload("2026-09-18", ["sh600000", "sz000001"])
        with patch("scripts.reports.gen_shortlist.build_from_paths", return_value=generated):
            code = generate_shortlist([
                "--skip-sector-refresh",
                "--history-db", str(db_path),
                "--json-out", str(json_path),
                "--html-out", str(html_path),
            ])

        self.assertEqual(code, 0)
        stored = ShortlistRepository(db_path).get("2026-09-18")
        self.assertEqual([card["code"] for card in stored["cards"]], ["sh600000", "sz000001"])
        self.assertIn("历史候选", html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
