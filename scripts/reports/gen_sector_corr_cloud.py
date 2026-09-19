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


def _bare_code(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c[len(p):]
    return c


def _to_tx_code(code: str) -> str:
    c = str(code).strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("0", "1", "2", "3")):
        return "sz" + c
    return c


def _fetch_tencent_pct(codes: list[str]) -> dict[str, float]:
    """Batch-fetch latest day % change via Tencent quote API. keys = bare 6-digit."""
    import re as _re
    import time
    import requests

    out: dict[str, float] = {}
    tx = [_to_tx_code(c) for c in codes]
    batch = 80
    for i in range(0, len(tx), batch):
        chunk = tx[i : i + batch]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            r = requests.get(url, timeout=15)
            r.encoding = "gbk"
            for part in r.text.strip().split(";"):
                part = part.strip()
                if not part or "~" not in part:
                    continue
                m = _re.match(r'v_([^=]+)="([^"]*)"', part)
                if not m:
                    continue
                key, payload = m.group(1), m.group(2)
                fields = payload.split("~")
                if len(fields) < 33:
                    continue
                try:
                    out[_bare_code(key)] = float(fields[32])
                except Exception:
                    continue
        except Exception as exc:
            print(f"  行情批次失败 @{i}: {exc}")
        if i and i % 800 == 0:
            time.sleep(0.12)
    return out


def _stock_last_from_mom1(codes: list[str]) -> dict[str, float]:
    """Prefer mom1 last row when local parquet exists."""
    if not MOM1.exists():
        return {}
    try:
        mom = pd.read_parquet(MOM1)
        if mom.empty:
            return {}
        last = mom.ffill().iloc[-1]
        out = {}
        for c in codes:
            bare = _bare_code(c)
            for key in (c, bare, f"sh{bare}", f"sz{bare}", f"bj{bare}"):
                if key in last.index and pd.notna(last[key]):
                    out[bare] = float(last[key])
                    break
        return out
    except Exception as exc:
        print(f"  读取 mom1 成分涨跌失败: {exc}")
        return {}


