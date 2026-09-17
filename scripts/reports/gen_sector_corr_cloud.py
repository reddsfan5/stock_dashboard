#!/usr/bin/env python3
"""申万二级板块相关点云：2022→今残差相关 + MDS 三维 + Three.js 可查询页。

用法:
  python -m scripts.reports.gen_sector_corr_cloud
  python -m scripts.reports.gen_sector_corr_cloud --no-nav
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_DIR))

from data.industry import StockInfo

START = "2022-01-01"
MIN_NAMES_PER_DAY = 5
CORR_THR = 0.42
MAX_EDGES_PER_NODE = 8
MIN_YEAR_CONFIRM = 2
OUTPUT_HTML = PROJECT_DIR / "output" / "sector_corr_cloud.html"
OUTPUT_JSON = PROJECT_DIR / "cache" / "sector_corr_cloud.json"
MOM1 = PROJECT_DIR / "cache" / "indicators_mom1.parquet"

QUERY_EXPAND: dict[str, dict] = {
    "电力": {"exact": ["电力"], "contains": ["电力", "电网", "电源", "火电", "水电", "绿电", "储能", "风电", "光伏"]},
    "电": {"exact": ["电力"], "contains": ["电力", "电网", "电源"]},
    "锂电": {"exact": [], "contains": ["电池", "锂", "储能", "光伏", "电机"]},
    "半导体": {"exact": [], "contains": ["半导体", "芯片", "元件", "集成电路", "电子"]},
    "白酒": {"exact": ["白酒Ⅱ"], "contains": ["白酒", "啤酒", "饮料"]},
    "银行": {"exact": ["银行Ⅱ"], "contains": ["银行"]},
    "地产": {"exact": [], "contains": ["地产", "房地产", "物业", "装修"]},
    "军工": {"exact": [], "contains": ["军工", "航空装备", "航天装备", "地面兵装", "航海装备"]},
    "医药": {"exact": [], "contains": ["医药", "中药", "生物制品", "医疗", "器械", "药店"]},
    "新能源": {"exact": [], "contains": ["光伏", "风电", "电池", "储能", "电机", "电网", "电力"]},
}


def classical_mds(dist: np.ndarray, n_components: int = 3) -> np.ndarray:
    n = dist.shape[0]
    d2 = dist ** 2
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ d2 @ j
    eigvals, eigvecs = np.linalg.eigh(b)
    idx = np.argsort(eigvals)[::-1][:n_components]
    eigvals = np.clip(eigvals[idx], 0, None)
    return eigvecs[:, idx] * np.sqrt(eigvals)


def load_sw2_returns() -> tuple[pd.DataFrame, pd.DataFrame]:
    mom = pd.read_parquet(MOM1)
    mom.index = pd.to_datetime(mom.index)
    mom = mom.loc[mom.index >= START]

    info = StockInfo().df.copy()
    info = info.dropna(subset=["申万2级"])
    info = info[info["申万2级"].astype(str).str.len() > 0]
    info = info[~info["申万2级"].isin(["未分类", "未分类Ⅱ", ""])]
    code_to_sw2 = dict(zip(info["代码"], info["申万2级"]))
    cols = [c for c in mom.columns if c in code_to_sw2]
    mom = mom[cols]

    long = mom.stack(future_stack=True).rename("ret").reset_index()
    long.columns = ["日期", "代码", "ret"]
    long["申万2级"] = long["代码"].map(code_to_sw2)
    long = long.dropna(subset=["申万2级", "ret"])
    g = long.groupby(["日期", "申万2级"], sort=False)["ret"]
    agg = g.agg(["mean", "count"]).reset_index()
    agg.loc[agg["count"] < MIN_NAMES_PER_DAY, "mean"] = np.nan
    piv = agg.pivot(index="日期", columns="申万2级", values="mean").sort_index()
    ok = piv.notna().sum() >= 200
    piv = piv.loc[:, ok]

    meta = (
        info[info["申万2级"].isin(piv.columns)]
        .groupby("申万2级", as_index=False)
        .agg(申万1级=("申万1级", "first"), 成分股数=("代码", "count"))
        .rename(columns={"申万2级": "id", "申万1级": "l1", "成分股数": "n"})
    )
    return piv, meta


def market_proxy(piv: pd.DataFrame) -> pd.Series:
    try:
        from data.index import IndexData
        idx = IndexData()
        for code in ("sh000300", "sh000001"):
            try:
                k = idx.get_kline(code) if hasattr(idx, "get_kline") else None
            except Exception:
                k = None
            if k is None or len(k) == 0:
                continue
            k = k.copy()
            k["日期"] = pd.to_datetime(k["日期"])
            r = k.sort_values("日期").set_index("日期")["收盘"].pct_change() * 100
            r = r.reindex(piv.index)
            if r.notna().sum() > 200:
                return r
    except Exception:
        pass
    return piv.mean(axis=1)


def residualize(piv: pd.DataFrame, mkt: pd.Series) -> pd.DataFrame:
    out = pd.DataFrame(index=piv.index, columns=piv.columns, dtype=float)
    x = mkt.values.astype(float)
    for col in piv.columns:
        y = piv[col].values.astype(float)
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 100:
            out[col] = np.nan
            continue
        xm, ym = x[mask], y[mask]
        var = np.dot(xm - xm.mean(), xm - xm.mean())
        if var < 1e-12:
            out[col] = y - np.nanmean(y)
            continue
        beta = np.dot(xm - xm.mean(), ym - ym.mean()) / var
        alpha = ym.mean() - beta * xm.mean()
        out[col] = y - (alpha + beta * x)
    return out


def year_confirm(resid: pd.DataFrame, a: str, b: str, thr: float) -> int:
    sub_all = resid[[a, b]].dropna()
    if len(sub_all) < 120:
        return 0
    full = sub_all[a].corr(sub_all[b])
    if not np.isfinite(full):
        return 0
    ok = 0
    for y, sub in resid[[a, b]].groupby(resid.index.year):
        sub = sub.dropna()
        if len(sub) < 60:
            continue
        c = sub[a].corr(sub[b])
        if np.isfinite(c) and abs(c) >= thr * 0.85 and np.sign(c) == np.sign(full):
            ok += 1
    return ok



def top_neighbors(corr: pd.DataFrame, k: int = 6) -> dict[str, list]:
    """每点按|ρ|保留 Top-K 邻居，保证查询聚焦时孤立点也能展开。"""
    names = list(corr.columns)
    out: dict[str, list] = {n: [] for n in names}
    vals = corr.values
    for i, a in enumerate(names):
        row = vals[i]
        order = np.argsort(-np.abs(np.nan_to_num(row, nan=0.0)))
        got = 0
        for j in order:
            if i == j:
                continue
            c = float(row[j])
            if not np.isfinite(c):
                continue
            out[a].append({"id": names[j], "corr": round(c, 4), "abs": round(abs(c), 4)})
            got += 1
            if got >= k:
                break
    return out

def build_edges(corr: pd.DataFrame, resid: pd.DataFrame) -> list[dict]:
    names = list(corr.columns)
    cands = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            c = corr.loc[a, b]
            if np.isfinite(c) and abs(c) >= CORR_THR:
                cands.append((a, b, float(c)))
    cands.sort(key=lambda t: -abs(t[2]))
    degree = {n: 0 for n in names}
    edges = []
    for a, b, c in cands:
        if degree[a] >= MAX_EDGES_PER_NODE or degree[b] >= MAX_EDGES_PER_NODE:
            continue
        conf = year_confirm(resid, a, b, CORR_THR)
        if conf < MIN_YEAR_CONFIRM:
            continue
        edges.append({
            "source": a, "target": b,
            "corr": round(c, 4), "abs": round(abs(c), 4),
            "sign": 1 if c >= 0 else -1, "years": int(conf),
        })
        degree[a] += 1
        degree[b] += 1
    return edges


def expand_query(q: str, names: list[str], l1_of: dict[str, str]) -> list[str]:
    """自动扩展：优先最长关键词规则，避免「电力」被短词「电」扩到整个电子。"""
    q = (q or "").strip()
    if not q:
        return []
    hit: set[str] = set()
    matched_keys = [k for k in QUERY_EXPAND if k in q or q in k]
    if matched_keys:
        max_len = max(len(k) for k in matched_keys)
        matched_keys = [k for k in matched_keys if len(k) == max_len]
    for key in matched_keys:
        rule = QUERY_EXPAND[key]
        for ex in rule.get("exact", []):
            if ex in names:
                hit.add(ex)
        for sub in rule.get("contains", []):
            for n in names:
                if sub in n:
                    hit.add(n)
    for n in names:
        if q in n or n in q:
            hit.add(n)
        l1 = l1_of.get(n, "")
        if q and q in l1:
            hit.add(n)
    return sorted(hit)


def pack_payload(piv: pd.DataFrame, resid: pd.DataFrame, meta: pd.DataFrame) -> dict:
    corr = resid.corr(method="pearson", min_periods=120)
    names = [c for c in corr.columns if corr[c].notna().sum() > 10]
    corr = corr.loc[names, names]
    cmat = corr.fillna(0).values.copy()
    np.fill_diagonal(cmat, 1.0)
    dist = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - cmat)))
    xyz = classical_mds(dist, 3)
    xyz = (xyz - xyz.mean(0)) / (xyz.std(0) + 1e-9) * 40.0

    meta_i = meta.set_index("id").reindex(names)
    edges = build_edges(corr, resid[names])
    # 给度为 0 的点补 2 条最强相关边（虚边标记 soft=1），便于点云连线与查询展开
    linked = set()
    for e in edges:
        linked.add(e["source"]); linked.add(e["target"])
    soft = []
    for name in names:
        if name in linked:
            continue
        row = corr[name].drop(labels=[name]).dropna()
        if row.empty:
            continue
        top = row.reindex(row.abs().sort_values(ascending=False).index).head(2)
        for other, c in top.items():
            a, b = sorted([name, other])
            key = (a, b)
            if key in {(x["source"], x["target"]) if x["source"] < x["target"] else (x["target"], x["source"]) for x in edges + soft}:
                continue
            soft.append({
                "source": name, "target": other,
                "corr": round(float(c), 4), "abs": round(abs(float(c)), 4),
                "sign": 1 if c >= 0 else -1, "years": 0, "soft": 1,
            })
    edges = edges + soft
    last = piv[names].ffill().iloc[-1]
    cum20 = ((1 + piv[names].fillna(0) / 100).tail(20).prod() - 1) * 100

    nodes = []
    for i, name in enumerate(names):
        l1 = ""
        n_stocks = 0
        if name in meta_i.index:
            l1 = str(meta_i.loc[name, "l1"]) if pd.notna(meta_i.loc[name, "l1"]) else ""
            n_stocks = int(meta_i.loc[name, "n"]) if pd.notna(meta_i.loc[name, "n"]) else 0
        nodes.append({
            "id": name, "name": name, "l1": l1, "n": n_stocks,
            "x": round(float(xyz[i, 0]), 3),
            "y": round(float(xyz[i, 1]), 3),
            "z": round(float(xyz[i, 2]), 3),
            "last": None if not np.isfinite(last.get(name, np.nan)) else round(float(last.get(name)), 3),
            "cum20": None if not np.isfinite(cum20.get(name, np.nan)) else round(float(cum20.get(name)), 2),
        })

    # 可视化边仍用稳健阈值；邻居表用 Top-K |ρ|，避免「电力」等点在图上孤立无法展开
    neighbors = top_neighbors(corr, k=6)

    l1_of = {n["id"]: n["l1"] for n in nodes}
    query_demo = {q: expand_query(q, names, l1_of) for q in ("电力", "半导体", "白酒")}

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "start": START,
        "end": str(piv.index.max().date()),
        "n_days": int(piv.notna().any(axis=1).sum()),
        "n_sectors": len(nodes),
        "corr_thr": CORR_THR,
        "note": "坐标：去市场beta后残差相关 → 经典MDS三维；边为|ρ|阈值+多年同号复现的同步相关（非因果）。查询会按关键词自动扩展相关二级。",
        "nodes": nodes,
        "edges": edges,
        "neighbors": neighbors,
        "presets": ["电力", "半导体", "白酒", "新能源", "医药", "军工", "银行", "地产"],
        "query_demo": query_demo,
        "expand_rules": QUERY_EXPAND,
    }


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>申万二级 · 相关点云</title>
<style>
:root{{--bg:#070b14;--panel:rgba(12,18,32,.9);--line:rgba(120,160,255,.22);--text:#e8eefc;--muted:#8b9bb8;--accent:#5ad1ff;--hot:#ff7a7a;--ok:#3ddc97}}
*{{box-sizing:border-box}}
html,body{{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;overflow:hidden}}
#c{{position:fixed;inset:0;display:block}}
.hud{{position:fixed;z-index:5;pointer-events:none}}
.hud *{{pointer-events:auto}}
.top{{top:16px;left:16px;right:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;backdrop-filter:blur(10px);box-shadow:0 8px 32px rgba(0,0,0,.35)}}
.title{{font-size:15px;font-weight:650}}
.sub{{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.45;max-width:460px}}
.search{{display:flex;gap:8px;align-items:center;margin-top:10px;min-width:min(440px,100%)}}
.search input{{flex:1;background:#0b1220;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:10px 12px;font-size:14px;outline:none}}
.search input:focus{{border-color:var(--accent)}}
.search button,.chip{{background:#132038;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 12px;font-size:12px;cursor:pointer}}
.search button:hover,.chip:hover,.chip.active{{border-color:var(--accent);color:var(--accent)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.side{{top:16px;right:16px;width:min(340px,92vw);max-height:calc(100vh - 32px);overflow:auto}}
.side h3{{margin:0 0 8px;font-size:14px}}
.meta{{font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.4}}
.kv{{display:grid;grid-template-columns:64px 1fr;gap:4px 8px;font-size:12px;margin-bottom:10px}}
.kv b{{color:var(--muted);font-weight:500}}
.list{{list-style:none;padding:0;margin:0}}
.list li{{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.06);font-size:12px;cursor:pointer}}
.list li:hover{{color:var(--accent)}}
.pos{{color:var(--hot)}} .neg{{color:var(--ok)}}
.hint{{position:fixed;bottom:14px;left:16px;font-size:11px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 10px;max-width:min(720px,90vw)}}
.empty{{color:var(--muted);font-size:12px;line-height:1.5}}
@media(max-width:720px){{.side{{top:auto;bottom:12px;right:12px;left:12px;width:auto;max-height:36vh}}}}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="hud top">
  <div class="card">
    <div class="title">申万二级 · 相关点云</div>
    <div class="sub">去市场 beta 后的残差相关 → MDS 三维。点近≈结构相近；红/绿线=正/负稳健同步相关。查询会自动扩展相关二级（如「电力」→电网/电源/储能等）。</div>
    <div class="search">
      <input id="q" placeholder="查询板块，如：电力、半导体、白酒…" autocomplete="off"/>
      <button id="go" type="button">聚焦</button>
      <button id="reset" type="button">全局</button>
    </div>
    <div class="chips" id="presets"></div>
  </div>
</div>
<aside class="hud side card" id="panel">
  <h3 id="p-title">全局视图</h3>
  <div class="meta" id="p-meta"></div>
  <div class="kv" id="p-kv"></div>
  <div id="p-body" class="empty">输入关键词自动扩展并高亮该簇。</div>
</aside>
<div class="hint">数据 {payload['start']} → {payload['end']} · {payload['n_sectors']} 个二级 · {payload['n_days']} 个交易日 · 边阈值 |ρ|≥{payload['corr_thr']} · 生成 {payload['generated_at']}</div>
<script type="importmap">{{"imports":{{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}}}</script>
<script type="module">
import * as THREE from 'three';
import {{ OrbitControls }} from 'three/addons/controls/OrbitControls.js';

const DATA = {data_json};
const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({{canvas, antialias:true}});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.setClearColor(0x070b14, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x070b14, 0.004);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 2000);
camera.position.set(0, 45, 145);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.55;
controls.minDistance = 35;
controls.maxDistance = 380;
scene.add(new THREE.AmbientLight(0x9eb7ff, 0.8));
const key = new THREE.PointLight(0x5ad1ff, 1.15, 700); key.position.set(80,110,70); scene.add(key);
const fill = new THREE.PointLight(0xff6b6b, 0.32, 700); fill.position.set(-90,-30,-80); scene.add(fill);

{{ // starfield
  const n=900, pos=new Float32Array(n*3);
  for(let i=0;i<n;i++){{ pos[i*3]=(Math.random()-0.5)*520; pos[i*3+1]=(Math.random()-0.5)*520; pos[i*3+2]=(Math.random()-0.5)*520; }}
  const g=new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos,3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({{color:0x243552, size:0.55, transparent:true, opacity:0.5}})));
}}

const nodeById = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const N = DATA.nodes.length;
const positions = new Float32Array(N*3);
const colors = new Float32Array(N*3);
const baseRGB = [];
for(let i=0;i<N;i++){{
  const n=DATA.nodes[i];
  positions[i*3]=n.x; positions[i*3+1]=n.y; positions[i*3+2]=n.z;
  const h=[...(n.l1||n.name)].reduce((a,c)=>a+c.charCodeAt(0),0);
  const col=new THREE.Color().setHSL((h%360)/360, 0.55, 0.58);
  colors[i*3]=col.r; colors[i*3+1]=col.g; colors[i*3+2]=col.b;
  baseRGB.push([col.r,col.g,col.b]);
}}
const pointsGeo = new THREE.BufferGeometry();
pointsGeo.setAttribute('position', new THREE.BufferAttribute(positions,3));
pointsGeo.setAttribute('color', new THREE.BufferAttribute(colors,3));
const pointsMat = new THREE.PointsMaterial({{size:3.4, vertexColors:true, transparent:true, opacity:0.95, sizeAttenuation:true, depthWrite:false}});
const points = new THREE.Points(pointsGeo, pointsMat); scene.add(points);

const glowTex = (()=>{{
  const c=document.createElement('canvas'); c.width=c.height=64;
  const ctx=c.getContext('2d');
  const g=ctx.createRadialGradient(32,32,2,32,32,30);
  g.addColorStop(0,'rgba(180,220,255,1)'); g.addColorStop(0.4,'rgba(90,180,255,.5)'); g.addColorStop(1,'rgba(90,180,255,0)');
  ctx.fillStyle=g; ctx.fillRect(0,0,64,64);
  return new THREE.CanvasTexture(c);
}})();
const sprites=[];
for(let i=0;i<N;i++){{
  const s=new THREE.Sprite(new THREE.SpriteMaterial({{map:glowTex, transparent:true, blending:THREE.AdditiveBlending, depthWrite:false, opacity:0.32}}));
  s.position.set(DATA.nodes[i].x, DATA.nodes[i].y, DATA.nodes[i].z);
  s.scale.set(5,5,1); scene.add(s); sprites.push(s);
}}

const edgePos=[], edgeCol=[];
const cPos=new THREE.Color(0xff7a7a), cNeg=new THREE.Color(0x3ddc97);
for(const e of DATA.edges){{
  const a=nodeById[e.source], b=nodeById[e.target]; if(!a||!b) continue;
  edgePos.push(a.x,a.y,a.z, b.x,b.y,b.z);
  const col=e.sign>=0?cPos:cNeg;
  const w=0.22+0.78*Math.min(1,(e.abs-0.4)/0.4);
  edgeCol.push(col.r*w,col.g*w,col.b*w, col.r*w,col.g*w,col.b*w);
}}
const edgeGeo=new THREE.BufferGeometry();
edgeGeo.setAttribute('position', new THREE.Float32BufferAttribute(edgePos,3));
edgeGeo.setAttribute('color', new THREE.Float32BufferAttribute(edgeCol,3));
const edgeLines=new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial({{vertexColors:true, transparent:true, opacity:0.34, depthWrite:false}}));
scene.add(edgeLines);

const labelGroup=new THREE.Group(); scene.add(labelGroup);
function makeLabel(text, color='#e8eefc'){{
  const c=document.createElement('canvas'); const ctx=c.getContext('2d');
  ctx.font='600 28px sans-serif'; const w=Math.ceil(ctx.measureText(text).width)+28;
  c.width=w; c.height=48; ctx.font='600 28px sans-serif';
  ctx.fillStyle='rgba(8,12,22,0.72)';
  ctx.beginPath(); const r=12; ctx.moveTo(r,4); ctx.arcTo(w,4,w,44,r); ctx.arcTo(w,44,0,44,r); ctx.arcTo(0,44,0,4,r); ctx.arcTo(0,4,w,4,r); ctx.closePath(); ctx.fill();
  ctx.fillStyle=color; ctx.textBaseline='middle'; ctx.fillText(text,14,26);
  const tex=new THREE.CanvasTexture(c); tex.minFilter=THREE.LinearFilter;
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({{map:tex, transparent:true, depthTest:false}}));
  sp.scale.set(w/10, 4.8, 1); return sp;
}}

let coreIds=new Set(), focusIds=new Set();

function expandQuery(q){{
  q=(q||'').trim(); if(!q) return [];
  const hit=new Set();
  const rules=DATA.expand_rules||{{}};
  let keys=Object.keys(rules).filter(k => q.includes(k)||k.includes(q));
  if(keys.length){{
    const m=Math.max(...keys.map(k=>k.length));
    keys=keys.filter(k=>k.length===m);
  }}
  for(const k of keys){{
    const rule=rules[k]||{{}};
    for(const ex of (rule.exact||[])) if(nodeById[ex]) hit.add(ex);
    for(const sub of (rule.contains||[])) for(const n of DATA.nodes) if(n.name.includes(sub)) hit.add(n.id);
  }}
  for(const n of DATA.nodes){{
    if(n.name.includes(q)||q.includes(n.name)) hit.add(n.id);
    if((n.l1||'').includes(q)) hit.add(n.id);
  }}
  return [...hit];
}}

function egoOf(cores){{
  const one=new Set(cores), two=new Set();
  for(const id of cores) for(const nb of (DATA.neighbors[id]||[])) one.add(nb.id);
  for(const id of one){{
    if(cores.includes(id)) continue;
    for(const nb of (DATA.neighbors[id]||[])) if(!one.has(nb.id)) two.add(nb.id);
  }}
  return {{one, two}};
}}

function clearLabels(){{
  while(labelGroup.children.length){{
    const ch=labelGroup.children.pop();
    ch.material.map?.dispose(); ch.material.dispose();
  }}
}}

function rebuildLabels(cores, neighbors){{
  clearLabels();
  const show=[...new Set([...(cores||[]), ...(neighbors||[])])].slice(0,22);
  for(const id of show){{
    const n=nodeById[id]; if(!n) continue;
    const lab=makeLabel(n.name, coreIds.has(id)?'#5ad1ff':'#e8eefc');
    lab.position.set(n.x, n.y+4.5, n.z); labelGroup.add(lab);
  }}
}}

function applyFocusVisual(){{
  const colAttr=pointsGeo.getAttribute('color');
  const has=focusIds.size>0;
  for(let i=0;i<N;i++){{
    const id=DATA.nodes[i].id;
    let r=baseRGB[i][0], g=baseRGB[i][1], b=baseRGB[i][2];
    let spro=0.32, scale=5;
    if(has){{
      if(coreIds.has(id)){{ r=0.35; g=0.82; b=1; spro=0.92; scale=9; }}
      else if(focusIds.has(id)){{ spro=0.55; scale=6.5; }}
      else {{ r*=0.25; g*=0.28; b*=0.35; spro=0.04; scale=2.8; }}
    }}
    colAttr.setXYZ(i,r,g,b);
    sprites[i].material.opacity=spro;
    sprites[i].scale.set(scale,scale,1);
  }}
  colAttr.needsUpdate=true;
  edgeLines.material.opacity = has ? 0.55 : 0.34;
}}

function renderPanel(cores, neighborIds){{
  const title=document.getElementById('p-title');
  const meta=document.getElementById('p-meta');
  const kv=document.getElementById('p-kv');
  const body=document.getElementById('p-body');
  if(!cores||!cores.length){{
    title.textContent='全局视图';
    meta.textContent=`${{DATA.n_sectors}} 个二级 · 边 ${{DATA.edges.length}} 条（|ρ|≥${{DATA.corr_thr}} 且多年复现）`;
    kv.innerHTML='';
    body.innerHTML=`<div class="empty">${{DATA.note}}</div>`;
    return;
  }}
  title.textContent = cores.length===1 ? cores[0] : `聚焦簇（${{cores.length}}）`;
  meta.textContent = `自动扩展核心 ${{cores.length}} · 一跳邻居 ${{neighborIds.length}}`;
  if(cores.length===1){{
    const n=nodeById[cores[0]];
    kv.innerHTML=`<b>一级</b><span>${{n.l1||'-'}}</span><b>成分</b><span>${{n.n||'-'}} 只</span><b>近20日</b><span class="${{(n.cum20||0)>=0?'pos':'neg'}}">${{n.cum20??'-'}}%</span><b>最新</b><span class="${{(n.last||0)>=0?'pos':'neg'}}">${{n.last??'-'}}%</span>`;
  }} else {{
    kv.innerHTML=cores.slice(0,12).map(id=>`<b>核心</b><span>${{id}}</span>`).join('');
  }}
  const rows=[], seen=new Set();
  for(const c of cores){{
    for(const nb of (DATA.neighbors[c]||[])){{
      if(coreIds.has(nb.id)||seen.has(nb.id)) continue;
      seen.add(nb.id); rows.push(nb);
    }}
  }}
  rows.sort((a,b)=>b.abs-a.abs);
  body.innerHTML = rows.length
    ? `<ul class="list">${{rows.slice(0,24).map(r=>`<li data-id="${{r.id}}"><span>${{r.id}}</span><span class="${{r.corr>=0?'pos':'neg'}}">ρ ${{r.corr.toFixed(2)}}</span></li>`).join('')}}</ul>`
    : `<div class="empty">该簇暂无足够强的稳健相关边，仍可看空间邻近。</div>`;
  body.querySelectorAll('li').forEach(li => li.onclick=()=>{{ document.getElementById('q').value=li.dataset.id; setFocus([li.dataset.id]); }});
}}

function setFocus(cores){{
  coreIds=new Set(cores);
  if(!cores.length){{
    focusIds=new Set(); applyFocusVisual(); clearLabels(); renderPanel(null,[]); return;
  }}
  const {{one,two}}=egoOf(cores);
  focusIds=new Set([...one, ...two]);
  applyFocusVisual();
  const pts=cores.map(id=>nodeById[id]).filter(Boolean);
  if(pts.length){{
    const cx=pts.reduce((s,p)=>s+p.x,0)/pts.length;
    const cy=pts.reduce((s,p)=>s+p.y,0)/pts.length;
    const cz=pts.reduce((s,p)=>s+p.z,0)/pts.length;
    controls.target.set(cx,cy,cz);
    camera.position.set(cx+55, cy+28, cz+95);
  }}
  const neighbors=[...one].filter(id=>!coreIds.has(id));
  renderPanel(cores, neighbors);
  rebuildLabels(cores, neighbors.slice(0,18));
}}

function runQuery(){{
  const cores=expandQuery(document.getElementById('q').value);
  if(!cores.length){{
    renderPanel(null,[]);
    document.getElementById('p-body').innerHTML=`<div class="empty">未匹配到二级板块，试试「电力」「半导体」「白酒」。</div>`;
    return;
  }}
  setFocus(cores);
}}

document.getElementById('go').onclick=runQuery;
document.getElementById('reset').onclick=()=>{{ document.getElementById('q').value=''; setFocus([]); }};
document.getElementById('q').addEventListener('keydown', e=>{{ if(e.key==='Enter') runQuery(); }});
const presetsEl=document.getElementById('presets');
(DATA.presets||[]).forEach(p=>{{
  const b=document.createElement('button'); b.className='chip'; b.type='button'; b.textContent=p;
  b.onclick=()=>{{ [...presetsEl.children].forEach(x=>x.classList.remove('active')); b.classList.add('active'); document.getElementById('q').value=p; runQuery(); }};
  presetsEl.appendChild(b);
}});

const raycaster=new THREE.Raycaster(); raycaster.params.Points.threshold=2.8;
const mouse=new THREE.Vector2();
canvas.addEventListener('pointerdown', ev=>{{
  mouse.x=(ev.clientX/innerWidth)*2-1; mouse.y=-(ev.clientY/innerHeight)*2+1;
  raycaster.setFromCamera(mouse, camera);
  const hits=raycaster.intersectObject(points);
  if(hits.length){{ const id=DATA.nodes[hits[0].index].id; document.getElementById('q').value=id; setFocus([id]); }}
}});
window.addEventListener('resize', ()=>{{ camera.aspect=innerWidth/innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth,innerHeight); }});
renderPanel(null,[]);
(function tick(){{ requestAnimationFrame(tick); controls.update(); renderer.render(scene,camera); }})();
</script>
</body>
</html>
"""


