import unittest

import numpy as np
import pandas as pd

from scripts.reports import sector_lead_stats as sls
from scripts.reports.gen_sector_corr_cloud import attach_graph, render_html
from scripts.reports.sector_signals import SIGNAL_DEFAULTS, build_context, compute_signals, day_candidates

IDS = [f"{g}{i}" for g in "ABC" for i in range(5)]


def _graph():
    comms = [{"id": k, "name": f"簇{g}", "members": [f"{g}{i}" for i in range(5)], "core": f"{g}0"} for k, g in enumerate("ABC")]
    return {"communities": comms, "bridges": [{"id": "B4", "comm": 1, "links": [2]}]}


def _edges():
    e = []
    for g in "ABC":
        for i in range(5):
            for j in range(i + 1, 5):
                e.append({"source": f"{g}{i}", "target": f"{g}{j}", "corr": 0.9 if (g, i, j) == ("C", 0, 1) else 0.5, "sign": 1})
    e += [{"source": "B4", "target": f"C{i}", "corr": 0.55, "sign": 1} for i in range(3)]
    return e


def _frame(n=100, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2026-03-02", periods=n)
    return pd.DataFrame(rng.normal(0, 0.3, size=(n, len(IDS))), index=idx, columns=IDS)


def _planted():
    df = _frame()
    T = len(df) - 1
    df.loc[df.index[T], [f"A{i}" for i in range(1, 5)]] = -1.5          # 逆簇：A0 独涨
    df.loc[df.index[T], "A0"] = 2.5
    df.loc[df.index[T - 3:T + 1], "B0"] = [0.6, 1.0, 2.0, 3.0]           # 持续：连涨且放大
    df.loc[df.index[T - 4:T + 1], "C0"] = 2.0                            # 脱钩：C0 与 C1（ρ0.9）
    df.loc[df.index[T - 4:T + 1], "C1"] = -1.0
    df.loc[df.index[T], ["C2", "C3", "C4"]] = [5.0, -5.0, 0.0]           # 分化：簇 C 中位≈0
    df.loc[df.index[T], "B4"] = 4.0                                      # 桥梁当日先动
    return df


class SectorSignalsTest(unittest.TestCase):
    def test_each_type_detected_on_planted_day(self):
        df = _planted()
        cfg = SIGNAL_DEFAULTS
        ctx = build_context(IDS, _graph(), _edges(), cfg)
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        c = day_candidates(df.to_numpy(), dates, ctx, cfg)
        by = {}
        for o in c:
            by.setdefault(o["t"], []).append(o)
        self.assertIn("A0", [o["id"] for o in by["contra"]])
        self.assertIn("B0", [o["id"] for o in by["streak"]])
        self.assertTrue(any({o["id"], *o["p"]} == {"C0", "C1"} for o in by["decouple"]))
        self.assertIn("近5日背离", next(o for o in by["decouple"] if "C1" in {o["id"], *o["p"]})["x"])
        self.assertEqual(by["disperse"][0]["id"], "簇C")
        self.assertEqual(by["disperse"][0]["p"], ["C2", "C3"])
        br = next(o for o in by["bridge"] if o["id"] == "B4")
        self.assertIn("观察中", br["x"])  # 当日事件不能用未来数据判断是否跟随
        self.assertIn("簇B↔簇C", br["x"])

    def test_selection_caps_dedup_and_nth_day(self):
        df = _planted()
        out = [d.strftime("%Y-%m-%d") for d in df.index[-10:]]
        s = compute_signals(df, _graph(), _edges(), out)
        caps = SIGNAL_DEFAULTS["type_caps"]
        for day in s["days"]:
            self.assertLessEqual(len(day), SIGNAL_DEFAULTS["max_items"])
            ids = [x for o in day if o["t"] != "disperse" for x in (o["id"], *o["p"])]
            self.assertEqual(len(ids), len(set(ids)))
            for t, cap in caps.items():
                self.assertLessEqual(sum(o["t"] == t for o in day), cap)
            for o in day:
                self.assertGreaterEqual(o["n"], 1)
        # 脱钩连续多日出现时累计「第N天」
        ns = [o["n"] for day in s["days"] for o in day if o["t"] == "decouple" and {o["id"], *o["p"]} == {"C0", "C1"}]
        self.assertTrue(ns and max(ns) >= 2, ns)

    def test_no_look_ahead(self):
        df = _planted()
        out = [d.strftime("%Y-%m-%d") for d in df.index[-20:]]
        full = compute_signals(df, _graph(), _edges(), out)
        rng = np.random.default_rng(7)
        for cut in (5, 12, 19):
            # 1) 截断：只给到第 cut 天的数据
            trunc = compute_signals(df.iloc[:len(df) - 20 + cut + 1], _graph(), _edges(), out[:cut + 1])
            self.assertEqual(trunc["days"], full["days"][:cut + 1])
            # 2) 篡改未来：第 cut 天之后的数据换成极端随机数，之前各天的信号必须完全不变
            bad = df.copy()
            k0 = len(df) - 20 + cut + 1
            if k0 < len(df):
                bad.iloc[k0:] = rng.normal(0, 8, size=bad.iloc[k0:].shape)
                pert = compute_signals(bad, _graph(), _edges(), out)
                self.assertEqual(pert["days"][:cut + 1], full["days"][:cut + 1])

    def test_page_panel_and_config(self):
        nodes = [{"id": s, "l1": "电子", "last": 0.5} for s in ("甲", "乙", "丙")]
        payload = attach_graph({"nodes": nodes, "edges": [{"source": "甲", "target": "乙", "corr": 0.8, "abs": 0.8, "sign": 1}], "n_sectors": 3})
        payload["signals"] = {"dates": ["2026-09-30"], "days": [[{"t": "streak", "id": "甲", "p": [], "c": None, "d": 1, "s": 2.0, "x": "连涨 3 天", "n": 2}]],
                              "labels": {"streak": "持续"}}
        html = render_html({"start": "2022-01-01", "end": "2026-09-30", **payload})
        for needle in ('id="sigPanel"', 'id="sgList"', 'id="sgPeek"', "function renderSignals(", "function openSignal(",
                       "不是预测", 'class="lc-bar"', '"signals"'):
            self.assertIn(needle, html, needle)
        cfg = sls.load_config()
        sg = cfg["signals"]
        for key in ("max_items", "min_items", "type_caps", "contra", "decouple", "streak", "bridge", "disperse", "history_days"):
            self.assertIn(key, sg)
        self.assertLessEqual(sg["min_items"], sg["max_items"])
        self.assertEqual(set(sg["type_caps"]), {"contra", "decouple", "streak", "bridge", "disperse"})
        self.assertIn("signals_open_mobile", cfg["display"])


if __name__ == "__main__":
    unittest.main()
