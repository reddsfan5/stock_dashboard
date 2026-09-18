#!/usr/bin/env python3
"""申万二级板块相关点云：残差相关 MDS 三维 + 领先滞后箭头 + Three.js 查询页。

用法:
  python -m scripts.reports.gen_sector_corr_cloud
  python -m scripts.reports.gen_sector_corr_cloud --no-nav
  python -m scripts.reports.gen_sector_corr_cloud --from-cache --no-nav
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
MAX_LAG = 4  # 周频滞后天数（约 1–4 周）
LEAD_XCORR_THR = 0.08
LEAD_ASYM = 0.015  # 领先方向需略强于反向
LEAD_MAX_OUT = 4  # 每点最多指出几条领先边
EVENT_Q = 0.80
HORIZON = 5
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
    for _, sub in resid[[a, b]].groupby(resid.index.year):
        sub = sub.dropna()
        if len(sub) < 60:
            continue
        c = sub[a].corr(sub[b])
        if np.isfinite(c) and abs(c) >= thr * 0.85 and np.sign(c) == np.sign(full):
            ok += 1
    return ok


def top_neighbors(corr: pd.DataFrame, k: int = 6) -> dict[str, list]:
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


def _xcorr_at_lag(a: np.ndarray, b: np.ndarray, lag: int, min_n: int = 40) -> float:
    """corr(a_t, b_{t+lag}) for lag>=1."""
    if lag <= 0:
        return np.nan
    x = a[:-lag]
    y = b[lag:]
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < min_n:
        return np.nan
    x, y = x[mask], y[mask]
    sx, sy = x.std(), y.std()
    if sx < 1e-12 or sy < 1e-12:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


def event_follow_prob(leader: np.ndarray, follower: np.ndarray, horizon: int = HORIZON, q: float = EVENT_Q) -> dict:
    """leader 日残差 > 分位后，follower 未来 horizon 日累计为正的条件概率 vs 基准。"""
    n = len(leader)
    if n < horizon + 50:
        return {"n": 0, "p_up": None, "p_dn": None, "base_up": None, "base_dn": None, "lift_up": None, "lift_dn": None}
    # future cum return of follower
    fut = np.full(n, np.nan)
    for t in range(n - horizon):
        window = follower[t + 1 : t + 1 + horizon]
        if np.isfinite(window).sum() < horizon:
            continue
        fut[t] = np.nansum(window)
    valid = np.isfinite(leader) & np.isfinite(fut)
    if valid.sum() < 80:
        return {"n": int(valid.sum()), "p_up": None, "p_dn": None, "base_up": None, "base_dn": None, "lift_up": None, "lift_dn": None}
    thr_hi = np.nanquantile(leader[valid], q)
    thr_lo = np.nanquantile(leader[valid], 1 - q)
    base_up = float(np.mean(fut[valid] > 0))
    base_dn = float(np.mean(fut[valid] < 0))
    up_mask = valid & (leader >= thr_hi)
    dn_mask = valid & (leader <= thr_lo)
    p_up = float(np.mean(fut[up_mask] > 0)) if up_mask.sum() >= 20 else None
    p_dn = float(np.mean(fut[dn_mask] < 0)) if dn_mask.sum() >= 20 else None
    return {
        "n_up": int(up_mask.sum()),
        "n_dn": int(dn_mask.sum()),
        "p_up": None if p_up is None else round(p_up, 3),
        "p_dn": None if p_dn is None else round(p_dn, 3),
        "base_up": round(base_up, 3),
        "base_dn": round(base_dn, 3),
        "lift_up": None if p_up is None else round(p_up - base_up, 3),
        "lift_dn": None if p_dn is None else round(p_dn - base_dn, 3),
    }


def build_lead_edges(resid: pd.DataFrame, corr: pd.DataFrame) -> list[dict]:
    """周频残差上挖掘 A 领先 B；事件概率仍用日频残差（更贴近交易观察窗）。"""
    names = list(resid.columns)
    # 周频：按日历周求和，抑制日噪声，交叉相关更可读
    weekly = resid.resample("W-FRI").sum(min_count=2)
    warr = {n: weekly[n].values.astype(float) for n in names}
    darr = {n: resid[n].values.astype(float) for n in names}

    pair_pool = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            c = corr.loc[a, b]
            if np.isfinite(c) and abs(c) >= 0.20:
                pair_pool.append((a, b, float(c)))
    pair_pool.sort(key=lambda t: -abs(t[2]))
    pair_pool = pair_pool[: min(len(pair_pool), 3000)]

    cands = []
    for a, b, sync in pair_pool:
        best_ab = (-1, np.nan)
        best_ba = (-1, np.nan)
        for lag in range(1, MAX_LAG + 1):
            cab = _xcorr_at_lag(warr[a], warr[b], lag)
            cba = _xcorr_at_lag(warr[b], warr[a], lag)
            if np.isfinite(cab) and (not np.isfinite(best_ab[1]) or abs(cab) > abs(best_ab[1])):
                best_ab = (lag, cab)
            if np.isfinite(cba) and (not np.isfinite(best_ba[1]) or abs(cba) > abs(best_ba[1])):
                best_ba = (lag, cba)
        if np.isfinite(best_ab[1]) and abs(best_ab[1]) >= LEAD_XCORR_THR:
            rev = abs(best_ba[1]) if np.isfinite(best_ba[1]) else 0.0
            if abs(best_ab[1]) >= rev + LEAD_ASYM:
                cands.append((a, b, best_ab[0], float(best_ab[1]), sync))
        if np.isfinite(best_ba[1]) and abs(best_ba[1]) >= LEAD_XCORR_THR:
            rev = abs(best_ab[1]) if np.isfinite(best_ab[1]) else 0.0
            if abs(best_ba[1]) >= rev + LEAD_ASYM:
                cands.append((b, a, best_ba[0], float(best_ba[1]), sync))

    cands.sort(key=lambda t: -abs(t[3]))
    out_deg = {n: 0 for n in names}
    edges = []
    for src, tgt, lag, xc, sync in cands:
        if out_deg[src] >= LEAD_MAX_OUT:
            continue
        ev = event_follow_prob(darr[src], darr[tgt])
        # 必须有可交易意义的条件概率提升，避免纯噪声交叉相关
        lift_ok = (
            (ev.get("lift_up") is not None and ev["lift_up"] >= 0.025)
            or (ev.get("lift_dn") is not None and ev["lift_dn"] >= 0.025)
        )
        if not lift_ok:
            continue
        edges.append({
            "source": src,
            "target": tgt,
            "lag": int(lag),
            "lag_unit": "week",
            "xcorr": round(xc, 4),
            "abs": round(abs(xc), 4),
            "sign": 1 if xc >= 0 else -1,
            "sync": round(sync, 4),
            "p_up": ev.get("p_up"),
            "p_dn": ev.get("p_dn"),
            "base_up": ev.get("base_up"),
            "base_dn": ev.get("base_dn"),
            "lift_up": ev.get("lift_up"),
            "lift_dn": ev.get("lift_dn"),
            "n_up": ev.get("n_up", 0),
            "n_dn": ev.get("n_dn", 0),
            "horizon": HORIZON,
        })
        out_deg[src] += 1
    return edges


def expand_query(q: str, names: list[str], l1_of: dict[str, str]) -> list[str]:
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
    resid = resid[names]
    cmat = corr.fillna(0).values.copy()
    np.fill_diagonal(cmat, 1.0)
    dist = np.sqrt(np.maximum(0.0, 2.0 * (1.0 - cmat)))
    xyz = classical_mds(dist, 3)
    xyz = (xyz - xyz.mean(0)) / (xyz.std(0) + 1e-9) * 40.0

    meta_i = meta.set_index("id").reindex(names)
    edges = build_edges(corr, resid)
    linked = {e["source"] for e in edges} | {e["target"] for e in edges}
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
            existing = {
                (x["source"], x["target"]) if x["source"] < x["target"] else (x["target"], x["source"])
                for x in edges + soft
            }
            if (a, b) in existing:
                continue
            soft.append({
                "source": name, "target": other,
                "corr": round(float(c), 4), "abs": round(abs(float(c)), 4),
                "sign": 1 if c >= 0 else -1, "years": 0, "soft": 1,
            })
    edges = edges + soft

    print("  计算领先—滞后边与跟涨/跟跌概率…")
    lead_edges = build_lead_edges(resid, corr)
    print(f"  领先边 {len(lead_edges)} 条")

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

    neighbors = top_neighbors(corr, k=6)
    lead_out: dict[str, list] = {n: [] for n in names}
    lead_in: dict[str, list] = {n: [] for n in names}
    for e in lead_edges:
        item = {
            "id": e["target"], "lag": e["lag"], "xcorr": e["xcorr"], "abs": e["abs"],
            "lift_up": e["lift_up"], "lift_dn": e["lift_dn"],
            "p_up": e["p_up"], "p_dn": e["p_dn"],
        }
        lead_out[e["source"]].append(item)
        lead_in[e["target"]].append({
            "id": e["source"], "lag": e["lag"], "xcorr": e["xcorr"], "abs": e["abs"],
            "lift_up": e["lift_up"], "lift_dn": e["lift_dn"],
            "p_up": e["p_up"], "p_dn": e["p_dn"],
        })
    for k in names:
        lead_out[k].sort(key=lambda d: -d["abs"])
        lead_in[k].sort(key=lambda d: -d["abs"])

    l1_of = {n["id"]: n["l1"] for n in nodes}
    query_demo = {q: expand_query(q, names, l1_of) for q in ("电力", "半导体", "白酒")}

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "start": START,
        "end": str(piv.index.max().date()),
        "n_days": int(piv.notna().any(axis=1).sum()),
        "n_sectors": len(nodes),
        "corr_thr": CORR_THR,
        "lead_thr": LEAD_XCORR_THR,
        "max_lag": MAX_LAG,
        "horizon": HORIZON,
        "note": "同步边=去beta日残差相关；箭头=周频残差交叉相关领先（滞后周数）+ 日频事件跟涨/跟跌条件概率提升。非因果，仅统计倾向。",
        "nodes": nodes,
        "edges": edges,
        "lead_edges": lead_edges,
        "neighbors": neighbors,
        "lead_out": lead_out,
        "lead_in": lead_in,
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
:root{{--bg:#070b14;--panel:rgba(12,18,32,.92);--line:rgba(120,160,255,.22);--text:#e8eefc;--muted:#8b9bb8;--accent:#5ad1ff;--hot:#ff7a7a;--ok:#3ddc97;--lead:#ffd166}}
*{{box-sizing:border-box}}
html,body{{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;overflow:hidden}}
#c{{position:fixed;inset:0;display:block}}
.hud{{position:fixed;z-index:5;pointer-events:none}}
.hud *{{pointer-events:auto}}
.top{{top:16px;left:16px;right:16px;display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;backdrop-filter:blur(10px);box-shadow:0 8px 32px rgba(0,0,0,.35)}}
.title{{font-size:15px;font-weight:650}}
.sub{{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.45;max-width:520px}}
.search{{display:flex;gap:8px;align-items:center;margin-top:10px;min-width:min(460px,100%);flex-wrap:wrap}}
.search input{{flex:1;min-width:160px;background:#0b1220;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:10px 12px;font-size:14px;outline:none}}
.search input:focus{{border-color:var(--accent)}}
.search button,.chip,.mode-btn{{background:#132038;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 12px;font-size:12px;cursor:pointer}}
.search button:hover,.chip:hover,.chip.active,.mode-btn:hover,.mode-btn.active{{border-color:var(--accent);color:var(--accent)}}
.mode-btn.active{{background:rgba(90,209,255,.12)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.modes{{display:flex;gap:6px;margin-top:8px;flex-wrap:wrap}}
.side{{top:16px;right:16px;width:min(360px,92vw);max-height:calc(100vh - 32px);overflow:auto}}
.side h3{{margin:0 0 8px;font-size:14px}}
.meta{{font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.4}}
.kv{{display:grid;grid-template-columns:72px 1fr;gap:4px 8px;font-size:12px;margin-bottom:10px}}
.kv b{{color:var(--muted);font-weight:500}}
.sec{{font-size:12px;color:var(--muted);margin:12px 0 6px}}
.list{{list-style:none;padding:0;margin:0}}
.list li{{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.06);font-size:12px;cursor:pointer}}
.list li:hover{{color:var(--accent)}}
.list .sub2{{display:block;color:var(--muted);font-size:11px;margin-top:2px}}
.pos{{color:var(--hot)}} .neg{{color:var(--ok)}} .lead{{color:var(--lead)}}
.hint{{position:fixed;bottom:14px;left:16px;font-size:11px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 10px;max-width:min(760px,90vw)}}
.empty{{color:var(--muted);font-size:12px;line-height:1.5}}
@media(max-width:720px){{.side{{top:auto;bottom:12px;right:12px;left:12px;width:auto;max-height:36vh}}}}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="hud top">
  <div class="card">
    <div class="title">申万二级 · 相关点云</div>
    <div class="sub">去市场 beta 后的残差结构。同步模式看共涨共跌；传导模式看领先箭头与跟涨/跟跌概率。查询自动扩展相关二级。</div>
    <div class="search">
      <input id="q" placeholder="查询板块，如：电力、半导体、白酒…" autocomplete="off"/>
      <button id="go" type="button">聚焦</button>
      <button id="reset" type="button">全局</button>
    </div>
    <div class="modes">
      <button class="mode-btn active" id="mode-sync" type="button">同步相关</button>
      <button class="mode-btn" id="mode-lead" type="button">领先传导</button>
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
<div class="hint">数据 {payload['start']} → {payload['end']} · {payload['n_sectors']} 二级 · 同步边 |ρ|≥{payload['corr_thr']} · 领先周频 |xcorr|≥{payload['lead_thr']} · 跟涨观察 {payload['horizon']} 日 · 生成 {payload['generated_at']}</div>
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

{{ const n=900, pos=new Float32Array(n*3);
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

function buildSyncEdges(){{
  const edgePos=[], edgeCol=[];
  const cPos=new THREE.Color(0xff7a7a), cNeg=new THREE.Color(0x3ddc97);
  for(const e of DATA.edges){{
    const a=nodeById[e.source], b=nodeById[e.target]; if(!a||!b) continue;
    edgePos.push(a.x,a.y,a.z, b.x,b.y,b.z);
    const col=e.sign>=0?cPos:cNeg;
    const w=0.22+0.78*Math.min(1,(e.abs-0.35)/0.4);
    edgeCol.push(col.r*w,col.g*w,col.b*w, col.r*w,col.g*w,col.b*w);
  }}
  const geo=new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(edgePos,3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(edgeCol,3));
  return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({{vertexColors:true, transparent:true, opacity:0.34, depthWrite:false}}));
}}

const syncLines = buildSyncEdges(); scene.add(syncLines);
const egoSyncLines = new THREE.LineSegments(
  new THREE.BufferGeometry(),
  new THREE.LineBasicMaterial({{vertexColors:true, transparent:true, opacity:0.92, depthWrite:false}})
);
egoSyncLines.visible = false;
scene.add(egoSyncLines);
const leadGroup = new THREE.Group(); scene.add(leadGroup);
leadGroup.visible = false;

let edgeGrow = null; // {{token, t0, dur, segs:[{{ax,ay,az,bx,by,bz, cr,cg,cb}}]}}
let edgeGrowToken = 0;

function cancelEdgeGrow(){{
  edgeGrowToken++;
  edgeGrow = null;
}}

function easeOutCubic(t){{ return 1 - Math.pow(1-t, 3); }}

function maxCorrToCores(nodeId){{
  let best = 0;
  for(const c of coreIds){{
    if(c === nodeId) continue;
    for(const nb of (DATA.neighbors[c]||[])){{
      if(nb.id === nodeId) best = Math.max(best, nb.abs ?? Math.abs(nb.corr||0));
    }}
    for(const nb of (DATA.neighbors[nodeId]||[])){{
      if(nb.id === c) best = Math.max(best, nb.abs ?? Math.abs(nb.corr||0));
    }}
  }}
  for(const e of DATA.edges){{
    const a=e.source, b=e.target;
    if((coreIds.has(a) && b===nodeId) || (coreIds.has(b) && a===nodeId)){{
      best = Math.max(best, e.abs ?? Math.abs(e.corr||0));
    }}
  }}
  if(mode==='lead'){{
    for(const c of coreIds){{
      for(const nb of (DATA.lead_out[c]||[])) if(nb.id===nodeId) best = Math.max(best, nb.abs ?? Math.abs(nb.xcorr||0));
      for(const nb of (DATA.lead_in[c]||[])) if(nb.id===nodeId) best = Math.max(best, nb.abs ?? Math.abs(nb.xcorr||0));
    }}
  }}
  return best;
}}

function collectEgoSyncSegs(){{
  const cPos=new THREE.Color(0xff7a7a), cNeg=new THREE.Color(0x3ddc97);
  const segs=[];
  const seen=new Set();
  for(const e of DATA.edges){{
    const inEgo = focusIds.has(e.source) && focusIds.has(e.target);
    const touchesCore = coreIds.has(e.source) || coreIds.has(e.target);
    if(!inEgo || !touchesCore) continue;
    const key = e.source < e.target ? e.source+'|'+e.target : e.target+'|'+e.source;
    if(seen.has(key)) continue;
    seen.add(key);
    const a=nodeById[e.source], b=nodeById[e.target]; if(!a||!b) continue;
    // grow from core endpoint toward non-core (if both cores, from source)
    let ax=a.x, ay=a.y, az=a.z, bx=b.x, by=b.y, bz=b.z;
    if(coreIds.has(e.target) && !coreIds.has(e.source)){{
      ax=b.x; ay=b.y; az=b.z; bx=a.x; by=a.y; bz=a.z;
    }} else if(coreIds.has(e.source) && coreIds.has(e.target)){{
      // keep source→target
    }} else if(!coreIds.has(e.source) && coreIds.has(e.target)){{
      ax=b.x; ay=b.y; az=b.z; bx=a.x; by=a.y; bz=a.z;
    }}
    const strength = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.corr||0)) - 0.35) / 0.45));
    const w = 0.28 + 0.72 * strength;
    const col = (e.sign>=0 ? cPos : cNeg).clone().multiplyScalar(0.45 + 0.55*w);
    segs.push({{ax,ay,az,bx,by,bz, cr:col.r, cg:col.g, cb:col.b, abs:e.abs ?? Math.abs(e.corr||0)}});
  }}
  segs.sort((a,b)=>b.abs-a.abs);
  return segs;
}}

function applyEgoSyncPositions(segs, u){{
  const pos=[], col=[];
  for(const s of segs){{
    const x = s.ax + (s.bx - s.ax) * u;
    const y = s.ay + (s.by - s.ay) * u;
    const z = s.az + (s.bz - s.az) * u;
    pos.push(s.ax,s.ay,s.az, x,y,z);
    col.push(s.cr,s.cg,s.cb, s.cr,s.cg,s.cb);
  }}
  const geo = egoSyncLines.geometry;
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos,3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(col,3));
  geo.computeBoundingSphere();
}}

function startEdgeGrow(animate){{
  cancelEdgeGrow();
  const token = edgeGrowToken;
  if(mode==='sync'){{
    const segs = collectEgoSyncSegs();
    if(!segs.length){{
      egoSyncLines.visible = false;
      return;
    }}
    egoSyncLines.visible = true;
    if(!animate){{
      applyEgoSyncPositions(segs, 1);
      return;
    }}
    applyEgoSyncPositions(segs, 0.02);
    edgeGrow = {{token, t0: performance.now(), dur: 550, segs, kind:'sync'}};
  }} else {{
    // lead mode: rebuild arrows then fade/grow opacity by |xcorr|
    rebuildLeadArrows(focusIds.size ? focusIds : null, true);
    if(!animate){{
      for(const ch of leadGroup.children){{
        if(ch.userData && ch.userData.targetOpacity != null) ch.material.opacity = ch.userData.targetOpacity;
      }}
      return;
    }}
    edgeGrow = {{token, t0: performance.now(), dur: 500, kind:'lead'}};
  }}
}}

function tickEdgeGrow(now){{
  if(!edgeGrow || edgeGrow.token !== edgeGrowToken) return;
  const u = easeOutCubic(Math.min(1, (now - edgeGrow.t0) / edgeGrow.dur));
  if(edgeGrow.kind==='sync'){{
    applyEgoSyncPositions(edgeGrow.segs, u);
  }} else {{
    for(const ch of leadGroup.children){{
      const tgt = (ch.userData && ch.userData.targetOpacity != null) ? ch.userData.targetOpacity : 0.8;
      ch.material.opacity = tgt * u;
      // slight grow: scale along shaft from near-zero
      if(ch.userData && ch.userData.baseScale){{
        const s = 0.15 + 0.85 * u;
        ch.scale.set(s, s, s);
      }}
    }}
  }}
  if(u >= 1) edgeGrow = null;
}}

function rebuildLeadArrows(filterIds=null, forFocus=false){{
  while(leadGroup.children.length){{
    const ch=leadGroup.children.pop();
    ch.geometry?.dispose?.(); ch.material?.dispose?.();
  }}
  const edges = DATA.lead_edges||[];
  const cPos=new THREE.Color(0xffd166), cNeg=new THREE.Color(0x7aa2ff);
  for(const e of edges){{
    if(filterIds && filterIds.size){{
      if(!filterIds.has(e.source) && !filterIds.has(e.target)) continue;
    }}
    const a=nodeById[e.source], b=nodeById[e.target]; if(!a||!b) continue;
    const start=new THREE.Vector3(a.x,a.y,a.z);
    const end=new THREE.Vector3(b.x,b.y,b.z);
    const dir=end.clone().sub(start);
    const len=dir.length(); if(len<1e-3) continue;
    dir.normalize();
    const shaftLen = Math.max(0.1, len - 3.2);
    const mid = start.clone().add(dir.clone().multiplyScalar(shaftLen/2));
    const strength = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.xcorr||0)) - 0.08) / 0.25));
    const w = 0.35 + 0.9 * (forFocus ? (0.25 + 0.75*strength) : Math.min(1,(e.abs-0.15)/0.25));
    const col = e.sign>=0 ? cPos : cNeg;
    const bright = forFocus ? (0.35 + 0.65*strength) : 1;
    const shaftOp = (forFocus ? (0.25 + 0.7*strength) : 0.75) * bright;
    const headOp = (forFocus ? (0.35 + 0.65*strength) : 0.9) * bright;
    const shaft = new THREE.Mesh(
      new THREE.CylinderGeometry(0.12*w, 0.12*w, shaftLen, 6),
      new THREE.MeshBasicMaterial({{color:col, transparent:true, opacity: forFocus ? 0 : shaftOp}})
    );
    shaft.position.copy(mid);
    shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0), dir);
    shaft.userData = {{targetOpacity: shaftOp, baseScale: true}};
    leadGroup.add(shaft);
    const head = new THREE.Mesh(
      new THREE.ConeGeometry(0.55*w, 2.4, 8),
      new THREE.MeshBasicMaterial({{color:col, transparent:true, opacity: forFocus ? 0 : headOp}})
    );
    head.position.copy(start.clone().add(dir.clone().multiplyScalar(shaftLen + 1.0)));
    head.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0), dir);
    head.userData = {{targetOpacity: headOp, baseScale: true}};
    leadGroup.add(head);
  }}
}}
rebuildLeadArrows();

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
let mode = 'sync'; // sync | lead

function setMode(m){{
  mode = m;
  document.getElementById('mode-sync').classList.toggle('active', m==='sync');
  document.getElementById('mode-lead').classList.toggle('active', m==='lead');
  syncLines.visible = m==='sync' && focusIds.size===0;
  egoSyncLines.visible = m==='sync' && focusIds.size>0;
  leadGroup.visible = m==='lead';
  applyFocusVisual({{animateEdges:true}});
  if(coreIds.size) renderPanel([...coreIds], [...focusIds].filter(id=>!coreIds.has(id)));
  else renderPanel(null, []);
}}

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
  const nmap = mode==='lead' ? null : DATA.neighbors;
  if(mode==='lead'){{
    for(const id of cores){{
      for(const nb of (DATA.lead_out[id]||[])) one.add(nb.id);
      for(const nb of (DATA.lead_in[id]||[])) one.add(nb.id);
    }}
  }} else {{
    for(const id of cores) for(const nb of (nmap[id]||[])) one.add(nb.id);
    for(const id of one){{
      if(cores.includes(id)) continue;
      for(const nb of (nmap[id]||[])) if(!one.has(nb.id)) two.add(nb.id);
    }}
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

function applyFocusVisual(opts={{}}){{
  const animateEdges = !!(opts && opts.animateEdges);
  const colAttr=pointsGeo.getAttribute('color');
  const has=focusIds.size>0;
  for(let i=0;i<N;i++){{
    const id=DATA.nodes[i].id;
    let r=baseRGB[i][0], g=baseRGB[i][1], b=baseRGB[i][2];
    let spro=0.32, scale=5;
    if(has){{
      if(coreIds.has(id)){{ r=0.35; g=0.82; b=1; spro=0.95; scale=9.5; }}
      else if(focusIds.has(id)){{
        const strength = maxCorrToCores(id);
        const t = Math.min(1, Math.max(0, strength <= 0 ? 0.15 : (strength - 0.2) / 0.55));
        const boost = 0.35 + 0.65 * t;
        r = r * (0.55 + 0.45*boost) + 0.12*boost;
        g = g * (0.55 + 0.45*boost) + 0.18*boost;
        b = b * (0.55 + 0.45*boost) + 0.22*boost;
        spro = 0.18 + 0.72 * boost;
        scale = 4.2 + 4.0 * boost;
      }}
      else {{ r*=0.18; g*=0.20; b*=0.26; spro=0.03; scale=2.4; }}
    }}
    colAttr.setXYZ(i,r,g,b);
    sprites[i].material.opacity=spro;
    sprites[i].scale.set(scale,scale,1);
  }}
  colAttr.needsUpdate=true;

  if(!has){{
    cancelEdgeGrow();
    syncLines.visible = mode==='sync';
    syncLines.material.opacity = 0.34;
    egoSyncLines.visible = false;
    if(mode==='lead') rebuildLeadArrows(null, false);
  }} else {{
    // dim global sync mesh; ego edges get strength-scaled colors + grow anim
    syncLines.visible = false;
    egoSyncLines.visible = mode==='sync';
    if(mode==='sync') startEdgeGrow(animateEdges);
    else {{
      egoSyncLines.visible = false;
      startEdgeGrow(animateEdges);
    }}
  }}
}}

function fmtPct(x){{
  if(x==null || Number.isNaN(x)) return '-';
  return (x*100).toFixed(0)+'%';
}}
function fmtLift(x){{
  if(x==null || Number.isNaN(x)) return '-';
  const s=(x>=0?'+':'')+(x*100).toFixed(0)+'pt';
  return s;
}}

function renderPanel(cores, neighborIds){{
  const title=document.getElementById('p-title');
  const meta=document.getElementById('p-meta');
  const kv=document.getElementById('p-kv');
  const body=document.getElementById('p-body');
  if(!cores||!cores.length){{
    title.textContent='全局视图';
    if(mode==='lead'){{
      meta.textContent=`${{DATA.n_sectors}} 个二级 · 领先边 ${{(DATA.lead_edges||[]).length}} 条（周频 |xcorr|≥${{DATA.lead_thr}}，滞后1–${{DATA.max_lag}}周）`;
      body.innerHTML=`<div class="empty">金色/蓝色箭头：A → B 表示 A 领先 B。侧栏在聚焦后显示跟涨/跟跌条件概率相对基准的提升。</div>`;
    }} else {{
      meta.textContent=`${{DATA.n_sectors}} 个二级 · 同步边 ${{DATA.edges.length}} 条（|ρ|≥${{DATA.corr_thr}} 且多年复现）`;
      body.innerHTML=`<div class="empty">${{DATA.note}}</div>`;
    }}
    kv.innerHTML='';
    return;
  }}
  title.textContent = cores.length===1 ? cores[0] : `聚焦簇（${{cores.length}}）`;
  if(cores.length===1){{
    const n=nodeById[cores[0]];
    kv.innerHTML=`<b>一级</b><span>${{n.l1||'-'}}</span><b>成分</b><span>${{n.n||'-'}} 只</span><b>近20日</b><span class="${{(n.cum20||0)>=0?'pos':'neg'}}">${{n.cum20??'-'}}%</span><b>最新</b><span class="${{(n.last||0)>=0?'pos':'neg'}}">${{n.last??'-'}}%</span>`;
  }} else {{
    kv.innerHTML=cores.slice(0,10).map(id=>`<b>核心</b><span>${{id}}</span>`).join('');
  }}

  if(mode==='lead'){{
    meta.textContent = `传导模式 · 核心 ${{cores.length}} · 关联 ${{neighborIds.length}}`;
    const outs=[], inns=[], seenO=new Set(), seenI=new Set();
    for(const c of cores){{
      for(const nb of (DATA.lead_out[c]||[])){{
        if(coreIds.has(nb.id)||seenO.has(nb.id)) continue;
        seenO.add(nb.id); outs.push({{...nb, from:c}});
      }}
      for(const nb of (DATA.lead_in[c]||[])){{
        if(coreIds.has(nb.id)||seenI.has(nb.id)) continue;
        seenI.add(nb.id); inns.push({{...nb, to:c}});
      }}
    }}
    outs.sort((a,b)=>b.abs-a.abs); inns.sort((a,b)=>b.abs-a.abs);
    const rowOut = outs.slice(0,12).map(r=>`<li data-id="${{r.id}}"><span>${{r.id}}<span class="sub2">滞后 ${{r.lag}} 周 · xcorr ${{r.xcorr.toFixed(2)}} · 跟涨 ${{fmtPct(r.p_up)}}（${{fmtLift(r.lift_up)}}）· 跟跌 ${{fmtPct(r.p_dn)}}（${{fmtLift(r.lift_dn)}}）</span></span><span class="lead">→</span></li>`).join('');
    const rowIn = inns.slice(0,12).map(r=>`<li data-id="${{r.id}}"><span>${{r.id}}<span class="sub2">滞后 ${{r.lag}} 周 · xcorr ${{r.xcorr.toFixed(2)}} · 跟涨 ${{fmtPct(r.p_up)}}（${{fmtLift(r.lift_up)}}）· 跟跌 ${{fmtPct(r.p_dn)}}（${{fmtLift(r.lift_dn)}}）</span></span><span class="lead">←</span></li>`).join('');
    body.innerHTML = `
      <div class="sec">它领先谁（箭头指出）</div>
      ${{outs.length?`<ul class="list">${{rowOut}}</ul>`:`<div class="empty">暂无明显领先对象</div>`}}
      <div class="sec">谁领先它（箭头指入）</div>
      ${{inns.length?`<ul class="list">${{rowIn}}</ul>`:`<div class="empty">暂无明显领先来源</div>`}}
      <div class="empty" style="margin-top:8px">跟涨/跟跌 = 领先方残差处于自身高/低分位后，跟随方未来 ${{DATA.horizon}} 日累计涨/跌的条件概率；括号为相对无条件基准的提升。</div>`;
  }} else {{
    meta.textContent = `同步模式 · 核心 ${{cores.length}} · 一跳邻居 ${{neighborIds.length}}`;
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
  }}
  body.querySelectorAll('li').forEach(li => li.onclick=()=>{{ document.getElementById('q').value=li.dataset.id; setFocus([li.dataset.id]); }});
}}

function setFocus(cores){{
  coreIds=new Set(cores);
  if(!cores.length){{
    focusIds=new Set(); applyFocusVisual({{animateEdges:false}}); clearLabels(); renderPanel(null,[]); return;
  }}
  const {{one,two}}=egoOf(cores);
  focusIds=new Set([...one, ...two]);
  applyFocusVisual({{animateEdges:true}});
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
document.getElementById('mode-sync').onclick=()=>setMode('sync');
document.getElementById('mode-lead').onclick=()=>setMode('lead');
const presetsEl=document.getElementById('presets');
(DATA.presets||[]).forEach(p=>{{
  const b=document.createElement('button'); b.className='chip'; b.type='button'; b.textContent=p;
  b.onclick=()=>{{ [...presetsEl.children].forEach(x=>x.classList.remove('active')); b.classList.add('active'); document.getElementById('q').value=p; runQuery(); }};
  presetsEl.appendChild(b);
}});

const raycaster=new THREE.Raycaster(); raycaster.params.Points.threshold=2.8;
const mouse=new THREE.Vector2();
const CLICK_PX = 6;
let ptrDown = null;
let ptrDragged = false;
// Only movement threshold gates selection — OrbitControls autoRotate would fire 'change' every frame.
canvas.addEventListener('pointerdown', ev=>{{
  if(ev.button != null && ev.button !== 0) return;
  ptrDown = {{x: ev.clientX, y: ev.clientY}};
  ptrDragged = false;
}});
canvas.addEventListener('pointermove', ev=>{{
  if(!ptrDown) return;
  if(Math.hypot(ev.clientX - ptrDown.x, ev.clientY - ptrDown.y) > CLICK_PX) ptrDragged = true;
}});
function clearPtr(){{ ptrDown = null; ptrDragged = false; }}
canvas.addEventListener('pointercancel', clearPtr);
canvas.addEventListener('pointerup', ev=>{{
  if(!ptrDown) return;
  const moved = ptrDragged || Math.hypot(ev.clientX - ptrDown.x, ev.clientY - ptrDown.y) > CLICK_PX;
  clearPtr();
  if(moved) return;
  mouse.x=(ev.clientX/innerWidth)*2-1; mouse.y=-(ev.clientY/innerHeight)*2+1;
  raycaster.setFromCamera(mouse, camera);
  const hits=raycaster.intersectObject(points);
  if(hits.length){{ const id=DATA.nodes[hits[0].index].id; document.getElementById('q').value=id; setFocus([id]); }}
}});
window.addEventListener('resize', ()=>{{ camera.aspect=innerWidth/innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth,innerHeight); }});
renderPanel(null,[]);
(function tick(now){{ requestAnimationFrame(tick); tickEdgeGrow(now || performance.now()); controls.update(); renderer.render(scene,camera); }})();
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
        # refresh description if present
        return
    anchor = "sector_atlas.html"
    if anchor not in txt:
        return
    injection = (
        '("sector_corr_cloud.html", "🌌", "板块相关点云", '
        '"申万二级残差相关三维点云，支持同步/领先传导与查询聚焦", "板块跟踪"),\n            '
        f'("{anchor}"'
    )
    old = f'("{anchor}"'
    if old in txt:
        gen_index.write_text(txt.replace(old, injection, 1), encoding="utf-8")
        print("已注册 gen_index 入口")
        import subprocess
        subprocess.run([sys.executable, "-m", "scripts.reports.gen_index"], cwd=PROJECT_DIR, check=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-nav", action="store_true")
    ap.add_argument(
        "--from-cache",
        action="store_true",
        help="仅用 cache/sector_corr_cloud.json 重渲 HTML（不重算相关）",
    )
    args = ap.parse_args()

    if args.from_cache:
        if not OUTPUT_JSON.exists():
            raise SystemExit(f"缺少缓存: {OUTPUT_JSON}")
        payload = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
        OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
        print(f"从缓存重渲 {OUTPUT_HTML}（节点 {payload.get('n_sectors')} · 边 {len(payload.get('edges', []))}）")
        if not args.no_nav:
            update_nav()
        return

    print("加载 2022→今 mom1，聚合申万二级…")
    piv, meta = load_sw2_returns()
    print(f"  板块 {piv.shape[1]} · 交易日 {piv.shape[0]} · 截止 {piv.index.max().date()}")
    mkt = market_proxy(piv)
    print("去市场 beta，MDS + 同步边…")
    resid = residualize(piv, mkt)
    payload = pack_payload(piv, resid, meta)
    print(f"  节点 {payload['n_sectors']} · 同步边 {len(payload['edges'])} · 领先边 {len(payload['lead_edges'])}")

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

    # sample lead edges for smoke
    leads = payload["lead_edges"][:5]
    for e in leads:
        print(f"  领先样例 {e['source']} → {e['target']} lag={e['lag']} xcorr={e['xcorr']} lift_up={e['lift_up']} lift_dn={e['lift_dn']}")

    if not args.no_nav:
        update_nav()
        # refresh index to pick up any title tweaks
        import subprocess
        subprocess.run([sys.executable, "-m", "scripts.reports.gen_index"], cwd=PROJECT_DIR, check=False)


if __name__ == "__main__":
    main()