def attach_stock_payload(payload: dict) -> dict:
    """Attach stock_index + sector_members for SW2 nodes present in the cloud."""
    sector_ids = {n["id"] for n in payload.get("nodes", [])}
    try:
        info = StockInfo().df.copy()
    except Exception as exc:
        print(f"  StockInfo 不可用，跳过股票索引: {exc}")
        payload.setdefault("stock_index", [])
        payload.setdefault("sector_members", {})
        return payload

    # tolerate both 申万2级 / 申万二级 naming if ever aliased
    sw2_col = next((c for c in ("申万2级", "申万二级", "sw_l2") if c in info.columns), None)
    if sw2_col is None or "代码" not in info.columns:
        print(f"  StockInfo 缺申万2级/代码列 cols={list(info.columns)}")
        payload.setdefault("stock_index", [])
        payload.setdefault("sector_members", {})
        return payload

    info = info.dropna(subset=[sw2_col])
    info = info[info[sw2_col].astype(str).isin(sector_ids)]
    if info.empty:
        print("  无与点云重叠的成分股")
        payload["stock_index"] = []
        payload["sector_members"] = {}
        return payload

    codes = info["代码"].astype(str).tolist()
    last_map = _stock_last_from_mom1(codes)
    missing = [_bare_code(c) for c in codes if _bare_code(c) not in last_map]
    if missing:
        print(f"  拉取腾讯行情补齐成分涨跌 {len(missing)} / {len(codes)} …")
        # map bare->original for tx fetch
        bare_to_raw = {}
        for c in codes:
            bare_to_raw.setdefault(_bare_code(c), c)
        fetched = _fetch_tencent_pct([bare_to_raw[b] for b in missing if b in bare_to_raw])
        last_map.update(fetched)
    print(f"  成分涨跌覆盖 {len(last_map)} / {len(set(_bare_code(c) for c in codes))}")

    stock_index = []
    sector_members: dict[str, list] = {s: [] for s in sector_ids}
    seen = set()
    name_col = "名称" if "名称" in info.columns else None
    for _, row in info.iterrows():
        code = _bare_code(row["代码"])
        name = str(row[name_col]) if name_col else code
        sector = str(row[sw2_col])
        key = (code, sector)
        if key in seen:
            continue
        seen.add(key)
        stock_index.append({"code": code, "name": name, "sector": sector})
        last = last_map.get(code)
        sector_members[sector].append({
            "code": code,
            "name": name,
            "last": None if last is None else round(float(last), 3),
        })

    for s, rows in list(sector_members.items()):
        rows.sort(key=lambda r: (r["last"] is None, -(r["last"] or 0.0)))
        sector_members[s] = rows[:80]
    sector_members = {k: v for k, v in sector_members.items() if v}

    payload["stock_index"] = stock_index
    payload["sector_members"] = sector_members
    print(f"  stock_index={len(stock_index)} · sector_members={len(sector_members)}")
    return payload


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

    payload = {
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
    print("附加股票索引与成分涨跌…")
    return attach_stock_payload(payload)


def render_html(payload: dict) -> str:
    """Embed Three.js page; payload JSON injected via token replace (no f-string brace hell)."""
    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return (
        HTML_TEMPLATE
        .replace("__DATA_JSON__", data_json)
        .replace("__START__", str(payload.get("start", "")))
        .replace("__END__", str(payload.get("end", "")))
        .replace("__N_SECTORS__", str(payload.get("n_sectors", "")))
        .replace("__CORR_THR__", str(payload.get("corr_thr", "")))
        .replace("__LEAD_THR__", str(payload.get("lead_thr", "")))
        .replace("__HORIZON__", str(payload.get("horizon", "")))
        .replace("__GENERATED_AT__", str(payload.get("generated_at", "")))
    )


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>申万二级 · 相关点云</title>
<style>
:root{--bg:#070b14;--panel:rgba(12,18,32,.88);--line:rgba(120,160,255,.22);--text:#e8eefc;--muted:#8b9bb8;--accent:#5ad1ff;--hot:#ff7a7a;--ok:#3ddc97;--lead:#ffd166;--rail:48px;--fly:min(340px,86vw)}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif;overflow:hidden}
#c{position:fixed;inset:0;display:block;z-index:0}
.rail{position:fixed;left:8px;top:50%;transform:translateY(-50%);width:var(--rail);padding:8px 0;display:flex;flex-direction:column;align-items:center;gap:6px;border-radius:16px;background:var(--panel);border:1px solid var(--line);backdrop-filter:blur(14px);box-shadow:0 10px 36px rgba(0,0,0,.45);z-index:6;pointer-events:auto}
.rail-btn{width:36px;height:36px;border:0;border-radius:12px;background:transparent;color:var(--muted);font-size:15px;font-weight:700;cursor:pointer;letter-spacing:.02em;transition:background .15s,color .15s,box-shadow .15s}
.rail-btn:hover{color:var(--text);background:rgba(90,209,255,.08)}
.rail-btn.active{color:var(--accent);background:rgba(90,209,255,.16);box-shadow:inset 0 0 0 1px rgba(90,209,255,.45)}
.rail-tip{position:fixed;left:64px;bottom:18px;z-index:7;pointer-events:none;font-size:12px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 12px;backdrop-filter:blur(10px);opacity:0;transform:translateY(6px);transition:opacity .25s,transform .25s;max-width:min(280px,70vw)}
.rail-tip.show{opacity:1;transform:none}
.flyout{position:fixed;left:56px;top:12px;width:var(--fly);max-height:calc(100vh - 24px);display:none;flex-direction:column;border-radius:16px;background:var(--panel);border:1px solid var(--line);backdrop-filter:blur(14px);box-shadow:0 12px 40px rgba(0,0,0,.5);z-index:6;pointer-events:auto;overflow:hidden}
.flyout.open{display:flex}
.fly-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 14px 8px;border-bottom:1px solid rgba(255,255,255,.06);flex-shrink:0}
.fly-hd h3{margin:0;font-size:14px;font-weight:650}
.fly-close{width:32px;height:32px;border-radius:10px;background:#132038;color:var(--muted);border:1px solid var(--line);cursor:pointer;font-size:16px;line-height:1}
.fly-close:hover{color:var(--accent);border-color:var(--accent)}
.fly-body{padding:10px 14px 14px;overflow:auto;flex:1;min-height:0}
.fly-pane{display:none}
.fly-pane.active{display:block}
.title{font-size:15px;font-weight:650}
.sub{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.45}
.search{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}
.search input{flex:1;min-width:120px;background:#0b1220;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:10px 12px;font-size:14px;outline:none}
.search input:focus{border-color:var(--accent)}
button,.chip{background:#132038;color:var(--text);border:1px solid var(--line);border-radius:999px;padding:8px 12px;font-size:12px;cursor:pointer}
button:hover,.chip:hover,.chip.active,.seg button.active{border-color:var(--accent);color:var(--accent)}
.seg{display:inline-flex;gap:2px;padding:2px;background:#0b1220;border-radius:999px;border:1px solid var(--line)}
.seg button{border:0;background:transparent;padding:7px 11px}
.seg button.active{background:rgba(90,209,255,.14)}
.row{display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.hints{margin-top:8px;max-height:110px;overflow:auto;font-size:12px}
.hints div{padding:5px 2px;border-bottom:1px solid rgba(255,255,255,.06);cursor:pointer;color:var(--muted)}
.hints div:hover,.hints .on{color:var(--accent)}
.hints b{color:var(--text);font-weight:560;margin-right:6px}
.meta{font-size:12px;color:var(--muted);margin-bottom:10px;line-height:1.4}
.kv{display:grid;grid-template-columns:72px 1fr;gap:4px 8px;font-size:12px;margin-bottom:10px}
.kv b{color:var(--muted);font-weight:500}
.sec{font-size:12px;color:var(--muted);margin:12px 0 6px;display:flex;justify-content:space-between;gap:8px}
.list{list-style:none;padding:0;margin:0}
.list li{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid rgba(255,255,255,.06);font-size:12px;cursor:pointer}
.list li:hover,.list li.on{color:var(--accent)}
.list .sub2{display:block;color:var(--muted);font-size:11px;margin-top:2px}
.pos{color:var(--hot)}.neg{color:var(--ok)}.leadc{color:var(--lead)}
.board-hd{font-size:12px;color:var(--muted);margin-bottom:6px;display:flex;justify-content:space-between}
.members{max-height:220px;overflow:auto}
.foot{position:fixed;bottom:14px;left:64px;font-size:11px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 10px;max-width:min(720px,calc(100vw - 80px));z-index:4;pointer-events:none;backdrop-filter:blur(10px)}
.empty{color:var(--muted);font-size:12px;line-height:1.5}
@media(max-width:900px){
  .rail{left:50%;right:auto;top:auto;bottom:calc(10px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);flex-direction:row;width:auto;padding:6px 10px;border-radius:999px;gap:4px}
  .rail-btn{width:40px;height:40px;border-radius:999px;font-size:14px}
  .flyout{left:10px;right:10px;top:auto;bottom:calc(64px + env(safe-area-inset-bottom,0px));width:auto;max-height:42vh;border-radius:16px 16px 14px 14px}
  .rail-tip{left:50%;bottom:calc(72px + env(safe-area-inset-bottom,0px));transform:translateX(-50%) translateY(6px);text-align:center}
  .rail-tip.show{transform:translateX(-50%)}
  .foot{display:none}
}
</style>
</head>
<body>

<canvas id="c"></canvas>

<nav class="rail" id="dockRail" aria-label="分析轨道">
  <button type="button" class="rail-btn active" data-mode="ctrl" id="railCtrl" title="搜索与筛选">控</button>
  <button type="button" class="rail-btn" data-mode="board" id="railBoard" title="涨跌榜">榜</button>
  <button type="button" class="rail-btn" data-mode="detail" id="railDetail" title="聚焦详情">详</button>
</nav>
<div class="rail-tip" id="zenTip">再点轨道图标可收起面板</div>

<aside class="flyout open" id="flyout" data-mode="ctrl">
  <div class="fly-hd">
    <h3 id="flyTitle">搜索与筛选</h3>
    <button type="button" class="fly-close" id="flyClose" aria-label="收起面板">×</button>
  </div>
  <div class="fly-body">
    <div class="fly-pane active" id="paneCtrl" data-pane="ctrl">
      <div class="title">申万二级 · 相关点云</div>
      <div class="sub">去市场 beta 后的残差结构。点颜色/大小=涨跌（红涨绿跌）；连线为持续流动光流。查询可切换「板块 / 标的」。领先传导只显示当前中心板块的最强连接，每个方向保留前3条。当前点云是关系观察，不代表历史时点信号。</div>
      <div class="search">
        <div class="seg" id="qmode">
          <button type="button" class="active" data-v="sector">板块</button>
          <button type="button" data-v="stock">标的</button>
        </div>
        <input id="q" placeholder="查询板块，如：电力、半导体、白酒…" autocomplete="off"/>
        <button id="go" type="button">聚焦</button>
        <button id="reset" type="button">全局</button>
      </div>
      <div class="hints" id="hints"></div>
      <div class="row">
        <div class="seg" id="retmode">
          <button type="button" class="active" data-v="last">当日</button>
          <button type="button" data-v="cum20">近20日</button>
        </div>
        <div class="seg" id="edgemode">
          <button type="button" class="active" data-v="sync">同步相关</button>
          <button type="button" data-v="lead">领先传导</button>
        </div>
      </div>
      <div class="chips" id="presets"></div>
    </div>
    <div class="fly-pane" id="paneBoard" data-pane="board">
      <div class="board-hd"><span>按当前收益窗口排序</span><span id="boardLabel">当日</span></div>
      <ul class="list" id="boardList"></ul>
    </div>
    <div class="fly-pane" id="paneDetail" data-pane="detail">
      <h3 id="pTitle">全局视图</h3>
      <div class="meta" id="pMeta"></div>
      <div class="kv" id="pKv"></div>
      <div id="pBody" class="empty">输入关键词自动扩展并高亮该簇；或点击点云中的板块。</div>
      <div class="sec" id="memSec" style="display:none"><span>成分股（按当日涨跌）</span><span id="memCount"></span></div>
      <ul class="list members" id="memList"></ul>
    </div>
  </div>
</aside>

<div class="foot">数据 __START__ → __END__ · __N_SECTORS__ 二级 · 同步边 |ρ|≥__CORR_THR__ · 领先周频 |xcorr|≥__LEAD_THR__ · 跟涨观察 __HORIZON__ 日 · 生成 __GENERATED_AT__</div>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA = __DATA_JSON__;
const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.setClearColor(0x070b14, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x070b14, 0.0018);
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 2000);
camera.position.set(0, 45, 145);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.55;
controls.minDistance = 35;
controls.maxDistance = 380;
scene.add(new THREE.AmbientLight(0x9eb7ff, 0.8));
const keyLight = new THREE.PointLight(0x5ad1ff, 1.15, 700); keyLight.position.set(80, 110, 70); scene.add(keyLight);
const fillLight = new THREE.PointLight(0xff6b6b, 0.32, 700); fillLight.position.set(-90, -30, -80); scene.add(fillLight);

{
  const n = 900, pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    pos[i * 3] = (Math.random() - 0.5) * 520;
    pos[i * 3 + 1] = (Math.random() - 0.5) * 520;
    pos[i * 3 + 2] = (Math.random() - 0.5) * 520;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0x243552, size: 0.55, transparent: true, opacity: 0.5 })));
}

const nodeById = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const N = DATA.nodes.length;
const positions = new Float32Array(N * 3);
const colors = new Float32Array(N * 3);
for (let i = 0; i < N; i++) {
  const n = DATA.nodes[i];
  positions[i * 3] = n.x; positions[i * 3 + 1] = n.y; positions[i * 3 + 2] = n.z;
  colors[i * 3] = 0.5; colors[i * 3 + 1] = 0.55; colors[i * 3 + 2] = 0.65;
}
const pointsGeo = new THREE.BufferGeometry();
pointsGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
pointsGeo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
const pointsMat = new THREE.PointsMaterial({ size: 9.5, vertexColors: true, transparent: true, opacity: 1, sizeAttenuation: true, depthWrite: false });
const points = new THREE.Points(pointsGeo, pointsMat); scene.add(points);

const glowTex = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(32, 32, 2, 32, 32, 30);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.4, 'rgba(255,255,255,.45)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
})();
const sprites = [];
for (let i = 0; i < N; i++) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: 0x88aacc, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.3 }));
  s.position.set(DATA.nodes[i].x, DATA.nodes[i].y, DATA.nodes[i].z);
  s.scale.set(10, 10, 1); scene.add(s); sprites.push(s);
}

