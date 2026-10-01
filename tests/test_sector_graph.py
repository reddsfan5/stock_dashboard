import unittest

from scripts.reports import sector_lead_stats as sls
from scripts.reports.gen_sector_corr_cloud import attach_graph, render_html
from scripts.reports.sector_graph import analyze


def _toy():
    nodes = [{"id": f"a{i}", "l1": "电子"} for i in range(4)] + [{"id": f"b{i}", "l1": "银行"} for i in range(4)]
    nodes.append({"id": "c", "l1": "电子"})
    edges = []
    for grp in ("a", "b"):
        for i in range(4):
            for j in range(i + 1, 4):
                w = 0.95 if (i == 0 or j == 0) else 0.7  # x0 为簇内加权度最高的核心
                edges.append({"source": f"{grp}{i}", "target": f"{grp}{j}", "corr": w, "abs": w, "sign": 1})
    edges += [
        {"source": "c", "target": "a1", "corr": 0.45, "abs": 0.45, "sign": 1},
        {"source": "c", "target": "b1", "corr": 0.45, "abs": 0.45, "sign": 1},
        {"source": "a3", "target": "b3", "corr": -0.6, "abs": 0.6, "sign": -1},
    ]
    return nodes, edges


class SectorGraphTest(unittest.TestCase):
    def test_two_planted_communities_with_cores_and_names(self):
        g = analyze(*_toy())
        names = sorted(c["name"] for c in g["communities"])
        self.assertEqual(names, ["电子", "银行"])
        cores = {c["name"]: c["core"] for c in g["communities"]}
        self.assertEqual(cores["电子"], "a0")
        self.assertEqual(cores["银行"], "b0")
        self.assertGreater(g["modularity"], 0.3)
        # 负相关边不参与社区划分：a3 与 b3 仍分属两簇
        self.assertNotEqual(g["node_comm"]["a3"], g["node_comm"]["b3"])

    def test_bridge_connects_both_communities(self):
        g = analyze(*_toy(), {"bridge_top_k": 3})
        ids = [b["id"] for b in g["bridges"]]
        self.assertEqual(ids[0], "c")
        self.assertEqual(len(g["bridges"][0]["links"]), 1)
        self.assertGreater(g["betweenness"]["c"], g["betweenness"]["a2"])

    def test_name_override_by_core_and_small_groups_are_loose(self):
        nodes, edges = _toy()
        nodes.append({"id": "z", "l1": "其他"})
        g = analyze(nodes, edges, {"name_overrides": {"a0": "科技链"}})
        self.assertIn("科技链", [c["name"] for c in g["communities"]])
        self.assertEqual(g["node_comm"]["z"], -1)

    def test_attach_graph_and_page_controls(self):
        nodes, edges = _toy()
        payload = attach_graph({"nodes": nodes, "edges": edges, "n_sectors": len(nodes)})
        self.assertEqual(len(payload["graph"]["communities"]), 2)
        html = render_html({"start": "2022-01-01", "end": "2026-08-14", **payload})
        for needle in ('id="trailBar"', 'id="trBack"', 'id="trFwd"', 'id="colormode"', 'id="tourBtn"', 'id="lgComm"',
                       "prefers-reduced-motion", "function startTransition(", "function flyTo(", "function stepPulses(",
                       "function explainStep(", "function renderSuggest(", "function buildTour(",
                       "controls.addEventListener('end', scheduleResume)", "THREE.AdditiveBlending", '"node_comm"'):
            self.assertIn(needle, html, needle)

    def test_config_defaults(self):
        cfg = sls.load_config()
        d, g = cfg["display"], cfg["graph"]
        self.assertEqual(d["color_by"], "community")
        for key in ("rotate_resume_ms", "focus_orbit_speed", "transition_ms", "reduced_motion_ms",
                    "web_opacity_min", "web_opacity_max", "pulse_max", "trail_max", "tour_step_ms"):
            self.assertIn(key, d)
        self.assertLess(d["reduced_motion_ms"], d["transition_ms"])
        self.assertLess(d["web_opacity_min"], d["web_opacity_max"])
        for key in ("min_community_size", "bridge_top_k", "name_overrides"):
            self.assertIn(key, g)


if __name__ == "__main__":
    unittest.main()
