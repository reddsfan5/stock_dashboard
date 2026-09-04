"""标的上下文聚合与选股命中解析测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from data.hypotheses import HypothesisRepository
from data.journal import JournalRepository
from data.training_sessions import TrainingSessionRepository
from data.watchlist import WatchlistRepository
from scripts.services.symbol_context import (
    SymbolContextService,
    load_screen_hits,
)


class LoadScreenHitsTest(unittest.TestCase):
    def test_parse_tab_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboard.html"
            path.write_text(
                'var TAB_CODES={"continuity": ["sh600519", "sz000001"], "hammer": ["sz000001"]};',
                encoding="utf-8",
            )
            hits = load_screen_hits("sh600519", dashboard_path=path)
            self.assertEqual([m["module"] for m in hits["modules"]], ["continuity"])
            empty = load_screen_hits("sh999999", dashboard_path=path)
            self.assertEqual(empty["modules"], [])


class SymbolContextServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.watch = WatchlistRepository(root / "watchlist.sqlite3")
        self.train = TrainingSessionRepository(root / "training.sqlite3")
        self.journal = JournalRepository(root / "journal.sqlite3")
        self.hypo = HypothesisRepository(root / "hypotheses.sqlite3")
        self.svc = SymbolContextService(
            name_map={"sh600519": "贵州茅台"},
            watchlist=self.watch,
            training=self.train,
            journal=self.journal,
            hypotheses=self.hypo,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_context_aggregates_stores(self):
        self.watch.add(code="sh600519", name="贵州茅台", thesis="观察", screen_date="2026-09-01")
        run = self.train.create_run(
            session_token="tok-1",
            code="sh600519",
            name="贵州茅台",
            start_date="2026-08-01",
            capital=100000,
        )
        self.train.add_decision(
            run_id=run["id"],
            event_type="buy",
            market_date="2026-08-01",
            as_of="10:00:00",
            side="buy",
            shares=100,
            price=1800,
            reason="试仓",
        )
        case = self.journal.create_case(code="sh600519", title="茅台案例", thesis="假设")
        self.hypo.create(code="sh600519", title="缓涨", status="hypothesis")

        with mock.patch(
            "scripts.services.symbol_context.load_screen_hits",
            return_value={"code": "sh600519", "modules": [{"module": "continuity", "title": "K线连续性"}], "dashboard_mtime": None, "message": None},
        ), mock.patch(
            "scripts.services.symbol_context.load_screen_to_trade_note",
            return_value={"available": False, "message": "暂无", "recent_trades": [], "trade_count": 0},
        ):
            ctx = self.svc.context("sh600519")

        self.assertEqual(ctx["name"], "贵州茅台")
        self.assertEqual(ctx["watchlist"]["count"], 1)
        self.assertGreaterEqual(ctx["training"]["decision_count"], 1)
        self.assertEqual(ctx["journal"]["count"], 1)
        self.assertEqual(ctx["hypotheses"]["count"], 1)
        self.assertEqual(ctx["screen_hits"]["modules"][0]["module"], "continuity")
        self.assertIn("/symbol.html?code=sh600519", ctx["links"]["symbol"])

    def test_hypothesis_api_helpers(self):
        created = self.svc.create_hypothesis(
            {"code": "sz000001", "title": "测试", "thesis": "论点", "status": "hypothesis"}
        )
        updated = self.svc.update_hypothesis({"id": created["id"], "status": "training"})
        self.assertEqual(updated["status"], "training")


class DailyOpsGeneratorTest(unittest.TestCase):
    def test_generate_writes_html(self):
        from scripts.reports import gen_daily_ops

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            status = root / "daily_update_status.json"
            status.write_text(
                json.dumps({
                    "state": "success",
                    "ok": True,
                    "target_date": "2026-09-04",
                    "updated_at": "2026-09-04T18:36:52",
                    "stages": [{"name": "stocks", "ok": True, "duration_seconds": 1, "message": "ok"}],
                }),
                encoding="utf-8",
            )
            out = root / "daily_ops.html"
            # monkeypatch paths
            old_status, old_out, old_sector, old_cache = (
                gen_daily_ops.STATUS_PATH,
                gen_daily_ops.OUT_HTML,
                gen_daily_ops.SECTOR_SNAPSHOT,
                gen_daily_ops.CACHE_DIR,
            )
            gen_daily_ops.STATUS_PATH = status
            gen_daily_ops.OUT_HTML = out
            gen_daily_ops.SECTOR_SNAPSHOT = root / "sector.json"
            gen_daily_ops.CACHE_DIR = root
            try:
                gen_daily_ops.generate(skip_sector=True)
            finally:
                gen_daily_ops.STATUS_PATH = old_status
                gen_daily_ops.OUT_HTML = old_out
                gen_daily_ops.SECTOR_SNAPSHOT = old_sector
                gen_daily_ops.CACHE_DIR = old_cache
            html = out.read_text(encoding="utf-8")
            self.assertIn("每日操盘清单", html)
            self.assertIn("stocks", html)
            self.assertIn("选股仪表盘", html)


if __name__ == "__main__":
    unittest.main()