let retWindow = 'last';
let qMode = 'sector';
let mode = 'sync';
let coreIds = new Set();
let focusIds = new Set();
const LEAD_FOCUSED_TOTAL_LIMIT = 12;
const LEAD_PER_CORE_DIRECTION = 3;
const LEAD_PANEL_LIMIT = 6;

// xcorr 是传导强度主排序，事件概率提升用于同强度边的次级区分。
function leadStrength(edge) {
  const lift = Math.max(Math.abs(Number(edge.lift_up) || 0), Math.abs(Number(edge.lift_dn) || 0));
  return (Number(edge.abs) || Math.abs(Number(edge.xcorr) || 0)) + Math.min(0.2, lift) * 0.35;
}
function sortLeadEdges(edges) {
  return [...edges].sort((a, b) => leadStrength(b) - leadStrength(a) || (Number(b.abs) || 0) - (Number(a.abs) || 0));
}
function visibleLeadEdges(filterIds = null) {
  const all = DATA.lead_edges || [];
  // 领先传导必须围绕当前中心板块解释；全局视图不绘制无关连线。
  if (!filterIds || !filterIds.size) return [];
  const picked = new Map();
  for (const core of filterIds) {
    const outgoing = sortLeadEdges(all.filter(edge => edge.source === core)).slice(0, LEAD_PER_CORE_DIRECTION);
    const incoming = sortLeadEdges(all.filter(edge => edge.target === core)).slice(0, LEAD_PER_CORE_DIRECTION);
    for (const edge of [...outgoing, ...incoming]) picked.set(`${edge.source}|${edge.target}`, edge);
  }
  return sortLeadEdges([...picked.values()]).slice(0, LEAD_FOCUSED_TOTAL_LIMIT);
}

