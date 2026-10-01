"""板块同步相关图的社区结构与桥梁板块（供点云按簇着色、标记桥梁、探索建议）。

输入为点云 payload 的 nodes / edges（同步边，已去市场 beta）。
- 社区：只用正相关边（权重 |ρ|），greedy modularity（networkx 自带，可复现、无随机种子）。
  小于 min_community_size 的社区并入「零散」(-1)。
- 簇名：成员一级行业众数（占比不足时取前两名），重名时追加簇核心名。
- 簇核心：簇内加权度最高的板块。
- 桥梁：在正相关图上，加权参与系数（跨簇连接占比）× 介数中心性（距离 = 1-ρ），取前 bridge_top_k 个跨簇板块。
  负相关边不算“桥”（它表示对冲而非传导），只保留在 wdeg 与页面连线里。
"""
from __future__ import annotations

from collections import Counter, defaultdict  # noqa: F401
from typing import Any

import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities, modularity

GRAPH_DEFAULTS: dict[str, Any] = {
    "min_community_size": 3,
    "resolution": 1.0,
    "bridge_top_k": 8,
    "bridge_min_participation": 0.25,
    "name_min_share": 0.45,
    "name_overrides": {},   # {簇核心板块: 显示名}；核心变化时自动回落到行业众数命名
    "palette": [
        "#5ad1ff", "#ffb347", "#c792ea", "#7ee787", "#ff7eb6", "#f2cc60",
        "#4dd0b5", "#8fa8ff", "#ff8a65", "#b5e853", "#e0a3ff", "#6fd3ff",
    ],
    "loose_color": "#6b778c",
}


def _communities(g: nx.Graph, resolution: float) -> list[set]:
    try:
        return [set(c) for c in greedy_modularity_communities(g, weight="weight", resolution=resolution)]
    except TypeError:  # 老版本 networkx 无 resolution 参数
        return [set(c) for c in greedy_modularity_communities(g, weight="weight")]


def analyze(nodes: list[dict], edges: list[dict], cfg: dict | None = None) -> dict:
    cfg = {**GRAPH_DEFAULTS, **(cfg or {})}
    ids = [n["id"] for n in nodes]
    l1 = {n["id"]: n.get("l1") or "其他" for n in nodes}
    pos = nx.Graph()
    pos.add_nodes_from(ids)
    full = nx.Graph()
    full.add_nodes_from(ids)
    for e in edges:
        a, b = e["source"], e["target"]
        if a not in l1 or b not in l1 or a == b:
            continue
        w = float(e.get("abs", abs(e.get("corr", 0.0))))
        full.add_edge(a, b, weight=w, dist=max(1e-3, 1.0 - w))
        if e.get("sign", 1) > 0:
            pos.add_edge(a, b, weight=w, dist=max(1e-3, 1.0 - w))

    comms = _communities(pos, float(cfg["resolution"])) if pos.number_of_edges() else [set(ids)]
    q = float(modularity(pos, comms, weight="weight")) if pos.number_of_edges() else 0.0
    comms = sorted(comms, key=lambda c: (-len(c), min(c)))
    keep = [c for c in comms if len(c) >= int(cfg["min_community_size"])]
    node_comm = {i: -1 for i in ids}
    for k, c in enumerate(keep):
        for i in c:
            node_comm[i] = k

    wdeg_in = {i: sum(d["weight"] for _, j, d in pos.edges(i, data=True) if node_comm[j] == node_comm[i]) for i in ids}
    wdeg = {i: sum(d["weight"] for _, _, d in full.edges(i, data=True)) for i in ids}

    palette = list(cfg["palette"])
    out_comms, used_names = [], Counter()
    for k, c in enumerate(keep):
        # 一级行业按簇内加权度加权计票（并列时按名称），避免集合顺序导致命名漂移
        votes = defaultdict(float)
        for i in c:
            votes[l1[i]] += 1.0 + wdeg_in[i]
        cnt = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
        top = cnt[0][0]
        share = sum(1 for i in c if l1[i] == top) / len(c)
        core = max(c, key=lambda i: (wdeg_in[i], wdeg[i], i))
        auto = top if share >= float(cfg["name_min_share"]) or len(cnt) == 1 else f"{top}/{cnt[1][0]}"
        name = (cfg.get("name_overrides") or {}).get(core) or auto
        used_names[name] += 1
        if used_names[name] > 1:
            name = f"{name}·{core}"
        out_comms.append({
            "id": k, "name": name, "size": len(c), "core": core,
            "auto_name": auto, "l1_top": top, "l1_share": round(share, 3),
            "members": sorted(c, key=lambda i: (-wdeg_in[i], i)),
            "color": palette[k % len(palette)],
        })

    # 桥梁：跨簇连接
    btw = nx.betweenness_centrality(pos, weight="dist", normalized=True) if pos.number_of_edges() else {i: 0.0 for i in ids}
    part, links = {}, {}
    for i in ids:
        by = defaultdict(float)
        for _, j, d in pos.edges(i, data=True):
            by[node_comm[j]] += d["weight"]
        tot = sum(by.values())
        part[i] = 1.0 - sum((v / tot) ** 2 for v in by.values()) if tot > 0 else 0.0
        links[i] = [k for k, _ in sorted(by.items(), key=lambda kv: -kv[1]) if k != node_comm[i] and k >= 0]
    cand = [i for i in ids if links[i] and part[i] >= float(cfg["bridge_min_participation"])]
    cand.sort(key=lambda i: -(btw[i] * (0.5 + part[i])))
    bridges = [{
        "id": i, "comm": node_comm[i], "links": links[i][:3],
        "betweenness": round(btw[i], 4), "participation": round(part[i], 3),
    } for i in cand[: int(cfg["bridge_top_k"])]]

    return {
        "method": "greedy_modularity(+edges, weight=|ρ|)",
        "modularity": round(q, 4),
        "communities": out_comms,
        "node_comm": node_comm,
        "loose_color": cfg["loose_color"],
        "bridges": bridges,
        "wdeg": {i: round(v, 3) for i, v in wdeg.items()},
        "betweenness": {i: round(v, 4) for i, v in btw.items()},
    }