def update_nav() -> None:
    gen_index = PROJECT_DIR / "scripts" / "reports" / "gen_index.py"
    if not gen_index.exists():
        return
    txt = gen_index.read_text(encoding="utf-8")
    if "sector_corr_cloud.html" in txt:
        return
    anchor = "sector_atlas.html"
    if anchor not in txt:
        return
    injection = (
        '("sector_corr_cloud.html", "🌌", "板块相关点云", '
        '"申万二级残差相关三维点云，支持电力等查询自动扩展聚焦", "板块跟踪"),\n            '
        f'("{anchor}"'
    )
    # try tuple style used by gen_index
    old = f'("{anchor}"'
    if old in txt:
        gen_index.write_text(txt.replace(old, injection, 1), encoding="utf-8")
        print("已注册 gen_index 入口")
        import subprocess
        subprocess.run([sys.executable, "-m", "scripts.reports.gen_index"], cwd=PROJECT_DIR, check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-nav", action="store_true")
    args = ap.parse_args()

    print("加载 2022→今 mom1，聚合申万二级…")
    piv, meta = load_sw2_returns()
    print(f"  板块 {piv.shape[1]} · 交易日 {piv.shape[0]} · 截止 {piv.index.max().date()}")
    mkt = market_proxy(piv)
    print("去市场 beta，MDS + 稳健相关边…")
    resid = residualize(piv, mkt)
    payload = pack_payload(piv, resid, meta)
    print(f"  节点 {payload['n_sectors']} · 边 {len(payload['edges'])}")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"写入 {OUTPUT_HTML}")
    print(f"写入 {OUTPUT_JSON}")

    l1_of = {n["id"]: n["l1"] for n in payload["nodes"]}
    names = [n["id"] for n in payload["nodes"]]
    for q in ("电力", "半导体", "白酒"):
        hits = expand_query(q, names, l1_of)
        print(f"  查询「{q}」→ {len(hits)}: {hits[:10]}{'…' if len(hits)>10 else ''}")

    if not args.no_nav:
        update_nav()


if __name__ == "__main__":
    main()