function retOf(n) {
  const v = retWindow === 'cum20' ? (n.cum20 ?? n.cum_20 ?? n.ret20) : (n.last ?? n.ret1 ?? n.day);
  return (v == null || Number.isNaN(+v)) ? null : +v;
}
function retScale() {
  let m = 1e-6;
  for (const n of DATA.nodes) { const v = retOf(n); if (v != null) m = Math.max(m, Math.abs(v)); }
  return Math.max(1.5, m * 0.85);
}
function colorFromRet(v, dim = 1) {
  const c = new THREE.Color();
  if (v == null) { c.setRGB(0.35 * dim, 0.4 * dim, 0.48 * dim); return c; }
  const t = Math.max(-1, Math.min(1, v / retScale()));
  if (t >= 0) c.setRGB((0.45 + 0.55 * t) * dim, (0.18 + 0.05 * (1 - t)) * dim, (0.18 + 0.05 * (1 - t)) * dim);
  else { const u = -t; c.setRGB((0.12 + 0.05 * (1 - u)) * dim, (0.45 + 0.5 * u) * dim, (0.32 + 0.2 * u) * dim); }
  return c;
}
function sizeFromRet(v) {
  if (v == null) return 3.2;
  return 5.5 + 10.0 * Math.min(1, Math.abs(v) / retScale());
}
function maxCorrToCores(nodeId) {
  let best = 0;
  for (const c of coreIds) {
    if (c === nodeId) continue;
    for (const nb of (DATA.neighbors[c] || [])) if (nb.id === nodeId) best = Math.max(best, nb.abs ?? Math.abs(nb.corr || 0));
    for (const nb of (DATA.neighbors[nodeId] || [])) if (nb.id === c) best = Math.max(best, nb.abs ?? Math.abs(nb.corr || 0));
  }
  for (const e of DATA.edges) {
    if ((coreIds.has(e.source) && e.target === nodeId) || (coreIds.has(e.target) && e.source === nodeId))
      best = Math.max(best, e.abs ?? Math.abs(e.corr || 0));
  }
  if (mode === 'lead') {
    for (const c of coreIds) {
      for (const nb of (DATA.lead_out[c] || [])) if (nb.id === nodeId) best = Math.max(best, nb.abs ?? Math.abs(nb.xcorr || 0));
      for (const nb of (DATA.lead_in[c] || [])) if (nb.id === nodeId) best = Math.max(best, nb.abs ?? Math.abs(nb.xcorr || 0));
    }
  }
  return best;
}
function applyNodeAppearance() {
  const colAttr = pointsGeo.getAttribute('color');
  const has = focusIds.size > 0;
  for (let i = 0; i < N; i++) {
    const n = DATA.nodes[i], id = n.id, v = retOf(n);
    let dim = 1, sizeMul = 1, spro = 0.28, scale = sizeFromRet(v) * 1.35;
    if (has) {
      if (coreIds.has(id)) { dim = 1; sizeMul = 1.55; spro = 0.95; scale *= 1.4; }
      else if (focusIds.has(id)) {
        const s = maxCorrToCores(id);
        const t = Math.min(1, Math.max(0, s <= 0 ? 0.2 : (s - 0.2) / 0.55));
        dim = 0.35 + 0.65 * t; sizeMul = 0.85 + 0.55 * t; spro = 0.16 + 0.7 * t; scale *= sizeMul;
      } else { dim = 0.16; sizeMul = 0.55; spro = 0.03; scale = 2.1; }
    }
    const c = colorFromRet(v, dim);
    colAttr.setXYZ(i, c.r, c.g, c.b);
    pointsMat.size = 3.6; // global base; per-point via sprite scale primarily
    sprites[i].material.color.copy(c);
    sprites[i].material.opacity = spro;
    sprites[i].scale.set(scale * sizeMul, scale * sizeMul, 1);
  }
  colAttr.needsUpdate = true;
  // also encode size via points material size isn't per-vertex with PointsMaterial —
  // approximate by keeping color/glow; optionally rebuild later.
}

const syncLines = (() => {
  const edgePos = [], edgeCol = [];
  const cPos = new THREE.Color(0xff7a7a), cNeg = new THREE.Color(0x3ddc97);
  for (const e of DATA.edges) {
    const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
    edgePos.push(a.x, a.y, a.z, b.x, b.y, b.z);
    const col = e.sign >= 0 ? cPos : cNeg;
    const w = 0.18 + 0.55 * Math.min(1, (e.abs - 0.35) / 0.4);
    edgeCol.push(col.r * w, col.g * w, col.b * w, col.r * w, col.g * w, col.b * w);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(edgePos, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(edgeCol, 3));
  return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.28, depthWrite: false }));
})();
scene.add(syncLines);

const leadGroup = new THREE.Group(); scene.add(leadGroup); leadGroup.visible = false;
function rebuildLeadArrows(filterIds = null) {
  while (leadGroup.children.length) { const ch = leadGroup.children.pop(); ch.geometry?.dispose?.(); ch.material?.dispose?.(); }
  const cPos = new THREE.Color(0xffd166), cNeg = new THREE.Color(0x7aa2ff);
  for (const e of visibleLeadEdges(filterIds)) {
    if (filterIds && filterIds.size && !filterIds.has(e.source) && !filterIds.has(e.target)) continue;
    const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
    const start = new THREE.Vector3(a.x, a.y, a.z), end = new THREE.Vector3(b.x, b.y, b.z);
    const dir = end.clone().sub(start); const len = dir.length(); if (len < 1e-3) continue; dir.normalize();
    const shaftLen = Math.max(0.1, len - 3.2);
    const mid = start.clone().add(dir.clone().multiplyScalar(shaftLen / 2));
    const strength = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.xcorr || 0)) - 0.08) / 0.25));
    const w = 0.35 + 0.9 * (filterIds ? (0.25 + 0.75 * strength) : Math.min(1, (e.abs - 0.15) / 0.25));
    const col = e.sign >= 0 ? cPos : cNeg;
    const shaftOp = filterIds ? (0.25 + 0.7 * strength) : 0.55;
    const headOp = filterIds ? (0.35 + 0.65 * strength) : 0.7;
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.1 * w, 0.1 * w, shaftLen, 6), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: shaftOp }));
    shaft.position.copy(mid); shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir); leadGroup.add(shaft);
    const head = new THREE.Mesh(new THREE.ConeGeometry(0.5 * w, 2.2, 8), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: headOp }));
    head.position.copy(start.clone().add(dir.clone().multiplyScalar(shaftLen + 1.0)));
    head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir); leadGroup.add(head);
  }
}
rebuildLeadArrows();

