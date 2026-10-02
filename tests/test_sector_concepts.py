import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.reports import sector_concepts as sc
from scripts.reports.gen_sector_corr_cloud import render_html

CFG = {
    "enabled": True, "db": "state/c.sqlite3", "output": "output/sector_concepts.json", "min_members": 2, "max_members": 0, "return_days": 3,
    "sources": [
        {"key": "ths", "label": "同花顺", "source": "ths", "kind": "同花顺题材"},
        {"key": "em", "label": "东财", "source": "eastmoney", "kind": "数据商板块", "exclude_suffix": ["板块"],
         "exclude_shenwan_names": True, "exclude": ["融资融券"], "exclude_regex": ["^昨日"]},
    ],
}


def _db(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE board (source TEXT, board_id TEXT, name TEXT, kind TEXT);
    CREATE TABLE membership (code TEXT, source TEXT, board_id TEXT, observed_from TEXT, observed_to TEXT, evidence TEXT, source_url TEXT);
    """)
    boards = [("ths", "1", "培育钻石", "同花顺题材"), ("ths", "2", "孤品", "同花顺题材"), ("eastmoney", "10", "培育钻石", "数据商板块"),
              ("eastmoney", "11", "广东板块", "数据商板块"), ("eastmoney", "12", "通用设备", "数据商板块"), ("eastmoney", "13", "融资融券", "数据商板块"),
              ("eastmoney", "14", "昨日涨停", "数据商板块"), ("shenwan", "sw2:通用设备", "通用设备", "申万2级")]
    db.executemany("INSERT INTO board VALUES (?,?,?,?)", boards)
    mem = [("sz000001", "ths", "1", None), ("sh600002", "ths", "1", None), ("sz300003", "ths", "1", None), ("sz000004", "ths", "1", "2026-01-01"),
           ("sz000001", "ths", "2", None),
           ("sz000001", "eastmoney", "10", None), ("sh600002", "eastmoney", "10", None)]
    for bid in ("11", "12", "13", "14"):
        mem += [("sz000001", "eastmoney", bid, None), ("sh600002", "eastmoney", bid, None)]
    db.executemany("INSERT INTO membership VALUES (?,?,?,'2025-01-01',?,'','')", [(c, s, b, o) for c, s, b, o in mem])
    db.commit(); db.close()


def _kline(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    days = pd.to_datetime(["2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30"])
    rows = []
    for code, rets in (("000001", [0.0, 0.10, 0.10, -0.10]), ("600002", [0.0, 0.01, 0.01, 0.02])):
        px = 10.0
        for d, r in zip(days, rets):
            prev, px = px, px * (1 + r)
            rows.append({"代码": code, "日期": d, "收盘": px, "前收": prev})
    pd.DataFrame(rows).to_parquet(path)


class SectorConceptsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        _db(self.root / "state" / "c.sqlite3"); _kline(self.root / "cache" / "k.parquet")

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_filters_and_threshold(self):
        got = sc.read_concepts(self.root / "state" / "c.sqlite3", CFG)
        self.assertEqual(got[("ths", "培育钻石")], ["000001", "300003", "600002"])  # 已退出成分（observed_to）不算
        self.assertIn(("em", "培育钻石"), got)
        self.assertNotIn(("ths", "孤品"), got)          # 少于 min_members
        for name in ("广东板块", "通用设备", "融资融券", "昨日涨停"):   # 地域 / 申万同名行业 / 市场属性
            self.assertNotIn(("em", name), got)

    def test_returns_last_and_cumulative(self):
        as_of, rets = sc.stock_returns(self.root / "cache" / "k.parquet", days=3)
        self.assertEqual(as_of, "2026-09-30")
        self.assertAlmostEqual(rets["000001"][0], -10.0, places=2)
        self.assertAlmostEqual(rets["000001"][1], (1.1 * 1.1 * 0.9 - 1) * 100, places=1)
        self.assertAlmostEqual(rets["600002"][1], (1.01 * 1.01 * 1.02 - 1) * 100, places=1)

    def test_attach_writes_lazy_json_and_small_index(self):
        payload = {"nodes": [{"id": "通用设备", "n": 10}, {"id": "银行", "n": 5}]}
        stock_map = {"000001": ("平安银行", "银行"), "600002": ("某设备", "通用设备"), "300003": ("点云外", "不存在的二级")}
        sc.attach_concepts(payload, stock_map=stock_map, cfg=CFG, project_dir=self.root, kline=self.root / "cache" / "k.parquet")
        emb = payload["concepts"]
        self.assertEqual(emb["file"], "sector_concepts.json")
        self.assertEqual(emb["sources"], {"ths": "同花顺", "em": "东财"})
        self.assertEqual(emb["list"][0], ["ths", "培育钻石", 3, 2, 2])   # [来源, 名称, 成分, 点云内, 二级数]
        self.assertNotIn("stocks", emb)                                    # 成分明细不进页面
        lazy = json.loads((self.root / "output" / "sector_concepts.json").read_text(encoding="utf-8"))
        rows = [lazy["stocks"][i] for i in lazy["concepts"]["ths:培育钻石"]]
        by = {r[0]: r for r in rows}
        self.assertEqual(by["000001"][1:3], ["平安银行", "银行"])
        self.assertEqual(by["300003"][2], "")                              # 二级不在点云 → 空
        self.assertAlmostEqual(by["000001"][3], -10.0, places=2)
        self.assertEqual(lazy["as_of"], "2026-09-30")

    def test_disabled_or_missing_db_is_harmless(self):
        payload = {"nodes": [], "concepts": {"stale": 1}}
        sc.attach_concepts(payload, stock_map={}, cfg={**CFG, "enabled": False}, project_dir=self.root)
        self.assertNotIn("concepts", payload)
        sc.attach_concepts(payload, stock_map={}, cfg={**CFG, "db": "state/none.sqlite3"}, project_dir=self.root)
        self.assertNotIn("concepts", payload)

    def test_repo_config_and_page_hooks(self):
        cfg = sc.load_concept_config()
        self.assertEqual([s["key"] for s in cfg["sources"]][:2], ["ths", "em"])
        self.assertGreaterEqual(int(cfg["min_members"]), 1)
        html = render_html({"start": "2022-01-01", "end": "2026-09-30", "n_sectors": 0, "nodes": [], "edges": [],
                            "concepts": {"file": "sector_concepts.json", "sources": {"ths": "同花顺"}, "min_members": 3, "list": [["ths", "培育钻石", 19, 19, 12]]}})
        for needle in ("function selectConcept(", "function conceptMatches(", 'data-v="concept"', "INFO.concept", "conceptQuery(qt, 'exact')",
                       "conceptQuery(qt, 'fuzzy')", "function renderConceptPanel(", "else if (conceptSel) focusIds", "fetch(CONCEPTS.file"):
            self.assertIn(needle, html, needle)
        # 题材分支排在二级同名 / 一级全名之后
        self.assertLess(html.index("SW1_MEMBERS[qt] && !exactMatches(qt).length"), html.index("conceptQuery(qt, 'exact')"))


if __name__ == "__main__":
    unittest.main()