const flowGroup = new THREE.Group(); scene.add(flowGroup);
const flowMats = [];
function clearFlow() {
  while (flowGroup.children.length) {
    const ch = flowGroup.children.pop();
    ch.geometry?.dispose?.();
    if (ch.material && !flowMats.includes(ch.material)) ch.material.dispose?.();
  }
  for (const m of flowMats) m.dispose();
  flowMats.length = 0;
}
function addFlow(ax, ay, az, bx, by, bz, color, opacity) {
  const segs = 14, pos = [];
  for (let i = 0; i <= segs; i++) { const t = i / segs; pos.push(ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t); }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  const mat = new THREE.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 2.2, gapSize: 1.35, depthWrite: false });
  flowMats.push(mat);
  const line = new THREE.Line(geo, mat); line.computeLineDistances(); flowGroup.add(line);
  const ppos = []; for (let i = 0; i < 7; i++) { const t = (i + 0.35) / 7; ppos.push(ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t); }
  const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.Float32BufferAttribute(ppos, 3));
  const pts = new THREE.Points(pg, new THREE.PointsMaterial({ color, size: 1.7, transparent: true, opacity: opacity * 0.9, depthWrite: false, blending: THREE.AdditiveBlending }));
  pts.userData = { ax, ay, az, bx, by, bz, n: 7, phase: Math.random() };
  flowGroup.add(pts);
}
function rebuildFlow() {
  clearFlow();
  if (!focusIds.size) {
    if (mode === 'sync') {
      const top = [...DATA.edges].sort((a, b) => b.abs - a.abs).slice(0, 36);
      for (const e of top) {
        const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
        addFlow(a.x, a.y, a.z, b.x, b.y, b.z, e.sign >= 0 ? 0xff7a7a : 0x3ddc97, 0.2 + 0.22 * Math.min(1, (e.abs - 0.4) / 0.4));
      }
    }
    return;
  }
  if (mode === 'sync') {
    const seen = new Set();
    for (const e of DATA.edges) {
      if (!(focusIds.has(e.source) && focusIds.has(e.target))) continue;
      if (!(coreIds.has(e.source) || coreIds.has(e.target))) continue;
      const key = e.source < e.target ? e.source + '|' + e.target : e.target + '|' + e.source;
      if (seen.has(key)) continue; seen.add(key);
      let a = nodeById[e.source], b = nodeById[e.target];
      if (coreIds.has(e.target) && !coreIds.has(e.source)) { const t = a; a = b; b = t; }
      const s = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.corr || 0)) - 0.35) / 0.45));
      addFlow(a.x, a.y, a.z, b.x, b.y, b.z, e.sign >= 0 ? 0xff8a8a : 0x3ddc97, 0.45 + 0.5 * s);
    }
  } else {
    for (const e of visibleLeadEdges(coreIds)) {
      if (!(coreIds.has(e.source) || coreIds.has(e.target))) continue;
      if (!(focusIds.has(e.source) || focusIds.has(e.target))) continue;
      const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
      const s = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.xcorr || 0)) - 0.08) / 0.25));
      addFlow(a.x, a.y, a.z, b.x, b.y, b.z, e.sign >= 0 ? 0xffd166 : 0x7aa2ff, 0.4 + 0.55 * s);
    }
  }
}

const labelGroup = new THREE.Group(); scene.add(labelGroup);
function makeLabel(text, color = '#e8eefc') {
  const c = document.createElement('canvas'); const ctx = c.getContext('2d');
  ctx.font = '600 28px sans-serif'; const w = Math.ceil(ctx.measureText(text).width) + 28;
  c.width = w; c.height = 48; ctx.font = '600 28px sans-serif';
  ctx.fillStyle = 'rgba(8,12,22,0.72)';
  ctx.beginPath(); const r = 12; ctx.moveTo(r, 4); ctx.arcTo(w, 4, w, 44, r); ctx.arcTo(w, 44, 0, 44, r); ctx.arcTo(0, 44, 0, 4, r); ctx.arcTo(0, 4, w, 4, r); ctx.closePath(); ctx.fill();
  ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.fillText(text, 14, 26);
  const tex = new THREE.CanvasTexture(c); tex.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  sp.scale.set(w / 10, 4.8, 1); return sp;
}
function clearLabels() { while (labelGroup.children.length) { const ch = labelGroup.children.pop(); ch.material.map?.dispose(); ch.material.dispose(); } }
function rebuildLabels(cores, neighbors) {
  clearLabels();
  const show = [...new Set([...(cores || []), ...(neighbors || [])])].slice(0, 22);
  for (const id of show) {
    const n = nodeById[id]; if (!n) continue;
    const lab = makeLabel(n.name, coreIds.has(id) ? '#5ad1ff' : '#e8eefc');
    lab.position.set(n.x, n.y + 4.5, n.z); labelGroup.add(lab);
  }
}

function expandQuery(q) {
  q = (q || '').trim(); if (!q) return [];
  const hit = new Set();
  const rules = DATA.expand_rules || {};
  let keys = Object.keys(rules).filter(k => q.includes(k) || k.includes(q));
  if (keys.length) { const m = Math.max(...keys.map(k => k.length)); keys = keys.filter(k => k.length === m); }
  for (const k of keys) {
    const rule = rules[k] || {};
    for (const ex of (rule.exact || [])) if (nodeById[ex]) hit.add(ex);
    for (const sub of (rule.contains || [])) for (const n of DATA.nodes) if (n.name.includes(sub)) hit.add(n.id);
  }
  for (const n of DATA.nodes) {
    if (n.name.includes(q) || q.includes(n.name)) hit.add(n.id);
    if ((n.l1 || '').includes(q)) hit.add(n.id);
  }
  return [...hit];
}
function searchStocks(q) {
  q = (q || '').trim().toLowerCase(); if (!q) return [];
  const out = [];
  for (const s of (DATA.stock_index || [])) {
    const code = (s.code || '').toLowerCase(), name = (s.name || '').toLowerCase();
    if (code.includes(q) || name.includes(q) || q.includes(code)) { out.push(s); if (out.length >= 12) break; }
  }
  return out;
}
function applyDeepLink() {
  const params = new URLSearchParams(location.search);
  const stock = (params.get('stock') || params.get('code') || '').trim();
  if (!stock) return;
  const hit = searchStocks(stock)[0];
  qMode = 'stock';
  document.querySelectorAll('#qmode button').forEach(b => b.classList.toggle('active', b.dataset.v === 'stock'));
  const input = document.getElementById('q');
  input.placeholder = '输入股票名称或代码…';
  input.value = stock;
  if (!hit) {
    updateHints();
    document.getElementById('pBody').innerHTML = '<div class="empty">点云索引中暂未找到该标的，仍可手动查询板块。</div>';
    return;
  }
  setFocus([hit.sector]);
  const source = params.get('from');
  const title = document.getElementById('pTitle');
  if (title) title.textContent = `${hit.name} · ${hit.sector}`;
  const meta = document.getElementById('pMeta');
  if (meta) meta.textContent = `${source === 'shortlist' ? '来自短名单 · ' : source === 'symbol' ? '来自标的上下文 · ' : ''}已聚焦所属申万二级板块`;
}
function egoOf(cores) {
  const one = new Set(cores), two = new Set();
  if (mode === 'lead') {
    for (const id of cores) {
      for (const nb of (DATA.lead_out[id] || [])) one.add(nb.id);
      for (const nb of (DATA.lead_in[id] || [])) one.add(nb.id);
    }
  } else {
    for (const id of cores) for (const nb of (DATA.neighbors[id] || [])) one.add(nb.id);
    for (const id of one) {
      if (cores.includes(id)) continue;
      for (const nb of (DATA.neighbors[id] || [])) if (!one.has(nb.id)) two.add(nb.id);
    }
  }
  return { one, two };
}

function applyFocusVisual() {
  applyNodeAppearance();
  const has = focusIds.size > 0;
  syncLines.visible = mode === 'sync' && !has;
  leadGroup.visible = mode === 'lead';
  if (mode === 'lead') rebuildLeadArrows(has ? coreIds : null);
  rebuildFlow();
  renderBoard();
}

function fmtPct(x) { if (x == null || Number.isNaN(x)) return '-'; return (x * 100).toFixed(0) + '%'; }
function fmtLift(x) { if (x == null || Number.isNaN(x)) return '-'; return (x >= 0 ? '+' : '') + (x * 100).toFixed(0) + 'pt'; }
function fmtRet(x) { if (x == null || Number.isNaN(+x)) return '-'; const v = +x; return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }

function renderMembers(sectorId) {
  const sec = document.getElementById('memSec');
  const list = document.getElementById('memList');
  const cnt = document.getElementById('memCount');
  if (!sectorId) { sec.style.display = 'none'; list.innerHTML = ''; return; }
  const rows = (DATA.sector_members && DATA.sector_members[sectorId]) || [];
  sec.style.display = 'flex';
  cnt.textContent = rows.length ? `${rows.length} 只` : '';
  if (!rows.length) { list.innerHTML = `<div class="empty">暂无成分股明细</div>`; return; }
  list.innerHTML = rows.map(r => {
    const cls = r.last == null ? '' : (r.last >= 0 ? 'pos' : 'neg');
    return `<li><span>${r.name}<span class="sub2">${r.code}</span></span><span class="${cls}">${fmtRet(r.last)}</span></li>`;
  }).join('');
}

function renderPanel(cores, neighborIds) {
  const title = document.getElementById('pTitle');
  const meta = document.getElementById('pMeta');
  const kv = document.getElementById('pKv');
  const body = document.getElementById('pBody');
  if (!meta || !kv || !body) return;
  if (!cores || !cores.length) {
    if (title) title.textContent = '全局视图';
    if (mode === 'lead') {
      meta.textContent = `${DATA.n_sectors} 个二级 · 尚未聚焦中心板块 · 共 ${(DATA.lead_edges || []).length} 条候选领先边`;
      body.innerHTML = `<div class="empty">请先查询或点击一个板块。聚焦后只显示该中心板块最强的入向与出向连接，避免无关连线干扰。</div>`;
    } else {
      meta.textContent = `${DATA.n_sectors} 个二级 · 同步边 ${DATA.edges.length} 条（|ρ|≥${DATA.corr_thr} 且多年复现）`;
      body.innerHTML = `<div class="empty">${DATA.note || ''}</div>`;
    }
    kv.innerHTML = ''; renderMembers(null); return;
  }
  if (title) title.textContent = cores.length === 1 ? cores[0] : `聚焦簇（${cores.length}）`;
  if (cores.length === 1) {
    const n = nodeById[cores[0]];
    kv.innerHTML = `<b>一级</b><span>${n.l1 || '-'}</span><b>成分</b><span>${n.n || '-'} 只</span><b>近20日</b><span class="${(n.cum20 || 0) >= 0 ? 'pos' : 'neg'}">${fmtRet(n.cum20)}</span><b>当日</b><span class="${(n.last || 0) >= 0 ? 'pos' : 'neg'}">${fmtRet(n.last)}</span>`;
    renderMembers(cores[0]);
  } else {
    kv.innerHTML = cores.slice(0, 10).map(id => `<b>核心</b><span>${id}</span>`).join('');
    renderMembers(null);
  }
  if (mode === 'lead') {
    const visible = visibleLeadEdges(coreIds).length;
    meta.textContent = `传导模式 · 核心 ${cores.length} · 显示最强 ${visible} 条连接 · 关联 ${neighborIds.length}`;
    const outs = [], inns = [], seenO = new Set(), seenI = new Set();
    for (const c of cores) {
      for (const nb of (DATA.lead_out[c] || [])) { if (coreIds.has(nb.id) || seenO.has(nb.id)) continue; seenO.add(nb.id); outs.push(nb); }
      for (const nb of (DATA.lead_in[c] || [])) { if (coreIds.has(nb.id) || seenI.has(nb.id)) continue; seenI.add(nb.id); inns.push(nb); }
    }
    outs.sort((a, b) => leadStrength(b) - leadStrength(a)); inns.sort((a, b) => leadStrength(b) - leadStrength(a));
    const rowOut = outs.slice(0, LEAD_PANEL_LIMIT).map(r => `<li data-id="${r.id}"><span>${r.id}<span class="sub2">滞后 ${r.lag} 周 · xcorr ${r.xcorr.toFixed(2)} · 跟涨 ${fmtPct(r.p_up)}（${fmtLift(r.lift_up)}）· 跟跌 ${fmtPct(r.p_dn)}（${fmtLift(r.lift_dn)}）</span></span><span class="leadc">→</span></li>`).join('');
    const rowIn = inns.slice(0, LEAD_PANEL_LIMIT).map(r => `<li data-id="${r.id}"><span>${r.id}<span class="sub2">滞后 ${r.lag} 周 · xcorr ${r.xcorr.toFixed(2)} · 跟涨 ${fmtPct(r.p_up)}（${fmtLift(r.lift_up)}）· 跟跌 ${fmtPct(r.p_dn)}（${fmtLift(r.lift_dn)}）</span></span><span class="leadc">←</span></li>`).join('');
    body.innerHTML = `<div class="sec">它领先谁（箭头指出）</div>${outs.length ? `<ul class="list">${rowOut}</ul>` : `<div class="empty">暂无明显领先对象</div>`}<div class="sec">谁领先它（箭头指入）</div>${inns.length ? `<ul class="list">${rowIn}</ul>` : `<div class="empty">暂无明显领先来源</div>`}`;
  } else {
    meta.textContent = `同步模式 · 核心 ${cores.length} · 一跳邻居 ${neighborIds.length}`;
    const rows = [], seen = new Set();
    for (const c of cores) for (const nb of (DATA.neighbors[c] || [])) { if (coreIds.has(nb.id) || seen.has(nb.id)) continue; seen.add(nb.id); rows.push(nb); }
    rows.sort((a, b) => b.abs - a.abs);
    body.innerHTML = rows.length
      ? `<ul class="list">${rows.slice(0, 24).map(r => `<li data-id="${r.id}"><span>${r.id}</span><span class="${r.corr >= 0 ? 'pos' : 'neg'}">ρ ${r.corr.toFixed(2)}</span></li>`).join('')}</ul>`
      : `<div class="empty">该簇暂无足够强的稳健相关边，仍可看空间邻近。</div>`;
  }
  body.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setFocus([li.dataset.id]); });
}

function renderBoard() {
  const ul = document.getElementById('boardList');
  document.getElementById('boardLabel').textContent = retWindow === 'cum20' ? '近20日' : '当日';
  const rows = DATA.nodes.map(n => ({ id: n.id, v: retOf(n) })).filter(r => r.v != null);
  rows.sort((a, b) => b.v - a.v);
  ul.innerHTML = rows.map(r => {
    const cls = r.v >= 0 ? 'pos' : 'neg';
    const on = coreIds.has(r.id) ? 'on' : '';
    return `<li class="${on}" data-id="${r.id}"><span>${r.id}</span><span class="${cls}">${fmtRet(r.v)}</span></li>`;
  }).join('');
  ul.querySelectorAll('li').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setFocus([li.dataset.id]); });
}

function setMode(m) {
  mode = m;
  [...document.getElementById('edgemode').querySelectorAll('button')].forEach(b => b.classList.toggle('active', b.dataset.v === m));
  applyFocusVisual();
  if (coreIds.size) renderPanel([...coreIds], [...focusIds].filter(id => !coreIds.has(id)));
  else renderPanel(null, []);
}

function setFocus(cores) {
  coreIds = new Set(cores);
  if (!cores.length) { focusIds = new Set(); applyFocusVisual(); clearLabels(); renderPanel(null, []); return; }
  const { one, two } = egoOf(cores);
  focusIds = new Set([...one, ...two]);
  applyFocusVisual();
  const pts = cores.map(id => nodeById[id]).filter(Boolean);
  if (pts.length) {
    const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
    const cz = pts.reduce((s, p) => s + p.z, 0) / pts.length;
    controls.target.set(cx, cy, cz);
    camera.position.set(cx + 55, cy + 28, cz + 95);
  }
  const neighbors = [...one].filter(id => !coreIds.has(id));
  renderPanel(cores, neighbors);
  rebuildLabels(cores, neighbors.slice(0, 18));
  // 聚焦节点时自动切到「详」，仍可手动切走 / 再点收起
  setDockMode('detail', { fromFocus: true });
}

function updateHints() {
  const box = document.getElementById('hints');
  const q = document.getElementById('q').value.trim();
  if (qMode !== 'stock' || !q) { box.innerHTML = ''; return; }
  const hits = searchStocks(q);
  if (!hits.length) { box.innerHTML = `<div class="empty">未匹配到标的</div>`; return; }
  box.innerHTML = hits.map(h => `<div data-sector="${h.sector}"><b>${h.name}</b>${h.code} → ${h.sector}</div>`).join('');
  box.querySelectorAll('div[data-sector]').forEach(el => {
    el.onclick = () => {
      document.getElementById('q').value = el.dataset.sector;
      setFocus([el.dataset.sector]);
      [...box.children].forEach(x => x.classList.remove('on')); el.classList.add('on');
    };
  });
}

function runQuery() {
  const q = document.getElementById('q').value;
  if (qMode === 'stock') {
    const hits = searchStocks(q); updateHints();
    if (!hits.length) { renderPanel(null, []); document.getElementById('pBody').innerHTML = `<div class="empty">未匹配到标的，试试名称或代码。</div>`; return; }
    setFocus([hits[0].sector]); document.getElementById('q').value = hits[0].sector; return;
  }
  const cores = expandQuery(q);
  if (!cores.length) { renderPanel(null, []); document.getElementById('pBody').innerHTML = `<div class="empty">未匹配到二级板块，试试「电力」「半导体」「白酒」。</div>`; return; }
  setFocus(cores);
}

document.getElementById('go').onclick = runQuery;
document.getElementById('reset').onclick = () => { document.getElementById('q').value = ''; document.getElementById('hints').innerHTML = ''; setFocus([]); };
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') runQuery(); });
document.getElementById('q').addEventListener('input', () => { if (qMode === 'stock') updateHints(); });
document.getElementById('edgemode').onclick = e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.v); };
document.getElementById('qmode').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  qMode = b.dataset.v;
  [...document.getElementById('qmode').children].forEach(x => x.classList.toggle('active', x === b));
  document.getElementById('q').placeholder = qMode === 'stock' ? '输入股票名称或代码…' : '查询板块，如：电力、半导体、白酒…';
  updateHints();
};
document.getElementById('retmode').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  retWindow = b.dataset.v;
  [...document.getElementById('retmode').children].forEach(x => x.classList.toggle('active', x === b));
  applyNodeAppearance(); renderBoard();
  if (coreIds.size === 1) renderPanel([...coreIds], [...focusIds].filter(id => !coreIds.has(id)));
};

const presetsEl = document.getElementById('presets');
(DATA.presets || []).forEach(p => {
  const b = document.createElement('button'); b.className = 'chip'; b.type = 'button'; b.textContent = p;
  b.onclick = () => {
    [...presetsEl.children].forEach(x => x.classList.remove('active')); b.classList.add('active');
    qMode = 'sector'; [...document.getElementById('qmode').children].forEach(x => x.classList.toggle('active', x.dataset.v === 'sector'));
    document.getElementById('q').value = p; runQuery();
  };
  presetsEl.appendChild(b);
});

const raycaster = new THREE.Raycaster(); raycaster.params.Points.threshold = 2.8;
const mouse = new THREE.Vector2();
const CLICK_PX = 6; let ptrDown = null, ptrDragged = false;
canvas.addEventListener('pointerdown', ev => { if (ev.button != null && ev.button !== 0) return; ptrDown = { x: ev.clientX, y: ev.clientY }; ptrDragged = false; });
canvas.addEventListener('pointermove', ev => { if (!ptrDown) return; if (Math.hypot(ev.clientX - ptrDown.x, ev.clientY - ptrDown.y) > CLICK_PX) ptrDragged = true; });
function clearPtr() { ptrDown = null; ptrDragged = false; }
canvas.addEventListener('pointercancel', clearPtr);
canvas.addEventListener('pointerup', ev => {
  if (!ptrDown) return;
  const moved = ptrDragged || Math.hypot(ev.clientX - ptrDown.x, ev.clientY - ptrDown.y) > CLICK_PX;
  clearPtr(); if (moved) return;
  mouse.x = (ev.clientX / innerWidth) * 2 - 1; mouse.y = -(ev.clientY / innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  const hits = raycaster.intersectObject(points);
  if (hits.length) { const id = DATA.nodes[hits[0].index].id; document.getElementById('q').value = id; setFocus([id]); }
});
window.addEventListener('resize', () => { camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix(); renderer.setSize(innerWidth, innerHeight); });

applyNodeAppearance(); rebuildFlow(); renderBoard(); renderPanel(null, []);

const DOCK_KEY = 'scc_dock_mode';
const ZEN_TIP_KEY = 'scc_zen_tip_seen';
const MODE_TITLE = { ctrl: '搜索与筛选', board: '涨跌榜', detail: '聚焦详情' };
let dockMode = 'ctrl';

function maybeShowZenTip() {
  try {
    if (sessionStorage.getItem(ZEN_TIP_KEY) === '1') return;
    if (dockMode === 'hidden') return;
    const tip = document.getElementById('zenTip');
    tip.classList.add('show');
    sessionStorage.setItem(ZEN_TIP_KEY, '1');
    setTimeout(() => tip.classList.remove('show'), 3200);
  } catch (e) {}
}

function setDockMode(next, { persist = true, fromFocus = false } = {}) {
  if (!fromFocus && next === dockMode && next !== 'hidden') next = 'hidden';
  dockMode = next;
  const fly = document.getElementById('flyout');
  const open = dockMode !== 'hidden';
  fly.classList.toggle('open', open);
  document.querySelectorAll('.rail-btn').forEach(b => {
    b.classList.toggle('active', open && b.dataset.mode === dockMode);
  });
  document.querySelectorAll('.fly-pane').forEach(p => {
    p.classList.toggle('active', open && p.dataset.pane === dockMode);
  });
  if (open) document.getElementById('flyTitle').textContent = MODE_TITLE[dockMode] || '';
  if (persist) {
    try { sessionStorage.setItem(DOCK_KEY, dockMode); } catch (e) {}
  }
  maybeShowZenTip();
}

document.getElementById('dockRail').onclick = e => {
  const b = e.target.closest('.rail-btn'); if (!b) return;
  const m = b.dataset.mode;
  if (dockMode === m) setDockMode('hidden');
  else setDockMode(m);
};
document.getElementById('flyClose').onclick = () => setDockMode('hidden');

(function initDock() {
  let saved = null;
  try { saved = sessionStorage.getItem(DOCK_KEY); } catch (e) {}
  if (!saved) {
    try {
      const top = sessionStorage.getItem('scc_top_collapsed');
      const side = sessionStorage.getItem('scc_side_collapsed');
      if (top === '1' && side === '1') saved = 'hidden';
    } catch (e) {}
  }
  if (!['ctrl', 'board', 'detail', 'hidden'].includes(saved)) saved = 'ctrl';
  dockMode = '__init__';
  setDockMode(saved, { persist: false });
})();

applyDeepLink();

applyNodeAppearance();
rebuildFlow();

(function tick(now) {
  requestAnimationFrame(tick);
  const t = (now || performance.now()) * 0.001;
  for (const m of flowMats) m.dashOffset = -t * 4.5;
  for (const ch of flowGroup.children) {
    if (ch.isPoints && ch.userData && ch.userData.n) {
      const u = ch.userData, arr = ch.geometry.attributes.position.array;
      for (let i = 0; i < u.n; i++) {
        const tt = (i / u.n + t * 0.35 + u.phase) % 1;
        arr[i * 3] = u.ax + (u.bx - u.ax) * tt;
        arr[i * 3 + 1] = u.ay + (u.by - u.ay) * tt;
        arr[i * 3 + 2] = u.az + (u.bz - u.az) * tt;
      }
      ch.geometry.attributes.position.needsUpdate = true;
    }
  }
  controls.update(); renderer.render(scene, camera);
})();

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
        print("从缓存加载，刷新 stock_index / sector_members…")
        payload = attach_stock_payload(payload)
        OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
        print(f"从缓存重渲 {OUTPUT_HTML}（节点 {payload.get('n_sectors')} · 边 {len(payload.get('edges', []))} · 股票索引 {len(payload.get('stock_index', []))}）")
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
