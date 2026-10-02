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

from scripts.reports.sector_daily import load_daily_bundle
from scripts.reports.sector_graph import analyze as analyze_graph
from scripts.reports.sector_lead_stats import build_lead_edges_sig, load_config, rolling_residualize
from scripts.reports.sector_signals import compute_signals
from scripts.reports.sector_style import load_style
from scripts.reports.sector_concepts import attach_concepts

# 参数集中在 config/sector_corr_cloud.yaml（缺省值见 sector_lead_stats.DEFAULTS）
CFG = load_config()
START = str(CFG["start"])
MIN_NAMES_PER_DAY = 5
CORR_THR = float(CFG["sync"]["corr_thr"])
MAX_EDGES_PER_NODE = int(CFG["sync"]["max_edges_per_node"])
MIN_YEAR_CONFIRM = int(CFG["sync"]["min_year_confirm"])
MAX_LAG = int(CFG["lead"]["max_lag"])  # 周
LEAD_XCORR_THR = float(CFG["lead"]["xcorr_thr"])
LEAD_ASYM = float(CFG["lead"]["asym"])  # 领先方向需略强于反向
LEAD_MAX_OUT = int(CFG["lead"]["max_out"])  # 每点最多指出几条领先边
EVENT_Q = float(CFG["lead"]["event_q"])
HORIZON = 5  # 旧口径（日）；新口径的事件窗 = 边的滞后周数
BETA_WINDOW = int(CFG["beta"]["window"])
BETA_MIN_PERIODS = int(CFG["beta"]["min_periods"])
KLINE_CACHE = PROJECT_DIR / "cache" / "stock_kline_cache.parquet"
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


def beta_warmup_start(start: str = START) -> pd.Timestamp:
    """滚动 beta 需要预热：向前多取约一个窗口的交易日。"""
    return pd.Timestamp(start) - pd.Timedelta(days=int(BETA_WINDOW * 1.6) + 30)


def _load_daily_returns(start) -> pd.DataFrame:
    """个股日收益（%）。优先由 stock_kline_cache 的收盘价重算（与 features.daily 的 mom1 同口径，
    但覆盖到最新交易日）；日线缓存不可用时退回 indicators_mom1.parquet。"""
    start = pd.Timestamp(start)
    if KLINE_CACHE.exists():
        try:
            pre = start - pd.Timedelta(days=15)
            k = pd.read_parquet(KLINE_CACHE, columns=["代码", "日期", "收盘"], filters=[("日期", ">=", pre)])
            k["日期"] = pd.to_datetime(k["日期"])
            close = k.pivot_table(index="日期", columns="代码", values="收盘", aggfunc="last").sort_index()
            ret = close.pct_change(fill_method=None) * 100
            return ret.loc[ret.index >= start]
        except Exception as exc:
            print(f"  读取日线缓存失败，退回 mom1: {exc}")
    mom = pd.read_parquet(MOM1)
    mom.index = pd.to_datetime(mom.index)
    return mom.loc[mom.index >= start]


def load_sw2_returns(start=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    mom = _load_daily_returns(beta_warmup_start() if start is None else start)

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


def load_benchmark(piv: pd.DataFrame, index_cache: pd.DataFrame | None = None) -> tuple[pd.Series, dict]:
    """市场基准日收益（%）与说明。

    旧实现调用了 IndexData 不存在的 ``get_kline()``，异常被吞掉后总是静默退回板块等权均值。
    现直接读 ``IndexData().cache``（cache/index_kline_cache.parquet）。全部失败时仍退回等权，
    但返回 warning，页面会显著提示。
    """
    bc = CFG["benchmark"]
    codes = [str(bc["code"]), *[str(c) for c in bc.get("fallback_codes", [])]]
    names: dict = {}
    err = ""
    if index_cache is None:
        try:
            from data.index import IndexData, INDEXES
            names = dict(INDEXES)
            index_cache = IndexData().cache
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            index_cache = None
    min_cov = float(bc.get("min_coverage", 0.95))
    span = piv.index[piv.notna().any(axis=1)]
    for code in codes:
        if index_cache is None or len(index_cache) == 0:
            break
        k = index_cache[index_cache["代码"] == code].copy()
        if k.empty:
            continue
        k["日期"] = pd.to_datetime(k["日期"])
        k = k.sort_values("日期").drop_duplicates("日期", keep="last").set_index("日期")
        r = k["收盘"].astype(float).pct_change(fill_method=None) * 100
        r = r.reindex(piv.index)
        cov = float(r.reindex(span).notna().mean()) if len(span) else 0.0
        if cov < min_cov:
            err = f"{code} 覆盖率 {cov:.1%} < {min_cov:.0%}"
            continue
        source = str(k["来源"].iloc[-1]) if "来源" in k.columns else "ak"
        is_primary = code == str(bc["code"])
        name = str(bc["name"]) if is_primary else names.get(code, code)
        warning = None
        if not is_primary:
            warning = f"{bc['name']}（{bc['code']}）不可用，已改用 {name}（{code}）作为市场基准"
        elif source == "etf":
            warning = f"{name} 使用 ETF 代理行情"
        return r, {"code": code, "name": name, "ok": is_primary and warning is None,
                   "coverage": round(cov, 4), "source": source, "warning": warning}
    return piv.mean(axis=1), {
        "code": "equal_weight", "name": "板块等权均值", "ok": False, "coverage": 1.0, "source": "fallback",
        "warning": f"{bc['name']}（{bc['code']}）读取失败（{err or '无数据'}），已退回板块等权均值作为市场基准",
    }


def market_proxy(piv: pd.DataFrame) -> pd.Series:
    return load_benchmark(piv)[0]


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


def pack_payload(piv: pd.DataFrame, resid: pd.DataFrame, meta: pd.DataFrame, bench: dict | None = None) -> dict:
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

    print("  计算领先—滞后边：循环平移零分布 + BH-FDR + 滚动样本外…")
    lead_edges, lead_summary = build_lead_edges_sig(resid, corr, CFG)
    oos_all = lead_summary["oos"]["all"]
    print(f"  领先候选 {len(lead_edges)} 条 · FDR(α={lead_summary['fdr_alpha']}) 通过 {lead_summary['fdr_pass']} 条"
          f" · 检验数 {lead_summary['m_tests']} · 样本外命中 {oos_all['hit']} vs 基准 {oos_all['base']}")

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
        body = {k: v for k, v in e.items() if k not in ("source", "target")}
        lead_out[e["source"]].append({"id": e["target"], **body})
        lead_in[e["target"]].append({"id": e["source"], **body})
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
        "horizon": "滞后周",
        "note": "同步边=滚动beta去市场后的日残差相关；箭头=周频残差交叉相关领先（滞后周数），经循环平移零分布+BH-FDR检验，并做滚动样本外验证。非因果，仅统计倾向。",
        "data_as_of": str(piv.index.max().date()),
        "benchmark": bench or {},
        "beta_window": BETA_WINDOW,
        "lead_summary": lead_summary,
        "lead_display": CFG["display"],
        "stale_weeks": int(CFG["stale_weeks"]),
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


def attach_graph(payload: dict) -> dict:
    """同步图社区 / 桥梁 / 加权度（秒级，from-cache 也会刷新，参数见 config graph:）。"""
    payload["graph"] = analyze_graph(payload.get("nodes", []), payload.get("edges", []), CFG.get("graph"))
    return payload


def attach_daily(payload: dict) -> dict:
    """嵌入近 N 日板块日收益（基点整数数组），供光晕/边着色/逐日回放；参数见 config daily:。"""
    ids = [n["id"] for n in payload.get("nodes", [])]
    try:
        info = StockInfo().df
        sw2_col = next(c for c in ("申万2级", "申万二级", "sw_l2") if c in info.columns)
        sig_cfg = CFG.get("signals") or {}
        style_cfg = CFG.get("style") or {}
        hist = max(int(sig_cfg.get("history_days", 120)), int(style_cfg.get("lookback_days", 500)))
        payload["daily"], frame = load_daily_bundle(KLINE_CACHE, info, sw2_col, ids, CFG.get("daily"), history_days=hist)
        d = payload["daily"]
        print(f"  逐日收益 {len(d['dates'])} 日（回放 {d['replay_days']} 日，{d['dates'][0] if d['dates'] else '-'} → {d['dates'][-1] if d['dates'] else '-'}）")
    except Exception as exc:  # 行情缓存缺失时页面退回 node.last / cum20，不影响结构
        print(f"  逐日收益不可用，回放关闭: {exc}")
        payload["daily"], payload["signals"], payload["style"] = None, None, None
        return payload
    try:  # 风格标签（进攻/防御/周期/成长/价值）：历史 beta/波动/回撤 + 市盈率 + 配置覆盖
        l1_of = {n["id"]: n.get("l1", "") for n in payload.get("nodes", [])}
        payload["style"] = load_style(frame, l1_of, info, sw2_col, PROJECT_DIR / "cache" / "index_kline_cache.parquet",
                                      PROJECT_DIR / "cache" / "daily_basic_cache.parquet",
                                      str(CFG.get("benchmark", {}).get("code", "sh000300")), style_cfg)
        payload["style"]["view"] = {k: style_cfg.get(k) for k in ("links", "link_k", "link_opacity")}
        print(f"  风格标签 {payload['style']['counts']}（基准 {payload['style']['bench']}，{payload['style']['lookback']} 日）")
    except Exception as exc:
        print(f"  风格标签不可用: {exc}")
        payload["style"] = None
    try:
        out_dates = d["dates"][-d["replay_days"]:] if d["replay_days"] else []
        payload["signals"] = compute_signals(frame, payload.get("graph"), payload.get("edges", []), out_dates, sig_cfg,
                                             payload.get("style"))
        print(f"  异动观察 {sum(len(x) for x in payload['signals']['days'])} 条 / {len(out_dates)} 日")
    except Exception as exc:  # 信号失败不影响回放
        print(f"  异动观察不可用: {exc}")
        payload["signals"] = None
    return payload


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
.warnbar{position:fixed;top:12px;right:12px;z-index:7;max-width:min(460px,calc(100vw - 24px));display:none;flex-direction:column;gap:4px;padding:8px 12px;border-radius:12px;background:rgba(60,12,16,.9);border:1px solid rgba(255,107,107,.55);color:#ffd5d5;font-size:12px;line-height:1.45;backdrop-filter:blur(10px)}
.warnbar.show{display:flex}
.warnbar code{color:#fff;font-size:11px}
.foot .stale{color:#ff6b6b;font-weight:650}
.sig{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;font-size:10px;line-height:16px;vertical-align:1px}
.sig.ok{color:#0b1220;background:var(--lead)}
.sig.no{color:var(--muted);border:1px dashed rgba(139,155,184,.6)}
.list li.failed{opacity:.62}
.leadsum{font-size:11px;color:var(--muted);line-height:1.5;margin-top:8px}
.rcol{position:fixed;right:14px;top:14px;bottom:14px;z-index:5;width:300px;display:flex;flex-direction:column;justify-content:flex-start;gap:8px;pointer-events:none}
.rcol>*{pointer-events:auto}
.legend{flex:0 0 auto;max-height:58vh;overflow:auto;font-size:11px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:6px 10px 8px;backdrop-filter:blur(10px)}
.legend summary{cursor:pointer;color:var(--text);font-weight:600;list-style:none;padding:2px 0}
.legend summary::-webkit-details-marker{display:none}
.legend summary::after{content:' ▾';color:var(--muted)}
.legend:not([open]) summary::after{content:' ▸'}
.lg-row{display:flex;align-items:center;gap:8px;margin-top:6px;line-height:1.35}
.lg-sw{flex:0 0 46px;display:flex;align-items:center;justify-content:center;gap:5px}
.lg-grad{width:46px;height:8px;border-radius:4px;background:linear-gradient(90deg,#2ee6a0,#1f3a33 46%,#3b2428 54%,#ff5a5a)}
.lg-dot{display:inline-block;border-radius:50%;background:#ff7a7a;box-shadow:0 0 6px rgba(255,122,122,.6)}
.lg-dot.gray{background:#5a6679;box-shadow:none}
.lg-line{width:46px;height:0;border-top:2px solid rgba(255,122,122,.85)}
.lg-line.soft{border-top:2px dashed rgba(143,163,196,.8)}
.lg-arrow{width:38px;height:0;border-top:2px solid #ffd166;position:relative}
.lg-arrow::after{content:'';position:absolute;right:-6px;top:-5px;border-left:7px solid #ffd166;border-top:4px solid transparent;border-bottom:4px solid transparent}
.lg-arrow.neg{border-top-color:#7aa2ff}.lg-arrow.neg::after{border-left-color:#7aa2ff}
.lg-arrow.fail{border-top-style:dashed;opacity:.6}
.chips.expand{align-items:center}
.exp-hd{font-size:11px;color:var(--muted);margin-right:2px;white-space:nowrap}
.seg button.exp{border:1px dashed rgba(255,209,102,.45);color:var(--muted)}
.seg button.exp small{margin-left:4px;font-size:10px;color:#ffd166}
.exp-note{font-size:11px;line-height:1.5;color:#ffd166;background:rgba(255,209,102,.08);border:1px dashed rgba(255,209,102,.4);border-radius:10px;padding:6px 8px;margin-bottom:8px}
.from{display:inline-block;margin-left:6px;font-size:10px;color:var(--accent);opacity:.85}
.tag-ok{display:inline-block;margin-left:6px;font-size:10px;color:#ffb3b3;border:1px solid rgba(255,122,122,.4);border-radius:999px;padding:0 5px;line-height:15px}
.kv .corelist{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.rail-btn.home{font-size:17px}
.trail{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:5;display:flex;align-items:center;gap:6px;max-width:min(640px,calc(100vw - 460px));padding:4px 8px;border-radius:999px;background:var(--panel);border:1px solid var(--line);backdrop-filter:blur(10px);font-size:12px}
.trail[hidden]{display:none}
.trail button{padding:1px 9px;font-size:15px;line-height:20px;flex:0 0 auto}
.trail button:disabled{opacity:.35;cursor:default}
.crumbs{display:flex;align-items:center;gap:3px;overflow-x:auto;white-space:nowrap;scrollbar-width:none;min-width:0}
.crumbs::-webkit-scrollbar{display:none}
.crumb{color:var(--muted);cursor:pointer;padding:2px 7px;border-radius:999px}
.crumb:hover{color:var(--text)}
.crumb.on{color:#0b1220;background:#9ff3ff}
.crumb-sep{color:rgba(139,155,184,.5)}
.pstep{font-size:12px;line-height:1.5;color:#bfefff;background:rgba(90,209,255,.07);border-left:2px solid #9ff3ff;border-radius:6px;padding:6px 8px;margin:0 0 8px}
.pstep:empty{display:none}
.stag{display:inline-block;font-size:10px;border-radius:999px;padding:0 6px;margin-right:6px;line-height:16px;border:1px solid var(--line);color:var(--muted)}
.stag.core{color:#0b1220;background:#ffe9a8;border-color:transparent}
.stag.bridge{color:#ffe9a8;border-color:rgba(255,233,168,.6)}
.stag.nb{color:var(--accent);border-color:rgba(90,209,255,.45)}
.commlink{cursor:pointer;text-decoration:underline dotted;text-underline-offset:3px}
.cdot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;flex:0 0 auto}
.lg-comm{display:grid;grid-template-columns:1fr;gap:3px;margin:4px 0 0 2px}
.lg-comm .lc{display:grid;grid-template-columns:9px minmax(0,1fr) 44px 40px 60px;align-items:center;column-gap:8px;cursor:pointer;line-height:1.25;padding:1px 0}
.lg-comm .lc.nostep{cursor:default}
.lg-comm .lc:hover{color:var(--text)}
.lg-comm .lc .cdot{margin:0}
.lg-comm .lc-name{white-space:normal;word-break:keep-all;overflow-wrap:anywhere}
.lc-bar{height:6px;border-radius:3px;background:color-mix(in srgb,var(--dn,#22d38a) 38%,transparent);overflow:hidden}
.lc-bar i{display:block;height:100%;background:var(--up,#ff4d4f);border-radius:3px 0 0 3px}
.lc-up,.lc-avg{font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.lc-avg{padding-left:6px;border-left:1px solid rgba(139,155,184,.18)}
.legend summary{display:flex;align-items:center;gap:6px}
.legend summary::after{order:9}
.lg-sub{font-weight:400;color:var(--muted);font-size:10.5px;font-variant-numeric:tabular-nums}
.info-btn{display:inline-flex;align-items:center;justify-content:center;gap:3px;min-width:20px;height:20px;padding:0 5px;margin-left:4px;border-radius:999px;border:1px solid rgba(159,243,255,.4);background:rgba(159,243,255,.07);color:#9ff3ff;font-size:11px;line-height:1;cursor:pointer;vertical-align:middle;white-space:nowrap;pointer-events:auto;flex-shrink:0}
.info-btn:hover,.info-btn[aria-expanded="true"]{background:rgba(159,243,255,.2)}
.info-btn.pill{margin-left:auto;padding:0 8px;font-size:10.5px;height:18px}
.info-btn::before{content:'';position:absolute;inset:-8px}
.info-btn{position:relative}
.lg-colhd{display:grid;grid-template-columns:9px minmax(0,1fr) 44px 40px 60px;column-gap:8px;font-size:10px;color:var(--muted);opacity:.8;margin:4px 0 1px 2px}
.lg-colhd .c-up{grid-column:3 / 5;text-align:center}
.lg-colhd .c-avg{text-align:right;padding-left:6px}
.infopop{position:fixed;inset:auto;margin:0;box-sizing:border-box;width:min(380px,calc(100vw - 20px));max-height:min(70vh,560px);overflow:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;padding:0 14px 12px;border-radius:14px;border:1px solid var(--line);background:rgba(12,18,32,.97);color:var(--muted);font-size:12px;line-height:1.6;box-shadow:0 18px 50px rgba(0,0,0,.55);z-index:30}
.infopop::backdrop{background:transparent}
.infopop:not(:popover-open):not(.open){display:none}
.ip-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;color:var(--text);font-size:13px;position:sticky;top:0;background:rgba(12,18,32,.98);padding:10px 0 6px;margin-bottom:2px;border-bottom:1px solid rgba(255,255,255,.06);z-index:1}
.ip-x{width:28px;height:28px;flex-shrink:0;border-radius:50%;border:1px solid var(--line);background:transparent;color:var(--text);font-size:16px;cursor:pointer}
.ip-body p{margin:8px 0 0}.ip-body .lg-row{margin-top:8px}
.ip-list{margin:6px 0 0;padding-left:18px}.ip-list li{margin-top:3px}
.ip-body b{color:var(--text);font-weight:600}
.ip-kv{color:var(--text);font-variant-numeric:tabular-nums}
.exp-tag{display:flex;align-items:center;gap:4px;margin:4px 0 8px;font-size:11.5px;color:#ffcf7a}
.p-hd{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.p-hd h3{margin:0}
.info-btn.style-badge{height:22px;padding:0 9px 0 7px;gap:5px;border-color:var(--sc,#6b778c);background:rgba(255,255,255,.04);color:var(--text);font-size:12px;font-weight:600}
.style-badge i{width:8px;height:8px;border-radius:50%;background:var(--sc,#6b778c);display:inline-block}
.style-badge small{color:var(--sc2,var(--muted));font-weight:500}
.style-badge[hidden]{display:none}
.stag{display:inline-block;margin-left:4px;padding:0 3px;border-radius:4px;border:1px solid currentColor;font-size:9.5px;line-height:13px;font-weight:600;vertical-align:1px;opacity:.9;color:var(--sc,#8b9bb8)}
.node-tip{position:fixed;z-index:20;pointer-events:none;display:none;align-items:center;gap:6px;padding:5px 9px;border-radius:9px;background:rgba(10,15,28,.94);border:1px solid var(--line);color:var(--text);font-size:12px;white-space:nowrap;box-shadow:0 8px 24px rgba(0,0,0,.45)}
.node-tip.show{display:flex}.node-tip .stag{margin-left:0;font-size:11px;line-height:16px;padding:0 5px}
.node-tip .pos{color:var(--up,#ff4d4f)}.node-tip .neg{color:var(--dn,#22d38a)}
.list li.sdim{opacity:.45}
.lg-comm .lc.sf{cursor:pointer;border-radius:6px}
.lg-comm .lc.sf.on{background:rgba(159,243,255,.1);color:var(--text);outline:1px solid rgba(159,243,255,.35)}
.lg-comm .lc.sf.off{opacity:.38}
.sw1-row{display:flex;align-items:center;gap:6px;margin-top:8px}
.sw1-row select{flex:1;min-width:0;height:32px;border-radius:10px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--text);padding:0 10px;font-size:13px}
.sw1-row select.on{border-color:var(--accent);color:var(--accent)}
.sw1-badge{height:22px;padding:0 8px;border-radius:999px;border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--muted);font-size:11.5px;cursor:pointer}
.sw1-badge:hover{color:var(--text);border-color:var(--accent)}
.sw1-badge[hidden]{display:none}
.lg-comm.lg-scroll{max-height:min(44vh,400px);overflow:auto;padding-right:4px}
.node-tip .tip-l1{color:var(--muted);font-size:11px}
.sw1-list .sub2 .cdot{margin-right:3px}
@media (max-width:900px){.lg-comm.lg-scroll{max-height:32vh}.sw1-row select{height:36px;font-size:14px}}
.cc-src{display:inline-block;font-size:10.5px;line-height:15px;padding:0 5px;margin:0 5px;border-radius:6px;border:1px solid var(--line);color:var(--muted);font-weight:400;vertical-align:1px}
.cc-src.cc-ths{color:#ffb86b;border-color:rgba(255,184,107,.45)}
.cc-src.cc-em{color:#7cc4ff;border-color:rgba(124,196,255,.45)}
.cc-alts{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px;align-items:center}
.cc-alts .chip{padding:5px 9px}
.cc-alts .chip .cc-src{margin:0 4px 0 0}
.cc-alts-l{font-size:11px;color:var(--muted);margin-left:2px}
.cc-list li.cc-sec{border-bottom-color:rgba(255,255,255,.12);padding-top:10px;font-weight:560}
.cc-list li.cc-sec .sub2{font-weight:400}
.cc-list li.cc-stk{padding:5px 0 5px 12px;font-size:12px}
.cc-list li.cc-loose{cursor:default}
.cc-w{flex:0 0 46px;height:4px;align-self:center;border-radius:2px;background:linear-gradient(90deg,#ffd27a var(--w),rgba(255,255,255,.08) var(--w))}
.cc-r{display:flex;gap:8px;align-items:baseline;white-space:nowrap}
.cc-r small{min-width:52px;text-align:right;font-size:11px}
.cc-foot{margin-top:10px}
@media (max-width:900px){.cc-list li.cc-stk{padding:7px 0 7px 12px}.cc-alts .chip{padding:7px 10px}}
.why-v{font-variant-numeric:tabular-nums;color:var(--text)}
.short-note{display:flex;align-items:center;gap:4px;font-size:11.5px;color:var(--muted);margin-top:6px}
#paneCtrl .sub{display:none}
.fly-hd h3{margin-right:0}.fly-hd .info-btn{margin-right:auto}
.foot .info-btn{pointer-events:auto}
.leadsum .info-btn{margin-left:4px}
@media (min-width:901px){.warnbar{right:326px}}
.ra-mini{font-weight:700;color:#ff9f43}
.rp-ra{display:inline-flex;align-items:center;gap:5px;white-space:nowrap;padding-left:8px;border-left:1px solid var(--line)}
.rp-ra[hidden]{display:none}
.rp-ra .ra-lbl{color:var(--muted);font-size:11px}
.rp-ra b{font-variant-numeric:tabular-nums;font-weight:600;min-width:46px;text-align:right}
.rp-ra b.pos{color:var(--up,#ff4d4f)}.rp-ra b.neg{color:var(--dn,#22d38a)}
.sigp{flex:0 1 auto;min-height:0;overflow:auto;font-size:11.5px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:6px 10px 8px;backdrop-filter:blur(10px)}
.sigp summary{cursor:pointer;color:var(--text);font-weight:600;list-style:none;padding:2px 0;display:flex;align-items:baseline;gap:6px}
.sigp summary::-webkit-details-marker{display:none}
.sigp summary::after{content:'▾';color:var(--muted);margin-left:auto}
.sigp:not([open]) summary::after{content:'▸'}
.sg-date{font-weight:400;color:var(--accent);font-variant-numeric:tabular-nums}
.sg-peek{font-weight:400;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1}
.sigp[open] .sg-peek{display:none}
.sg-list{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:2px}
.sg-list li{display:flex;gap:7px;align-items:flex-start;padding:4px 4px;border-radius:8px;cursor:pointer;line-height:1.4}
.sg-list li:hover{background:rgba(159,243,255,.08)}
.sg-list li.weak{opacity:.72}
.sg-list li.empty{cursor:default;color:var(--muted)}
.sg-list li.new{animation:sgIn .9s ease-out}
@keyframes sgIn{from{background:rgba(159,243,255,.22)}to{background:transparent}}
.sg-tag{flex:0 0 auto;font-size:10px;line-height:16px;padding:0 5px;border-radius:999px;border:1px solid currentColor;margin-top:1px}
.sg-tag.t-style{color:#ff9f43;background:rgba(255,159,67,.12)}.sg-tag.t-contra{color:#ffb347}.sg-tag.t-decouple{color:#c792ea}.sg-tag.t-streak{color:#9ff3ff}.sg-tag.t-bridge{color:#ffe9a8}.sg-tag.t-disperse{color:#8fa8ff}
.sg-body{min-width:0;color:var(--muted)}
.sg-body b{color:var(--text);font-weight:600;margin-right:4px}
.sg-body b.pos{color:var(--up,#ff4d4f)}.sg-body b.neg{color:var(--dn,#22d38a)}
.sg-n{display:inline-block;font-size:10px;color:#0b1220;background:#9ff3ff;border-radius:999px;padding:0 5px;line-height:15px;margin-right:4px;vertical-align:1px}
.lg-ring{display:inline-block;width:14px;height:14px;border-radius:50%;border:1.5px dashed #ffe9a8}
.lg-line.web{border-top:1px solid rgba(170,205,255,.8);box-shadow:0 0 5px rgba(170,205,255,.6)}
.lg-line.trailc{border-top:2px solid #9ff3ff}
.tourcap{position:fixed;left:50%;top:54px;transform:translateX(-50%);z-index:6;max-width:min(560px,calc(100vw - 24px));font-size:13px;color:var(--text);background:rgba(12,18,32,.92);border:1px solid rgba(159,243,255,.45);border-radius:12px;padding:8px 14px;display:none;text-align:center;pointer-events:none}
.tourcap.show{display:block}
.replay{position:fixed;left:50%;bottom:64px;transform:translateX(-50%);z-index:5;display:flex;align-items:center;gap:8px;padding:5px 10px;border-radius:999px;background:var(--panel);border:1px solid var(--line);backdrop-filter:blur(10px);font-size:12px;width:min(680px,calc(100vw - 680px))}
.replay[hidden]{display:none}
.replay #rpPlay{width:30px;height:30px;padding:0;border-radius:50%;font-size:12px;flex:0 0 auto}
.replay input[type=range]{flex:1;min-width:60px;accent-color:#9ff3ff}
.rp-date{color:var(--text);font-variant-numeric:tabular-nums;white-space:nowrap;min-width:44px;text-align:right}
.seg.mini{flex:0 0 auto}
.seg.mini button{padding:4px 8px;font-size:11px}
.lg-halo{display:inline-block;width:14px;height:14px;border-radius:50%}
.lg-halo.up{background:radial-gradient(circle,#8fa8ff 0 3px,rgba(255,77,79,.75) 4px,rgba(255,77,79,0) 7px)}
.lg-halo.dn{background:radial-gradient(circle,#8fa8ff 0 3px,rgba(34,211,138,.75) 4px,rgba(34,211,138,0) 7px)}
@media(max-width:900px){
  .replay{bottom:auto;top:calc(52px + env(safe-area-inset-top,0px));left:10px;right:10px;width:auto;transform:none;flex-wrap:wrap;border-radius:18px;row-gap:4px}
  .rp-ra{flex:1 0 100%;justify-content:center;border-left:0;padding-left:0;border-top:1px solid var(--line);padding-top:3px}
  .tourcap{top:calc(100px + env(safe-area-inset-top,0px))}
}
@media(max-width:900px){
  .trail{left:92px;right:10px;transform:none;max-width:none;top:calc(10px + env(safe-area-inset-top,0px))}
  .tourcap{top:calc(56px + env(safe-area-inset-top,0px))}
}
@media(max-width:900px){
  .rail{left:50%;right:auto;top:auto;bottom:calc(10px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);flex-direction:row;width:auto;padding:6px 10px;border-radius:999px;gap:4px}
  .rail-btn{width:40px;height:40px;border-radius:999px;font-size:14px}
  .flyout{left:10px;right:10px;top:auto;bottom:calc(64px + env(safe-area-inset-bottom,0px));width:auto;max-height:var(--fly-mvh,42vh);border-radius:16px 16px 14px 14px}
  #paneCtrl .title,#paneCtrl .sub{display:none}
  .fly-hd{padding:8px 12px 6px}
  .fly-body{padding:8px 12px 10px}
  .search{margin-top:0;gap:6px}
  .search input{padding:8px 10px;min-width:0;flex:1 1 110px}
  .row{margin-top:6px;gap:6px}
  #presets,.chips.expand{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch;padding-bottom:2px}
  #presets::-webkit-scrollbar,.chips.expand::-webkit-scrollbar{display:none}
  #presets .chip,.chips.expand .chip{flex:0 0 auto}
  .rcol{display:contents}
  .legend{position:fixed;z-index:6;right:auto;left:10px;bottom:auto;top:calc(10px + env(safe-area-inset-top,0px));width:auto;max-width:calc(100vw - 20px);max-height:70vh}
  .legend[open]{width:min(330px,calc(100vw - 20px))}
  .infopop{font-size:13px}
  .info-btn{min-width:24px;height:24px}
  .sigp{position:fixed;z-index:5;left:10px;right:10px;top:calc(var(--rp-bottom,98px) + 6px);max-height:var(--sig-mvh,46vh);font-size:12.5px;padding:6px 12px 8px}
  .sigp:not([open]){padding:4px 12px}
  .sg-list li{padding:6px 4px}
  .sg-tag{font-size:11px;line-height:17px}
  .rail-tip{left:50%;bottom:calc(72px + env(safe-area-inset-bottom,0px));transform:translateX(-50%) translateY(6px);text-align:center}
  .rail-tip.show{transform:translateX(-50%)}
  .foot{display:none}
}
</style>
<script src="/assets/chart-touch.js"></script>
</head>
<body>

<canvas id="c"></canvas>
<div class="warnbar" id="warnBar" role="status"></div>
<div class="rcol" id="rcol">
<details class="legend" id="legend" open>
  <summary><span id="lgTitle">社区</span><span class="lg-sub" id="lgSub"></span><button type="button" class="info-btn pill" id="guideBtn" data-info="legend" aria-haspopup="dialog" aria-controls="infoPop" aria-expanded="false" title="读图说明：光晕、颜色、大小、连线等编码">ⓘ 读图</button></summary>
  <div class="lg-colhd" aria-hidden="true"><span></span><span id="lgColName">名称</span><span class="c-up">上涨占比</span><span class="c-avg">均值</span></div>
  <div class="lg-comm" id="lgComm"></div>
</details>
<details class="sigp" id="sigPanel" open>
  <summary><span>异动观察</span><button type="button" class="info-btn" data-info="signals" aria-haspopup="dialog" aria-controls="infoPop" aria-expanded="false" aria-label="异动观察说明">ⓘ</button><span class="sg-date" id="sgDate"></span><span class="sg-peek" id="sgPeek"></span></summary>
  <ol class="sg-list" id="sgList"></ol>
</details>
</div>

<div id="infoPop" class="infopop" popover="auto" role="dialog" aria-modal="false" aria-labelledby="infoTitle"><div class="ip-hd"><b id="infoTitle"></b><button type="button" class="ip-x" id="infoClose" aria-label="关闭">×</button></div><div class="ip-body" id="infoBody"></div></div>
<div id="infoSrc" hidden><div id="lgGuideSrc">
  <div class="lg-row"><span class="lg-sw"><span class="lg-halo up"></span><span class="lg-halo dn"></span></span><span id="lgScale">光晕 = 涨跌（红涨 / 绿跌）</span></div>
  <div class="lg-row" id="lgRet"><span class="lg-sw"><span class="lg-grad"></span></span><span>节点色 = 涨跌（「按涨跌」模式）</span></div>
  <div id="lgCommBox"><div class="lg-row"><span class="lg-sw"><span class="cdot" style="background:#5ad1ff"></span><span class="cdot" style="background:#ffb347"></span></span><span id="lgCommHd">节点色 = 同步图社区 · 条 = 上涨占比，右为均值（点击进入该簇）</span></div></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-dot" style="width:6px;height:6px"></span><span class="lg-dot" style="width:12px;height:12px"></span></span><span id="lgSize">点大小 = 连接强度 Σ|ρ|（越大越居中）</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-line web"></span></span><span id="lgWeb">千丝万缕 = 全部同步边，越亮 |ρ| 越大</span></div>
  <div class="lg-row" id="lgSoft"><span class="lg-sw"><span class="lg-line soft"></span></span><span>同步边 · 补充（未复核，仅让孤立板块有参照）</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-ring"></span></span><span>虚线环 = 桥梁板块（跨社区、介数高）</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-line trailc"></span></span><span>青色折线 = 探索路径</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-arrow"></span></span><span>领先箭头（实验）：金 = 同向，蓝 = 反向；虚线 = 未通过 FDR</span></div>
  <div class="lg-row" id="lgStyleRow"><span class="lg-sw"><span class="cdot" style="background:#ff9f43"></span><span class="cdot" style="background:#5ad1ff"></span></span><span>风格视角：节点色 = 风格，细线 = 最近的同风格板块（规则见「着色」ⓘ）</span></div>
</div></div>
<nav class="rail" id="dockRail" aria-label="分析轨道">
  <button type="button" class="rail-btn active" data-mode="ctrl" id="railCtrl" title="搜索与筛选">控</button>
  <button type="button" class="rail-btn" data-mode="board" id="railBoard" title="涨跌榜">榜</button>
  <button type="button" class="rail-btn" data-mode="detail" id="railDetail" title="聚焦详情">详</button>
  <button type="button" class="rail-btn home" data-act="home" id="railHome" title="复位视角" aria-label="复位视角">⟲</button>
</nav>
<div class="rail-tip" id="zenTip">再点轨道图标可收起面板</div>
<div class="trail" id="trailBar" hidden aria-label="探索路径">
  <button type="button" id="trBack" title="后退（Alt+←）" aria-label="后退">‹</button>
  <button type="button" id="trFwd" title="前进（Alt+→）" aria-label="前进">›</button>
  <div class="crumbs" id="crumbs"></div>
</div>
<div class="tourcap" id="tourCap" role="status"></div>
<div class="replay" id="replayBar" hidden aria-label="逐日回放">
  <button type="button" id="rpPlay" title="回放近 N 个交易日" aria-label="播放">▶</button>
  <input type="range" id="rpSlider" min="0" max="0" step="1" value="0" aria-label="日期"/>
  <span class="rp-date" id="rpDate"></span>
  <div class="seg mini" id="rpPeriod">
    <button type="button" data-v="1">1日</button>
    <button type="button" data-v="5">5日</button>
    <button type="button" data-v="20">20日</button>
  </div>
  <span class="rp-ra" id="rpRA" hidden title="风险偏好 = 进攻均值 − 防御均值；折线 = 回放窗口内逐日价差累计（只到当前日期）"><span class="ra-lbl">风险偏好</span><b id="raVal">—</b><svg id="raSpark" viewBox="0 0 64 18" width="64" height="18" aria-hidden="true"></svg><button type="button" class="info-btn" data-info="risk" aria-haspopup="dialog" aria-controls="infoPop" aria-expanded="false" aria-label="风险偏好说明">ⓘ</button></span>
</div>

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
      <div class="chips expand" id="expandBox" style="display:none"></div>
      <div class="row">
        <div class="seg" id="retmode">
          <button type="button" data-v="1">当日</button>
          <button type="button" data-v="5">近5日</button>
          <button type="button" data-v="20">近20日</button>
        </div>
        <div class="seg" id="colormode" title="节点着色">
          <button type="button" data-v="community">按簇</button>
          <button type="button" data-v="return">按涨跌</button>
          <button type="button" data-v="style" id="cmStyle" title="风格视角：进攻 / 防御 / 周期 / 成长 / 价值">风格视角</button>
          <button type="button" data-v="sw1" id="cmSw1" title="按申万一级行业着色">按申万一级</button>
        </div>
        <button type="button" class="info-btn" data-info="style" aria-haspopup="dialog" aria-controls="infoPop" aria-expanded="false" aria-label="着色与风格说明">ⓘ</button>
        <button type="button" class="chip" id="styleLinkBtn" hidden title="同风格板块之间的细连线">同风格连线</button>
        <button type="button" class="chip" id="tourBtn" title="从全局到局部：社区 → 核心 → 桥梁">导览 ▶</button>
        <div class="seg" id="edgemode">
          <button type="button" class="active" data-v="sync">同步相关</button>
          <button type="button" data-v="lead">领先传导</button>
        </div>
        <button type="button" class="chip" id="showFailed" title="未通过 BH-FDR 的候选领先边默认隐藏；打开后以虚线显示">显示未通过FDR的边</button>
      </div>
      <div class="leadsum" id="leadSum"></div>
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
const DATA = __DATA_JSON__;
const LEAD_DISPLAY = DATA.lead_display || {};
const CFG_UI = LEAD_DISPLAY;  // 展示参数（config/sector_corr_cloud.yaml → display）

// three.js：优先本地 /assets/vendor/three（随仓库提供），失败才回落 jsDelivr，避免 CDN 故障导致白屏。
const THREE_SOURCES = [
  { name: 'local', three: '/assets/vendor/three/three.module.min.js', orbit: '/assets/vendor/three/OrbitControls.js' },
  { name: 'cdn', three: 'https://cdn.jsdelivr.net/npm/three@0.160.0/+esm', orbit: 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js/+esm' },
];
let THREE = null, OrbitControls = null, THREE_SOURCE = '';
for (const src of THREE_SOURCES) {
  try {
    const t = await import(src.three);
    const o = await import(src.orbit);
    THREE = t; OrbitControls = o.OrbitControls; THREE_SOURCE = src.name; break;
  } catch (e) { console.warn(`three.js ${src.name} 加载失败，尝试下一来源`, e && e.message); }
}
if (!THREE) {
  const bar = document.getElementById('warnBar');
  bar.innerHTML = '<div>⚠ 3D 组件 three.js 本地与 CDN 均加载失败，无法绘制点云。</div>'; bar.classList.add('show');
  throw new Error('three.js unavailable');
}
document.documentElement.dataset.three = THREE_SOURCE;

// 粗指针判断沿用共享 chart-touch.js（缺失时本地兜底，口径一致）。
function isCoarse() {
  if (window.ChartTouch && typeof window.ChartTouch.isCoarse === 'function') return window.ChartTouch.isCoarse();
  return !!(window.matchMedia && (matchMedia('(pointer:coarse)').matches || matchMedia('(max-width:760px)').matches));
}
const MOBILE_MQ = window.matchMedia('(max-width:900px)');
const TAP_CANCEL_PX = 10;  // 与 chart-touch.js 的 MOVE_CANCEL_PX 一致
document.documentElement.style.setProperty('--fly-mvh', (CFG_UI.mobile_panel_vh ?? 42) + 'vh');

// ---------------- 状态 ----------------
let retWindow = String(CFG_UI.default_period ?? 1);  // 区间：'1' | '5' | '20'（交易日）
let qMode = 'sector';
let mode = CFG_UI.default_mode === 'lead' ? 'lead' : 'sync';
let coreIds = new Set();
let focusIds = new Set();
let expandOptions = [];      // 查询扩展出的可选板块（默认不聚焦）
const GRAPH = DATA.graph || { communities: [], node_comm: {}, bridges: [], wdeg: {} };
const COMM = GRAPH.communities || [];
const commById = Object.fromEntries(COMM.map(c => [c.id, c]));
const BRIDGES = new Map((GRAPH.bridges || []).map(b => [b.id, b]));
function commOf(id) { const k = (GRAPH.node_comm || {})[id]; return k == null ? -1 : k; }
function commName(k) { return commById[k] ? commById[k].name : '零散'; }
let colorBy = (CFG_UI.color_by === 'return' || !COMM.length) ? 'return' : 'community';
// ---------------- 风格（进攻 / 防御 / 周期 / 成长 / 价值；生成器按历史 beta·波动·回撤 + 市盈率 + 配置覆盖打标签） ----------------
const STYLE = (DATA.style && DATA.style.tags) ? DATA.style : null;
const STYLES = STYLE ? STYLE.styles : [];
const STYLE_CFG = (STYLE && STYLE.view) || {};
function styleOf(id) { return STYLE && STYLE.tags[id] ? STYLE.tags[id].p : null; }
function styleHex(k) { return (STYLE && STYLE.colors[k]) || '#6b778c'; }
function styleColor(id) { return new THREE.Color(styleHex(styleOf(id))); }
const STYLE_ABBR = { 进攻: '攻', 防御: '防', 周期: '周', 成长: '成', 价值: '价' };
function styleText(id) { const t = STYLE && STYLE.tags[id]; return t ? t.p + (t.s ? ` / ${t.s}` : '') : ''; }
// 名称后的紧凑风格标签（列表 / 异动 / 涨跌榜）
function styleTag(id, full = false) {
  const t = STYLE && STYLE.tags[id]; if (!t) return '';
  return `<span class="stag" style="--sc:${styleHex(t.p)}" title="风格：${styleText(id)}">${full ? t.p : (STYLE_ABBR[t.p] || t.p)}</span>`;
}
// 风格筛选：只高亮某一风格，其余变暗；与聚焦（lit）相乘，回放照常
let styleFilter = null;
const STYLE_DIM = 0.12;
const sMask = new Float32Array(DATA.nodes.length).fill(1);
// 申万一级：节点自带 l1；选中某个一级 = 一种聚焦状态（与板块聚焦、社区聚焦互斥），着色另有「按申万一级」
const SW1_MEMBERS = {};
for (const n of DATA.nodes) (SW1_MEMBERS[n.l1 || '未分类'] ||= []).push(n.id);
const SW1_LIST = Object.keys(SW1_MEMBERS).sort((a, b) => SW1_MEMBERS[b].length - SW1_MEMBERS[a].length || a.localeCompare(b, 'zh'));
const SW1_COLOR = Object.fromEntries(SW1_LIST.map((k, i) => [k, `hsl(${Math.round((i * 137.508 + 200) % 360)}, 62%, 62%)`]));
let sw1Sel = null, sw1Sum = {};
let conceptSel = null;  // 题材叠加（与板块 / 社区 / 一级聚焦互斥），见下方「题材 / 概念叠加层」
function sw1Of(id) { return (SW1_NODE[id] && SW1_NODE[id].l1) || '未分类'; }
const SW1_NODE = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
function sw1Agg(k) {
  const vs = (SW1_MEMBERS[k] || []).map(id => retOf(SW1_NODE[id])).filter(v => v != null);
  return vs.length ? { avg: vs.reduce((a, b) => a + b, 0) / vs.length, up: vs.filter(v => v > 0).length / vs.length, nUp: vs.filter(v => v > 0).length, n: vs.length } : null;
}
function styleQuery(q) { const m = String(q || '').trim().match(/^(进攻|防御|周期|成长|价值)(型|类|板块|风格)?$/); return m && STYLE ? m[1] : null; }
let styleSum = {}, styleLinksOn = STYLE_CFG.links ?? true;
const REDUCED = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const DIM_FLOOR = CFG_UI.web_focus_dim ?? 0.12;
const TRAIL_MAX = CFG_UI.trail_max ?? 12;
let commFocus = null;           // 当前聚焦的社区（图例 / 导览 / 详情里的“所属簇”）
let trail = [], trailIdx = -1;  // 探索路径：每次点击邻居前进一步，支持后退/前进
const LEAD_FOCUSED_TOTAL_LIMIT = LEAD_DISPLAY.focus_total ?? 12;
const LEAD_PER_CORE_DIRECTION = LEAD_DISPLAY.per_core_direction ?? 3;
const LEAD_PANEL_LIMIT = 6;
let showFailed = !!LEAD_DISPLAY.show_failed_default;
const LEAD_SIG_COUNT = (DATA.lead_summary || {}).fdr_pass;

// ---------------- 场景 ----------------
const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.setClearColor(0x070b14, 1);
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x070b14, 0.0018);
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 2000);
const HOME = { pos: new THREE.Vector3(0, 45, 145), target: new THREE.Vector3(0, 0, 0) };
if (innerWidth / innerHeight < 0.8) HOME.pos.multiplyScalar(1.4);  // 竖屏手机：全局视角拉远一些，看到整张网
camera.position.copy(HOME.pos);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.autoRotateSpeed = CFG_UI.auto_rotate_speed ?? 0.55;
controls.minDistance = 35;
controls.maxDistance = 380;
// 自转常开：仅在拖动/捏合/滚轮期间暂停，松手 rotate_resume_ms 后恢复；聚焦时绕焦点慢速环绕。
let dragging = false, resumeTimer = 0, flight = null;
const ROTATE_ON = (CFG_UI.auto_rotate ?? true) && (!REDUCED || !!CFG_UI.auto_rotate_reduced_motion);
function syncAutoRotate() {
  controls.autoRotate = ROTATE_ON && !dragging && !flight;
  controls.autoRotateSpeed = (coreIds.size || commFocus != null || sw1Sel || conceptSel) ? (CFG_UI.focus_orbit_speed ?? 0.3) : (CFG_UI.auto_rotate_speed ?? 0.55);
}
function pauseRotate() { dragging = true; clearTimeout(resumeTimer); syncAutoRotate(); }
function scheduleResume() { clearTimeout(resumeTimer); resumeTimer = setTimeout(() => { dragging = false; syncAutoRotate(); }, CFG_UI.rotate_resume_ms ?? 1500); }
controls.addEventListener('start', () => { pauseRotate(); cancelFlight(); stopTour(); });
controls.addEventListener('end', scheduleResume);
canvas.addEventListener('wheel', () => { pauseRotate(); scheduleResume(); }, { passive: true });
syncAutoRotate();

{
  const n = 900, pos = new Float32Array(n * 3);
  for (let i = 0; i < n * 3; i++) pos[i] = (Math.random() - 0.5) * 520;
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  scene.add(new THREE.Points(g, new THREE.PointsMaterial({ color: 0x243552, size: 0.55, transparent: true, opacity: 0.5 })));
}

const nodeById = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const N = DATA.nodes.length;
const idxOf = Object.fromEntries(DATA.nodes.map((n, i) => [n.id, i]));
const ADJ = {};
function addAdj(a, b, c) { if (!ADJ[a]) ADJ[a] = new Map(); const cur = ADJ[a].get(b); if (cur == null || Math.abs(c) > Math.abs(cur)) ADJ[a].set(b, c); }
for (const [id, rows] of Object.entries(DATA.neighbors || {})) for (const r of rows) { addAdj(id, r.id, r.corr); addAdj(r.id, id, r.corr); }
for (const e of DATA.edges) { addAdj(e.source, e.target, e.corr); addAdj(e.target, e.source, e.corr); }
const EDGE_SET = new Set(DATA.edges.filter(e => !e.soft).flatMap(e => [e.source + '|' + e.target, e.target + '|' + e.source]));
function rhoOf(a, b) { return ADJ[a] ? ADJ[a].get(b) : undefined; }

function radialTexture(stops) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 31);
  for (const [o, col] of stops) g.addColorStop(o, col);
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(32, 32, 31, 0, Math.PI * 2); ctx.fill();
  return new THREE.CanvasTexture(c);
}
// 圆形节点：实心圆盘（边缘轻微羽化）+ 叠加光晕。
const discTex = radialTexture([[0, 'rgba(255,255,255,1)'], [0.78, 'rgba(255,255,255,1)'], [1, 'rgba(255,255,255,0)']]);
const glowTex = radialTexture([[0, 'rgba(255,255,255,1)'], [0.4, 'rgba(255,255,255,.45)'], [1, 'rgba(255,255,255,0)']]);
const discs = [], glows = [];
for (let i = 0; i < N; i++) {
  const n = DATA.nodes[i];
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, color: 0x88aacc, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.3 }));
  glow.position.set(n.x, n.y, n.z); scene.add(glow); glows.push(glow);
  const disc = new THREE.Sprite(new THREE.SpriteMaterial({ map: discTex, color: 0x8899aa, transparent: true, depthWrite: false }));
  disc.position.set(n.x, n.y, n.z); disc.renderOrder = 2; scene.add(disc); discs.push(disc);
}

// ---------------- 收益：近 N 日逐日序列 → 1/5/20 日复利；光晕 / 边着色读平滑插值后的 retCur ----------------
const DAILY = (DATA.daily && DATA.daily.dates && DATA.daily.dates.length) ? DATA.daily : null;
const R_DAYS = DAILY ? Math.max(1, DATA.daily.replay_days) : 1;
const R_DATES = DAILY ? DAILY.dates.slice(-R_DAYS) : [DATA.market_as_of || DATA.end || ''];
const PERIODS = DAILY ? (DAILY.periods || [1, 5, 20]) : [1, 20];
if (!PERIODS.includes(+retWindow)) retWindow = String(PERIODS[0]);
let dayIdx = R_DAYS - 1;
function quantile(arr, q) {
  if (!arr.length) return 0;
  const a = [...arr].sort((x, y) => x - y), pos = (a.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return a[lo] + (a[hi] - a[lo]) * (pos - lo);
}
const RET = {};   // RET[P][d][i]：区间 P、回放第 d 天、第 i 个板块的收益（%），NaN = 无行情
{
  const rowOf = {}; if (DAILY) DAILY.ids.forEach((id, k) => { rowOf[id] = DAILY.bp[k]; });
  const off = DAILY ? DAILY.dates.length - R_DAYS : 0;
  for (const P of PERIODS) {
    RET[P] = [];
    for (let d = 0; d < R_DAYS; d++) {
      const a = new Float32Array(N).fill(NaN);
      for (let i = 0; i < N; i++) {
        const n = DATA.nodes[i];
        if (DAILY) {
          const row = rowOf[n.id]; if (!row) continue;
          const end = off + d; let g = 1, any = false;
          for (let k = Math.max(0, end - P + 1); k <= end; k++) { const v = row[k]; if (v == null) continue; g *= 1 + v / 10000; any = true; }
          if (any) a[i] = (g - 1) * 100;
        } else {
          const v = P === 1 ? n.last : n.cum20;
          if (v != null && !Number.isNaN(+v)) a[i] = +v;
        }
      }
      RET[P].push(a);
    }
  }
}
const CLIP_Q = CFG_UI.color_clip_pct ?? 0.9;
const CLIP = {};  // 每个区间在整个回放窗口上的 |收益| 分位封顶，跨日可比
for (const P of PERIODS) {
  const v = []; for (const a of RET[P]) for (const x of a) if (!Number.isNaN(x)) v.push(Math.abs(x));
  CLIP[P] = Math.max(CFG_UI.min_ret_scale ?? 0.5, quantile(v, CLIP_Q));
}
function curP() { return +retWindow; }
function periodLabel(P = curP()) { return P === 1 ? '当日' : `近${P}日`; }
function retAt(P, d, i) { const v = RET[P] ? RET[P][d][i] : NaN; return Number.isNaN(v) ? null : v; }
function retOf(n) { return retAt(curP(), dayIdx, idxOf[n.id]); }
let RET_SCALE = CLIP[curP()];
function computeRetScale() {}  // 兼容旧调用：封顶值由 CLIP[区间] 给出并随回放插值
const retCur = new Float32Array(N), retFrom = new Float32Array(N), retTo = new Float32Array(N), hasRet = new Uint8Array(N);
let retT0 = 0, retDur = 1, retAnim = false, scaleFrom = RET_SCALE, scaleTo = RET_SCALE;
let movers = new Set(), commSum = {};
function setRetTargets(ms = CFG_UI.replay_tween_ms ?? 650) {
  const P = curP(), a = RET[P][dayIdx];
  for (let i = 0; i < N; i++) { retFrom[i] = retCur[i]; const v = a[i]; hasRet[i] = Number.isNaN(v) ? 0 : 1; retTo[i] = Number.isNaN(v) ? 0 : v; }
  scaleFrom = RET_SCALE; scaleTo = CLIP[P];
  retT0 = performance.now(); retDur = REDUCED ? 1 : Math.max(1, ms); retAnim = true;
  computeMovers(); updateCommSummary();
}
function stepRet(now) {
  if (!retAnim) return false;
  const u = Math.min(1, (now - retT0) / retDur), e = u * u * (3 - 2 * u);
  for (let i = 0; i < N; i++) retCur[i] = retFrom[i] + (retTo[i] - retFrom[i]) * e;
  RET_SCALE = scaleFrom + (scaleTo - scaleFrom) * e;
  if (u >= 1) retAnim = false;
  return true;
}
// 强势异动：|收益| ≥ 封顶，或 |收益| 前 N 且 ≥ 半个封顶 → 光晕轻微呼吸
function computeMovers() {
  const clip = CLIP[curP()], rows = [];
  for (let i = 0; i < N; i++) if (hasRet[i]) rows.push([i, Math.abs(retTo[i])]);
  rows.sort((a, b) => b[1] - a[1]);
  const topN = CFG_UI.breathe_top_n ?? 8, cap = CFG_UI.breathe_max ?? 16;
  movers = new Set(rows.filter((r, k) => r[1] >= clip || (k < topN && r[1] >= 0.5 * clip)).slice(0, cap).map(r => r[0]));
}
function sumText(k) { const v = commSum[k]; return v ? `${fmtRet(v.avg)}，${Math.round(v.up * 100)}% 上涨` : '—'; }
function updateCommSummary() {
  commSum = {}; styleSum = {};
  if (STYLE) {
    const acc = {};
    for (let i = 0; i < N; i++) { if (!hasRet[i]) continue; const k = styleOf(DATA.nodes[i].id); if (!k) continue; const a = acc[k] || (acc[k] = [0, 0, 0]); a[0] += retTo[i]; a[1]++; if (retTo[i] > 0) a[2]++; }
    for (const k of STYLES) { const a = acc[k]; styleSum[k] = a && a[1] ? { avg: a[0] / a[1], up: a[2] / a[1], n: a[1] } : null; }
    updateRiskAppetite();
  }
  sw1Sum = {};
  for (const k of SW1_LIST) {
    let s0 = 0, n0 = 0, up = 0;
    for (const id of SW1_MEMBERS[k]) { const i = idxOf[id]; if (i == null || !hasRet[i]) continue; s0 += retTo[i]; n0++; if (retTo[i] > 0) up++; }
    sw1Sum[k] = n0 ? { avg: s0 / n0, up: up / n0, n: n0 } : null;
  }
  for (const c of COMM) {
    let s0 = 0, n0 = 0, up = 0;
    for (const id of c.members) { const i = idxOf[id]; if (i == null || !hasRet[i]) continue; s0 += retTo[i]; n0++; if (retTo[i] > 0) up++; }
    commSum[c.id] = n0 ? { avg: s0 / n0, up: up / n0, n: n0 } : null;
  }
  renderCommLegend();
  updateRetDom();
}
// 详情/社区列表中随日期、区间变化的数字
function updateRetDom() {
  document.querySelectorAll('[data-csum]').forEach(el => { const v = commSum[+el.dataset.csum]; el.textContent = sumText(+el.dataset.csum); el.className = v ? (v.avg >= 0 ? 'pos' : 'neg') : ''; });
  document.querySelectorAll('[data-ret]').forEach(el => { const v = retAt(+el.dataset.ret, dayIdx, idxOf[el.dataset.id]); el.textContent = fmtRet(v); el.className = v == null ? '' : (v >= 0 ? 'pos' : 'neg'); });
  document.querySelectorAll('[data-retdate]').forEach(el => { el.textContent = R_DATES[dayIdx] || '-'; });
}
function colorFromRet(v, dim = 1) {
  const c = new THREE.Color();
  if (v == null) { c.setRGB(0.35 * dim, 0.4 * dim, 0.48 * dim); return c; }
  const t = Math.max(-1, Math.min(1, v / RET_SCALE));
  if (t >= 0) c.setRGB((0.45 + 0.55 * t) * dim, (0.18 + 0.05 * (1 - t)) * dim, (0.18 + 0.05 * (1 - t)) * dim);
  else { const u = -t; c.setRGB((0.12 + 0.05 * (1 - u)) * dim, (0.45 + 0.5 * u) * dim, (0.32 + 0.2 * u) * dim); }
  return c;
}
function sizeFromRet(v) {
  if (v == null) return 3.2;
  return 5.5 + 10.0 * Math.min(1, Math.abs(v) / RET_SCALE);
}
const WDEG_MAX = Math.max(1e-6, ...Object.values(GRAPH.wdeg || { _: 1 }));
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
      for (const nb of (DATA.lead_out[c] || [])) if (nb.id === nodeId && leadVisible(nb)) best = Math.max(best, nb.abs ?? Math.abs(nb.xcorr || 0));
      for (const nb of (DATA.lead_in[c] || [])) if (nb.id === nodeId && leadVisible(nb)) best = Math.max(best, nb.abs ?? Math.abs(nb.xcorr || 0));
    }
  }
  return best;
}
// 节点基础色（社区 / 涨跌）与基础大小；lit 为逐节点亮度（1=常态，>1=核心加亮，<1=压暗），过渡时逐帧插值。
const baseCol = DATA.nodes.map(() => new THREE.Color()), baseSize = new Float32Array(N);
const lit = new Float32Array(N).fill(1), litFrom = new Float32Array(N).fill(1), litTo = new Float32Array(N).fill(1);
const litT0 = new Float32Array(N), litDur = new Float32Array(N).fill(1);
let litAnimUntil = 0;
function commColor(id) { const c = commById[commOf(id)]; return new THREE.Color(c ? c.color : (GRAPH.loose_color || '#6b778c')); }
function computeBase() {
  for (let i = 0; i < N; i++) {
    const n = DATA.nodes[i];
    baseCol[i].copy(colorBy === 'style' ? styleColor(n.id) : colorBy === 'sw1' ? new THREE.Color(SW1_COLOR[n.l1 || '未分类']) : commColor(n.id));
    baseSize[i] = 2.0 + 2.6 * Math.sqrt(((GRAPH.wdeg || {})[n.id] ?? 0) / WDEG_MAX);  // 点大小 = 连接强度
  }
}
const _c = new THREE.Color(), _h = new THREE.Color();
const HALO_UP = new THREE.Color(CFG_UI.halo_up_color || '#ff4d4f'), HALO_DN = new THREE.Color(CFG_UI.halo_down_color || '#22d38a');
let breathT = 0;
// 节点核心 = 社区色（「按涨跌」时为涨跌色）；光晕 = 涨跌：红涨绿跌，大小/亮度 ∝ |收益|/封顶，强势异动轻微呼吸。
function paintNode(i) {
  const L = lit[i] * sMask[i], l1 = Math.min(1, L), boost = Math.max(0, L - 1), vis = DIM_FLOOR + (1 - DIM_FLOOR) * l1;
  const r = hasRet[i] ? retCur[i] : null;
  if (colorBy !== 'return') _c.copy(baseCol[i]); else _c.copy(colorFromRet(r, 1));
  _c.multiplyScalar(vis);
  discs[i].material.color.copy(_c);
  discs[i].material.opacity = 0.45 + 0.55 * l1;
  const base = colorBy !== 'return' ? baseSize[i] : sizeFromRet(r) * 0.34;
  const ds = Math.max(1.3, base * (0.55 + 0.45 * l1 + boost * 1.7));
  discs[i].scale.set(ds, ds, 1);
  const t = r == null ? 0 : Math.min(1, Math.abs(r) / Math.max(0.01, RET_SCALE));
  const br = (!REDUCED && movers.has(i)) ? Math.sin(2 * Math.PI * breathT * 1000 / (CFG_UI.breathe_period_ms ?? 1600) + i * 1.7) : 0;
  const amp = CFG_UI.breathe_amp ?? 0.18;
  _h.copy(r != null && r < 0 ? HALO_DN : HALO_UP).multiplyScalar(vis);
  glows[i].material.color.copy(_h);
  glows[i].material.opacity = (0.015 + (CFG_UI.halo_max_opacity ?? 0.62) * Math.pow(t, 0.85)) * (0.3 + 0.7 * l1) * (1 + amp * br);
  const gs = ds * (1.1 + (CFG_UI.halo_max_scale ?? 3.4) * t) * (1 + amp * 0.6 * br);
  glows[i].scale.set(gs, gs, 1);
}
// 桥梁板块：淡金色虚线环
const ringTex = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d');
  x.strokeStyle = 'rgba(255,255,255,0.95)'; x.lineWidth = 3; x.setLineDash([7, 5]);
  x.beginPath(); x.arc(32, 32, 27, 0, Math.PI * 2); x.stroke();
  return new THREE.CanvasTexture(c);
})();
const bridgeRings = [];
for (const b of BRIDGES.values()) {
  const i = idxOf[b.id]; if (i == null) continue; const n = DATA.nodes[i];
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: ringTex, color: 0xffe9a8, transparent: true, opacity: 0.5, depthWrite: false }));
  sp.position.set(n.x, n.y, n.z); sp.renderOrder = 3; sp.userData.i = i; scene.add(sp); bridgeRings.push(sp);
}
function paintRings() {
  for (const sp of bridgeRings) {
    const i = sp.userData.i, l1 = Math.min(1, lit[i] * sMask[i]);
    const r = Math.max(4.2, discs[i].scale.x * 2.3); sp.scale.set(r, r, 1);
    sp.material.opacity = 0.12 + 0.45 * l1;
  }
}
function applyNodeAppearance() {
  computeBase();
  for (let i = 0; i < N; i++) paintNode(i);
  paintRings(); updateWebColors(); renderLegend();
}

// ---------------- 同步边：多年复核 = 实线，补充（soft）= 暗色虚线 ----------------
function buildSyncLines(soft) {
  const pos = [], col = [];
  const cPos = new THREE.Color(0xff7a7a), cNeg = new THREE.Color(0x3ddc97), cSoft = new THREE.Color(0x8fa3c4);
  for (const e of DATA.edges) {
    if (!!e.soft !== soft) continue;
    const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
    pos.push(a.x, a.y, a.z, b.x, b.y, b.z);
    const base = soft ? cSoft : (e.sign >= 0 ? cPos : cNeg);
    const w = soft ? 0.55 : 0.18 + 0.55 * Math.max(0, Math.min(1, (e.abs - 0.35) / 0.4));
    col.push(base.r * w, base.g * w, base.b * w, base.r * w, base.g * w, base.b * w);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  const mat = soft
    ? new THREE.LineDashedMaterial({ vertexColors: true, transparent: true, opacity: 0.32, dashSize: 1.1, gapSize: 1.6, depthWrite: false })
    : new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.3, depthWrite: false });
  const line = new THREE.LineSegments(geo, mat);
  if (soft) line.computeLineDistances();
  return line;
}
// “千丝万缕”：全部同步边合并为一个 LineSegments（一次绘制），加色混合，亮度随 |ρ|；
// 社区着色时两端取各自社区色形成渐变，聚焦时按两端 lit 逐帧压暗/加亮。
const WEB = (() => {
  const list = DATA.edges.filter(e => !e.soft && nodeById[e.source] && nodeById[e.target]);
  const pos = new Float32Array(list.length * 6), col = new Float32Array(list.length * 6), w = new Float32Array(list.length);
  const lo = DATA.corr_thr || 0.4, wMin = CFG_UI.web_opacity_min ?? 0.07, wMax = CFG_UI.web_opacity_max ?? 0.6;
  list.forEach((e, k) => {
    const a = nodeById[e.source], b = nodeById[e.target];
    pos.set([a.x, a.y, a.z, b.x, b.y, b.z], k * 6);
    const t = Math.max(0, Math.min(1, ((e.abs ?? Math.abs(e.corr || 0)) - lo) / Math.max(0.05, 0.85 - lo)));
    w[k] = wMin + (wMax - wMin) * Math.pow(t, 1.2);
  });
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const line = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 1, blending: THREE.AdditiveBlending, depthWrite: false }));
  line.renderOrder = 1;
  return { list, w, col, line, ia: list.map(e => idxOf[e.source]), ib: list.map(e => idxOf[e.target]) };
})();
const syncLines = WEB.line; scene.add(syncLines);
// 风格视角：每个点连到最近 link_k 个同风格点（去重后合并为一个 LineSegments，一次绘制）
const styleLinks = (() => {
  if (!STYLE) return null;
  const K = STYLE_CFG.link_k ?? 2, seen = new Set(), pos = [], col = [], segStyle = [];
  const ns = DATA.nodes;
  for (let i = 0; i < ns.length; i++) {
    const si = styleOf(ns[i].id); if (!si) continue;
    const near = [];
    for (let j = 0; j < ns.length; j++) {
      if (j === i || styleOf(ns[j].id) !== si) continue;
      const dx = ns[i].x - ns[j].x, dy = ns[i].y - ns[j].y, dz = ns[i].z - ns[j].z;
      near.push([dx * dx + dy * dy + dz * dz, j]);
    }
    near.sort((a, b) => a[0] - b[0]);
    const c = new THREE.Color(styleHex(si));
    for (const [, j] of near.slice(0, K)) {
      const key = i < j ? i + ':' + j : j + ':' + i; if (seen.has(key)) continue; seen.add(key);
      pos.push(ns[i].x, ns[i].y, ns[i].z, ns[j].x, ns[j].y, ns[j].z);
      col.push(c.r, c.g, c.b, c.r, c.g, c.b); segStyle.push(si);
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
  const line = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: STYLE_CFG.link_opacity ?? 0.5, depthWrite: false, blending: THREE.AdditiveBlending }));
  line.renderOrder = 1; line.visible = false; line.userData.n = seen.size; line.userData.seg = segStyle; line.userData.col0 = Float32Array.from(col); scene.add(line);
  return line;
})();
const softLines = buildSyncLines(true); scene.add(softLines);
const cPosE = new THREE.Color(0xff7a7a), cNegE = new THREE.Color(0x3ddc97), cNegC = new THREE.Color(0x8fa3c4);
const cGreyE = new THREE.Color(0x8796b0), _ea = new THREE.Color(), _eb = new THREE.Color();
const TINT_ON = CFG_UI.edge_tint ?? true, TINT_MIX = CFG_UI.edge_tint_mix ?? 0.85;
// 边着色：两端同涨发红、同跌发绿（强度 ∝ min(|r1|,|r2|)/封顶 × |ρ|），方向相反变暗；仍是一个 LineSegments 的顶点色。
function updateWebColors() {
  const col = WEB.col, modeF = mode === 'lead' ? 0.35 : 1, focused = focusIds.size > 0, isComm = colorBy !== 'return';
  if (styleLinks) { styleLinks.visible = colorBy === 'style' && styleLinksOn; styleLinks.material.opacity = (STYLE_CFG.link_opacity ?? 0.5) * (focused ? 0.3 : 1); }
  for (let k = 0; k < WEB.list.length; k++) {
    const e = WEB.list[k], i = WEB.ia[k], j = WEB.ib[k], la = lit[i] * sMask[i], lb = lit[j] * sMask[j];
    let emph = Math.min(1, la, lb); emph = Math.max(0.05, emph * emph);
    if (focused && (la > 1.01 || lb > 1.01)) emph = Math.min(1.9, emph * 2.0);  // 与核心相连的边加亮
    let g = WEB.w[k] * emph * modeF;
    _ea.copy(e.sign >= 0 ? (isComm ? baseCol[i] : cGreyE) : cNegC);
    _eb.copy(e.sign >= 0 ? (isComm ? baseCol[j] : cGreyE) : cNegC);
    if (TINT_ON && hasRet[i] && hasRet[j]) {
      const r1 = retCur[i], r2 = retCur[j];
      const m = Math.min(1, Math.min(Math.abs(r1), Math.abs(r2)) / Math.max(0.01, RET_SCALE));
      if ((r1 > 0 && r2 > 0) || (r1 < 0 && r2 < 0)) {
        const mix = TINT_MIX * Math.min(1, m * 1.6), tc = r1 > 0 ? HALO_UP : HALO_DN;
        _ea.lerp(tc, mix); _eb.lerp(tc, mix); g *= 0.55 + 1.1 * m;
      } else g *= 0.4;
    }
    const o = k * 6;
    col[o] = _ea.r * g; col[o + 1] = _ea.g * g; col[o + 2] = _ea.b * g;
    col[o + 3] = _eb.r * g; col[o + 4] = _eb.g * g; col[o + 5] = _eb.b * g;
  }
  syncLines.geometry.attributes.color.needsUpdate = true;
}

// ---------------- 过渡：镜头弧线飞行 + 逐跳点亮 + 沿边脉冲 ----------------
function T_MS() { return REDUCED ? (CFG_UI.reduced_motion_ms ?? 350) : (CFG_UI.transition_ms ?? 1700); }
function tweenNode(i, to, delay, dur, now) {
  litFrom[i] = lit[i]; litTo[i] = to; litT0[i] = now + delay; litDur[i] = Math.max(1, dur);
  litAnimUntil = Math.max(litAnimUntil, now + delay + dur);
}
const PULSE_MAX = CFG_UI.pulse_max ?? 160;
const pulseGeo = new THREE.BufferGeometry();
pulseGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(PULSE_MAX * 3), 3));
pulseGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(PULSE_MAX * 3), 3));
pulseGeo.setDrawRange(0, 0);
const pulsePts = new THREE.Points(pulseGeo, new THREE.PointsMaterial({ size: 4.2, map: glowTex, vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
pulsePts.frustumCulled = false; pulsePts.renderOrder = 4; scene.add(pulsePts);
let pulses = [];
function clearPulses() { pulses = []; pulseGeo.setDrawRange(0, 0); }
function addPulse(a, b, t0, dur, color, gain = 1) {
  if (pulses.length >= PULSE_MAX) return;
  const A = nodeById[a], B = nodeById[b]; if (!A || !B) return;
  pulses.push({ A, B, t0, dur: Math.max(140, dur), c: new THREE.Color(color).multiplyScalar(gain) });
}
function stepPulses(now) {
  if (!pulses.length) return;
  pulses = pulses.filter(p => now < p.t0 + p.dur + 260);
  const P = pulseGeo.attributes.position.array, C = pulseGeo.attributes.color.array; let k = 0;
  for (const p of pulses) {
    const u = (now - p.t0) / p.dur; if (u < 0) continue;
    const t = Math.min(1, u), e = t * t * (3 - 2 * t), f = u > 1 ? Math.max(0, 1 - (u - 1) * p.dur / 260) : 1;
    P[k * 3] = p.A.x + (p.B.x - p.A.x) * e; P[k * 3 + 1] = p.A.y + (p.B.y - p.A.y) * e; P[k * 3 + 2] = p.A.z + (p.B.z - p.A.z) * e;
    C[k * 3] = p.c.r * f; C[k * 3 + 1] = p.c.g * f; C[k * 3 + 2] = p.c.b * f; k++;
  }
  pulseGeo.setDrawRange(0, k);
  pulseGeo.attributes.position.needsUpdate = true; pulseGeo.attributes.color.needsUpdate = true;
}
function nearestCore(id, cores) {
  let best = null, bv = -1;
  for (const c of cores) { const r = rhoOf(c, id); const v = r == null ? -0.5 : Math.abs(r); if (v > bv) { bv = v; best = c; } }
  return best;
}
let flowFade = 1, flowFadeT0 = 0, flowFadeDur = 1;
function startTransition({ origin = null, cores = new Set(), hop1 = new Set(), hop2 = new Set(), strength = () => 1 } = {}) {
  const now = performance.now(), T = T_MS(), quick = REDUCED;
  const focused = cores.size > 0 || hop1.size > 0;
  const o = origin ? nodeById[origin] : null;
  let maxD = 1;
  if (o) for (const id of hop1) { const n = nodeById[id]; if (n) maxD = Math.max(maxD, Math.hypot(n.x - o.x, n.y - o.y, n.z - o.z)); }
  clearPulses();
  const arrive = {};
  for (let i = 0; i < N; i++) {
    const id = DATA.nodes[i].id;
    if (!focused) { tweenNode(i, 1, quick ? 0 : 0.18 * T * ((i * 0.618) % 1), quick ? T : 0.6 * T, now); continue; }
    if (cores.has(id)) { tweenNode(i, 1.3, 0, quick ? T : 0.22 * T, now); arrive[id] = 0; }
    else if (hop1.has(id)) {
      const n = nodeById[id], d = o ? Math.hypot(n.x - o.x, n.y - o.y, n.z - o.z) / maxD : 0.5;
      const t = Math.max(0, Math.min(1, strength(id)));
      const delay = quick ? 0 : T * (0.1 + 0.32 * d);
      arrive[id] = delay;
      tweenNode(i, 0.55 + 0.45 * t, delay, quick ? T : 0.22 * T, now);
    } else if (hop2.has(id)) tweenNode(i, 0.4, quick ? 0 : T * (0.52 + 0.18 * ((i * 0.618) % 1)), quick ? T : 0.3 * T, now);
    else tweenNode(i, DIM_FLOOR, quick ? 0 : 0.1 * T, quick ? T : 0.8 * T, now);
  }
  if (!quick && focused) {
    for (const id of hop1) { const src = (origin && cores.has(origin) && rhoOf(origin, id) != null) ? origin : nearestCore(id, cores); if (src) addPulse(src, id, now, arrive[id], 0xdff8ff, 1); }
    let n2 = 0;
    for (const id of hop2) {
      if (n2 >= 60) break;
      let par = null, pv = -1;
      for (const h of hop1) { const r = rhoOf(h, id); if (r != null && Math.abs(r) > pv) { pv = Math.abs(r); par = h; } }
      if (par) { addPulse(par, id, now + arrive[par], T * 0.28, 0x9ff3ff, 0.55); n2++; }
    }
  }
  flowFadeT0 = now + (quick ? 0 : 0.45 * T); flowFadeDur = quick ? 1 : 0.5 * T; flowFade = 0;
}
function stepLit(now) {
  if (now > litAnimUntil + 40) return false;
  for (let i = 0; i < N; i++) {
    const u = (now - litT0[i]) / litDur[i];
    if (u <= 0) continue;
    const t = Math.min(1, u), e = t * t * (3 - 2 * t);
    lit[i] = litFrom[i] + (litTo[i] - litFrom[i]) * e;
    paintNode(i);
  }
  paintRings(); updateWebColors(); updateLabelOpacity();
  return true;
}
function flyTo(target, pos, ms = T_MS()) {
  const d = camera.position.distanceTo(pos) + controls.target.distanceTo(target);
  flight = { t0: performance.now(), dur: Math.max(1, ms), p0: camera.position.clone(), p1: pos.clone(), q0: controls.target.clone(), q1: target.clone(),
    arc: REDUCED ? 0 : (CFG_UI.arc_height ?? 0.22) * d };
  syncAutoRotate();
}
function cancelFlight() { if (flight) { flight = null; syncAutoRotate(); } }
const _back = new THREE.Vector3();
function stepFlight(now) {
  if (!flight) return false;
  const f = flight, u = Math.min(1, (now - f.t0) / f.dur);
  const e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
  controls.target.lerpVectors(f.q0, f.q1, e);
  camera.position.lerpVectors(f.p0, f.p1, e);
  if (f.arc) {
    const h = Math.sin(Math.PI * e) * f.arc;
    _back.copy(camera.position).sub(controls.target).normalize();
    camera.position.addScaledVector(_back, h * 0.6); camera.position.y += h * 0.5;
  }
  camera.lookAt(controls.target);
  if (u >= 1) { flight = null; controls.update(); syncAutoRotate(); }
  return true;
}

// ---------------- 领先边（实验） ----------------
function edgePass(edge) { return edge.fdr_pass !== false; }
function leadVisible(edge) { return showFailed || edgePass(edge); }
function leadStrength(edge) {
  const lift = edge.lift != null ? Math.abs(Number(edge.lift) || 0)
    : Math.max(Math.abs(Number(edge.lift_up) || 0), Math.abs(Number(edge.lift_dn) || 0));
  return (Number(edge.abs) || Math.abs(Number(edge.xcorr) || 0)) + Math.min(0.2, lift) * 0.35;
}
function sortLeadEdges(edges) {
  return [...edges].sort((a, b) => (edgePass(b) - edgePass(a)) || leadStrength(b) - leadStrength(a) || (Number(b.abs) || 0) - (Number(a.abs) || 0));
}
function visibleLeadEdges(filterIds = null) {
  const all = (DATA.lead_edges || []).filter(leadVisible);
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
const leadGroup = new THREE.Group(); scene.add(leadGroup); leadGroup.visible = false;
function disposeChildren(group) {
  while (group.children.length) { const ch = group.children.pop(); ch.geometry?.dispose?.(); ch.material?.map?.dispose?.(); ch.material?.dispose?.(); }
}
function rebuildLeadArrows(filterIds = null) {
  disposeChildren(leadGroup);
  const cPos = new THREE.Color(0xffd166), cNeg = new THREE.Color(0x7aa2ff);
  for (const e of visibleLeadEdges(filterIds)) {
    const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
    const start = new THREE.Vector3(a.x, a.y, a.z), end = new THREE.Vector3(b.x, b.y, b.z);
    const dir = end.clone().sub(start); const len = dir.length(); if (len < 1e-3) continue; dir.normalize();
    const shaftLen = Math.max(0.1, len - 3.2);
    const mid = start.clone().add(dir.clone().multiplyScalar(shaftLen / 2));
    const strength = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.xcorr || 0)) - 0.08) / 0.25));
    const w = 0.35 + 0.9 * (0.25 + 0.75 * strength);
    const col = e.sign >= 0 ? cPos : cNeg;
    const pass = edgePass(e);
    if (pass) {
      const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.1 * w, 0.1 * w, shaftLen, 6), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.25 + 0.7 * strength }));
      shaft.position.copy(mid); shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir); leadGroup.add(shaft);
    } else {
      // 未通过 FDR：静态虚线、降低亮度，不加流动光点。
      const g = new THREE.BufferGeometry().setFromPoints([start, start.clone().add(dir.clone().multiplyScalar(shaftLen))]);
      const dash = new THREE.Line(g, new THREE.LineDashedMaterial({ color: col, transparent: true, opacity: 0.42, dashSize: 1.2, gapSize: 1.4, depthWrite: false }));
      dash.computeLineDistances(); leadGroup.add(dash);
    }
    const head = new THREE.Mesh(new THREE.ConeGeometry((pass ? 0.5 : 0.36) * w, 2.2, 8), new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: pass ? 0.35 + 0.65 * strength : 0.35 }));
    head.position.copy(start.clone().add(dir.clone().multiplyScalar(shaftLen + 1.0)));
    head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir); leadGroup.add(head);
  }
}

// ---------------- 流动光点 ----------------
const flowGroup = new THREE.Group(); scene.add(flowGroup);
const flowMats = [];
function clearFlow() { disposeChildren(flowGroup); flowMats.length = 0; }
function addFlow(a, b, color, opacity) {
  const segs = 14, pos = [];
  for (let i = 0; i <= segs; i++) { const t = i / segs; pos.push(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t); }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  const mat = new THREE.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 2.2, gapSize: 1.35, depthWrite: false });
  mat.userData.base = opacity; flowMats.push(mat);
  const line = new THREE.Line(geo, mat); line.computeLineDistances(); flowGroup.add(line);
  const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.Float32BufferAttribute(new Array(21).fill(0), 3));
  const pts = new THREE.Points(pg, new THREE.PointsMaterial({ color, size: 1.7, map: discTex, alphaTest: 0.3, transparent: true, opacity: opacity * 0.9, depthWrite: false, blending: THREE.AdditiveBlending }));
  pts.material.userData.base = opacity * 0.9;
  pts.userData = { ax: a.x, ay: a.y, az: a.z, bx: b.x, by: b.y, bz: b.z, n: 7, phase: Math.random() };
  flowGroup.add(pts);
}
function rebuildFlow() {
  clearFlow();
  if (!coreIds.size) return;  // 全局/社区视图由“千丝万缕”同步网承担
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
      const color = e.soft ? 0x8fa3c4 : (e.sign >= 0 ? 0xff8a8a : 0x3ddc97);
      addFlow(a, b, color, e.soft ? 0.3 : 0.45 + 0.5 * s);
    }
  } else {
    for (const e of visibleLeadEdges(coreIds)) {
      if (!edgePass(e)) continue;
      const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
      const s = Math.min(1, Math.max(0, ((e.abs ?? Math.abs(e.xcorr || 0)) - 0.08) / 0.25));
      addFlow(a, b, e.sign >= 0 ? 0xffd166 : 0x7aa2ff, 0.4 + 0.55 * s);
    }
  }
}

// ---------------- 标签（含屏幕空间去重叠） ----------------
const labelGroup = new THREE.Group(); scene.add(labelGroup);
function makeLabel(text, color, priority) {
  const c = document.createElement('canvas'); const ctx = c.getContext('2d');
  ctx.font = '600 28px sans-serif'; const w = Math.ceil(ctx.measureText(text).width) + 28;
  c.width = w; c.height = 48; ctx.font = '600 28px sans-serif';
  ctx.fillStyle = 'rgba(8,12,22,0.74)';
  ctx.beginPath(); const r = 12; ctx.moveTo(r, 4); ctx.arcTo(w, 4, w, 44, r); ctx.arcTo(w, 44, 0, 44, r); ctx.arcTo(0, 44, 0, 4, r); ctx.arcTo(0, 4, w, 4, r); ctx.closePath(); ctx.fill();
  ctx.fillStyle = color; ctx.textBaseline = 'middle'; ctx.fillText(text, 14, 26);
  const tex = new THREE.CanvasTexture(c); tex.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  sp.scale.set(w / 10, 4.8, 1); sp.renderOrder = 5; sp.userData.priority = priority; sp.userData.w = w;
  return sp;
}
function setLabels(items) {
  disposeChildren(labelGroup);
  for (const it of items) {
    const n = nodeById[it.id]; if (!n) continue;
    const lab = makeLabel(it.text || n.name, it.color || '#e8eefc', it.priority || 0);
    lab.position.set(n.x, n.y + 4.5, n.z); lab.userData.i = idxOf[it.id]; lab.userData.pin = !!it.pin; labelGroup.add(lab);
  }
  updateLabelOpacity();
  declutterLabels();
}
// 标签随节点点亮淡入（核心/置顶标签常亮）
function updateLabelOpacity() {
  for (const sp of labelGroup.children) {
    const L = sp.userData.i == null ? 1 : lit[sp.userData.i] * sMask[sp.userData.i];
    sp.material.opacity = sp.userData.pin ? 1 : Math.max(0, Math.min(1, (L - 0.45) / 0.35));
  }
}
function topMovers() {
  const k = CFG_UI.top_movers_labels ?? 8;
  const rows = DATA.nodes.map(n => ({ id: n.id, v: retOf(n) })).filter(r => r.v != null);
  const up = rows.filter(r => r.v > 0).sort((a, b) => b.v - a.v).slice(0, Math.ceil(k / 2));
  const dn = rows.filter(r => r.v < 0).sort((a, b) => a.v - b.v).slice(0, Math.floor(k / 2));
  return [...up, ...dn];
}
function rebuildLabels() {
  const limit0 = CFG_UI.label_limit ?? 18;
  if (commFocus != null && !coreIds.size) {
    const c = commById[commFocus];
    setLabels(c.members.slice(0, limit0).map((id, k) => ({ id, color: id === c.core ? c.color : '#e8eefc', priority: id === c.core ? 1000 : 100 - k, pin: id === c.core })));
    return;
  }
  if (!coreIds.size && commFocus == null && conceptSel) { conceptLabels(limit0); return; }
  if (!coreIds.size && commFocus == null && sw1Sel) {
    const rows = (SW1_MEMBERS[sw1Sel] || []).map(id => ({ id, v: retOf(nodeById[id]) })).sort((a, b) => Math.abs(b.v ?? 0) - Math.abs(a.v ?? 0)).slice(0, limit0);
    setLabels(rows.map((r, i) => ({ id: r.id, text: `${r.id} ${fmtRet(r.v)}`, color: r.v == null ? '#e8eefc' : r.v >= 0 ? '#ff9d9d' : '#7fe7b8', priority: 80 - i })));
    return;
  }
  if (!coreIds.size && colorBy === 'sw1' && !styleFilter) {
    const wd = GRAPH.wdeg || {};
    setLabels(SW1_LIST.slice(0, 14).map((k, i) => ({ id: SW1_MEMBERS[k].slice().sort((a, b) => (wd[b] ?? 0) - (wd[a] ?? 0))[0], text: k, color: SW1_COLOR[k], priority: 60 - i })));
    return;
  }
  if (!coreIds.size && styleFilter) {
    const rows = DATA.nodes.filter(n => styleOf(n.id) === styleFilter).map(n => ({ id: n.id, v: retOf(n) }))
      .sort((a, b) => Math.abs(b.v ?? 0) - Math.abs(a.v ?? 0)).slice(0, CFG_UI.top_movers_labels ?? 8);
    setLabels(rows.map((r, i) => ({ id: r.id, text: `${r.id} ${fmtRet(r.v)}`, color: styleHex(styleFilter), priority: 60 - i })));
    return;
  }
  if (!coreIds.size && colorBy === 'community' && COMM.length) {
    setLabels(COMM.map((c, k) => ({ id: c.core, text: `${c.name}`, color: c.color, priority: 60 - k })));
    return;
  }
  if (!coreIds.size) {
    setLabels(topMovers().map((r, i) => ({ id: r.id, text: `${r.id} ${fmtRet(r.v)}`, color: r.v >= 0 ? '#ff9d9d' : '#7fe7b8', priority: 50 - i })));
    return;
  }
  const limit = CFG_UI.label_limit ?? 18;
  const neighbors = [...focusIds].filter(id => !coreIds.has(id))
    .map(id => ({ id, s: maxCorrToCores(id) })).filter(r => r.s > 0).sort((a, b) => b.s - a.s).slice(0, limit);
  setLabels([
    ...[...coreIds].map(id => ({ id, text: STYLE && STYLE.tags[id] ? `${id} · ${STYLE.tags[id].p}` : id, color: '#5ad1ff', priority: 1000, pin: true })),
    ...neighbors.map((r, i) => ({ id: r.id, color: '#e8eefc', priority: 100 - i - (r.s < 0.3 ? 20 : 0) })),
  ]);
}
const _v = new THREE.Vector3(), _e = new THREE.Vector3(), _right = new THREE.Vector3(), _up = new THREE.Vector3();
function toScreen(vec) { return [(vec.x + 1) / 2 * innerWidth, (1 - vec.y) / 2 * innerHeight]; }
function declutterLabels() {
  camera.updateMatrixWorld();
  _right.setFromMatrixColumn(camera.matrixWorld, 0); _up.setFromMatrixColumn(camera.matrixWorld, 1);
  const placed = [];
  const items = [...labelGroup.children].sort((a, b) => b.userData.priority - a.userData.priority);
  for (const sp of items) {
    if (sp.material.opacity < 0.05) { sp.visible = false; continue; }
    // 近处标签按距离缩小，避免贴近镜头时字号过大
    const k = Math.max(0.45, Math.min(1.2, camera.position.distanceTo(sp.position) / 130));
    sp.scale.set(sp.userData.w / 10 * k, 4.8 * k, 1);
    _v.copy(sp.position).project(camera);
    if (_v.z > 1 || Math.abs(_v.x) > 1.05 || Math.abs(_v.y) > 1.05) { sp.visible = false; continue; }
    const [cx, cy] = toScreen(_v);
    const [rx] = toScreen(_e.copy(sp.position).addScaledVector(_right, sp.scale.x / 2).project(camera));
    const [, uy] = toScreen(_e.copy(sp.position).addScaledVector(_up, sp.scale.y / 2).project(camera));
    const hw = Math.abs(rx - cx), hh = Math.abs(cy - uy);
    const rect = [cx - hw, cy - hh, cx + hw, cy + hh];
    const overlap = placed.some(p => !(rect[2] < p[0] || rect[0] > p[2] || rect[3] < p[1] || rect[1] > p[3]));
    if (overlap && sp.userData.priority < 1000) { sp.visible = false; continue; }
    sp.visible = true; placed.push(rect);
  }
}

// ---------------- 查询 ----------------
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
// 精确匹配：板块名相同（忽略末尾“Ⅱ”），或扩展规则里的 exact 项。
function exactMatches(q) {
  q = (q || '').trim(); if (!q) return [];
  const out = new Set();
  for (const n of DATA.nodes) if (n.name === q || n.name.replace(/Ⅱ$/, '') === q) out.add(n.id);
  const rule = (DATA.expand_rules || {})[q];
  if (!out.size && rule) for (const ex of (rule.exact || [])) if (nodeById[ex]) out.add(ex);
  return [...out];
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
  setExpandOptions([]);
  step([hit.sector]);
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
      for (const nb of (DATA.lead_out[id] || [])) if (leadVisible(nb)) one.add(nb.id);
      for (const nb of (DATA.lead_in[id] || [])) if (leadVisible(nb)) one.add(nb.id);
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

// ---------------- 视图 ----------------
function applyFocusVisual() {
  applyNodeAppearance();
  const has = focusIds.size > 0;
  syncLines.visible = true;
  softLines.visible = mode === 'sync' && !has;
  leadGroup.visible = mode === 'lead';
  if (mode === 'lead') rebuildLeadArrows(has ? coreIds : null);
  rebuildFlow();
  renderBoard();
}
// 面板遮挡时偏移投影中心：桌面左侧面板 → 向右；手机底部抽屉 → 向上，让聚焦点落在可见区域中央。
// 可见区域：扣掉浮层（手机：顶部图例 / 回放条 / 异动观察 + 底部抽屉与轨道；桌面：左侧轨道与面板、右栏、底部回放条）。
// 抽屉用 offsetTop（布局位置，不受滑入动画 transform 影响），所以抽屉刚打开时也能算准。
function visibleRect() {
  const W = innerWidth, H = innerHeight, m = 8, mob = MOBILE_MQ.matches;
  const fly = document.getElementById('flyout'), open = fly.classList.contains('open');
  const box = id => { const e = document.getElementById(id); if (!e || e.hidden) return null; const r = e.getBoundingClientRect(); return r.width && r.height ? r : null; };
  let L = 0, T = 0, R = W, B = H;
  if (mob) {
    for (const id of ['trailBar', 'legend', 'replayBar', 'sigPanel']) { const r = box(id); if (r && r.top < H * 0.4) T = Math.max(T, Math.min(r.bottom, H * 0.45)); }
    const rail = box('dockRail'); if (rail) B = Math.min(B, rail.top);
    if (open && fly.offsetHeight) B = Math.min(B, fly.offsetTop);
  } else {
    const rail = box('dockRail'); if (rail) L = Math.max(L, rail.right);
    if (open && fly.offsetWidth) L = Math.max(L, fly.offsetLeft + fly.offsetWidth);
    for (const id of ['legend', 'sigPanel']) { const r = box(id); if (r && r.left > W * 0.5) R = Math.min(R, r.left); }
    const rp = box('replayBar'); if (rp && rp.top > H * 0.5) B = Math.min(B, rp.top);
  }
  if (B - T < 120) { const c = (T + B) / 2; T = Math.max(0, c - 60); B = Math.min(H, c + 60); }
  return { L: L + m, T: T + m, R: R - m, B: B - m, W, H };
}
// 投影中心对准可见区域中心（setViewOffset），取景时按可见区域大小算距离。
function applyViewOffset() {
  const v = visibleRect(), W = v.W, H = v.H;
  const ox = W / 2 - (v.L + v.R) / 2, oy = H / 2 - (v.T + v.B) / 2;
  if (Math.abs(ox) > 0.5 || Math.abs(oy) > 0.5) camera.setViewOffset(W, H, ox, oy, W, H); else camera.clearViewOffset();
  camera.aspect = W / H; camera.updateProjectionMatrix();
}
function frameFor(ids, oneHop = true) {
  // 被高亮的点（核心 + 一跳邻居，或整组）全部放进可见区域（扣掉顶部浮层 / 底部抽屉 / 侧栏后的矩形）
  const pool = [...new Set(oneHop ? [...ids, ...egoOf(ids).one] : ids)].map(id => nodeById[id]).filter(Boolean);
  if (!pool.length) return null;
  const cx = pool.reduce((s, p) => s + p.x, 0) / pool.length;
  const cy = pool.reduce((s, p) => s + p.y, 0) / pool.length;
  const cz = pool.reduce((s, p) => s + p.z, 0) / pool.length;
  const v = visibleRect(), tpp = Math.tan(camera.fov * Math.PI / 360) / (v.H / 2);  // 每像素的视角正切
  // 横向再留 ~60px、纵向 ~12px 给居中显示的标签，避免点在区域内而标签压到侧栏
  const tX = Math.max(0.05, ((v.R - v.L) / 2 - Math.min(60, (v.R - v.L) * 0.12)) * tpp), tY = Math.max(0.05, ((v.B - v.T) / 2 - 12) * tpp);
  // 保持当前观察方向，从当前状态连续飞过去（不先复位）
  const dir = camera.position.clone().sub(controls.target);
  if (dir.lengthSq() < 1) dir.set(55, 28, 95);
  dir.normalize();
  // 在该方向下逐点收紧：把包围盒中心对准可见区域中心，每个点都满足 |x|/(d−z) ≤ tanX、|y|/(d−z) ≤ tanY
  const rt = new THREE.Vector3().crossVectors(camera.up, dir).normalize(), up = new THREE.Vector3().crossVectors(dir, rt);
  const loc = pool.map(p => { const q = new THREE.Vector3(p.x - cx, p.y - cy, p.z - cz); return [q.dot(rt), q.dot(up), q.dot(dir)]; });
  const mx = (Math.min(...loc.map(a => a[0])) + Math.max(...loc.map(a => a[0]))) / 2, my = (Math.min(...loc.map(a => a[1])) + Math.max(...loc.map(a => a[1]))) / 2;
  let need = 0;
  for (const [x, y, z] of loc) need = Math.max(need, z + (Math.abs(x - mx) + 3) / tX, z + (Math.abs(y - my) + 3) / tY);
  const dist = Math.min(900, Math.max(24, need * 1.04));
  controls.maxDistance = Math.max(380, dist * 1.05);  // 可见区域很小（手机）时允许拉远，否则 controls 会把镜头拽回 380
  const target = new THREE.Vector3(cx, cy, cz).addScaledVector(rt, mx).addScaledVector(up, my);
  return { target, pos: target.clone().addScaledVector(dir, dist) };
}
function frameCores() { const f = frameFor([...coreIds]); if (f) flyTo(f.target, f.pos); }
function resetView() {
  if (coreIds.size) { frameCores(); return; }
  if (commFocus != null && commById[commFocus]) { const f = frameFor(commById[commFocus].members, false); if (f) flyTo(f.target, f.pos); return; }
  if (sw1Sel) { const f = frameFor(SW1_MEMBERS[sw1Sel] || [], false); if (f) flyTo(f.target, f.pos); return; }
  if (conceptSel && conceptSel.secIds.length) { const f = frameFor(conceptSel.secIds, false); if (f) flyTo(f.target, f.pos); return; }
  if (styleFilter) { const f = frameFor(DATA.nodes.filter(n => styleOf(n.id) === styleFilter).map(n => n.id), false); if (f) flyTo(f.target, f.pos); return; }
  controls.maxDistance = 380;
  flyTo(HOME.target.clone(), HOME.pos.clone());
}
function refreshFocus({ origin = null } = {}) {
  let hop1 = new Set(), hop2 = new Set();
  if (coreIds.size) {
    const { one, two } = egoOf([...coreIds]);
    hop1 = new Set([...one].filter(id => !coreIds.has(id))); hop2 = two;
    focusIds = new Set([...one, ...two]);
  } else if (commFocus != null && commById[commFocus]) focusIds = new Set(commById[commFocus].members);
  else if (sw1Sel) focusIds = new Set(SW1_MEMBERS[sw1Sel] || []);
  else if (conceptSel) focusIds = new Set(conceptSel.secIds);
  else focusIds = new Set();
  syncSw1Ui();
  applyFocusVisual();
  if (coreIds.size) renderPanel([...coreIds], [...focusIds].filter(id => !coreIds.has(id)));
  else if (commFocus != null) renderCommunityPanel(commFocus);
  else if (sw1Sel) renderSw1Panel(sw1Sel);
  else if (conceptSel) renderConceptPanel();
  else renderPanel(null, []);
  rebuildLabels();
  syncAutoRotate();
  if (!coreIds.size && commFocus == null && !sw1Sel && conceptSel) conceptTransition();
  else if (!coreIds.size && commFocus == null && sw1Sel) {
    const ms = SW1_MEMBERS[sw1Sel] || [], wd = GRAPH.wdeg || {};
    const core = ms.slice().sort((a, b) => (wd[b] ?? 0) - (wd[a] ?? 0))[0] || null;
    startTransition({ origin: core, hop1: new Set(ms), strength: () => 0.9 });
  } else if (!coreIds.size && commFocus != null && commById[commFocus]) {
    const c = commById[commFocus];
    startTransition({ origin: c.core, cores: new Set([c.core]), hop1: new Set(c.members.filter(id => id !== c.core)), strength: () => 0.85 });
  } else {
    startTransition({ origin: origin || [...coreIds][0] || null, cores: coreIds, hop1, hop2,
      strength: id => { const s = maxCorrToCores(id); return s <= 0 ? 0.2 : (s - 0.2) / 0.55; } });
  }
}
function setFocus(cores, { moveCamera = true, openDetail = true, origin = null } = {}) {
  coreIds = new Set(cores); commFocus = null; sw1Sel = null; conceptSel = null;
  refreshFocus({ origin: origin || cores[0] || null });
  if (openDetail && cores.length) setDockMode('detail', { fromFocus: true });
  if (moveCamera) { if (cores.length) frameCores(); else resetView(); }
  renderExpandChips();
  renderTrail();
}
function focusCommunity(k, { openDetail = true } = {}) {
  if (!commById[k]) return;
  coreIds = new Set(); commFocus = k; sw1Sel = null; conceptSel = null; expandOptions = []; renderExpandChips();
  refreshFocus();
  if (openDetail) setDockMode('detail', { fromFocus: true });
  resetView();
  renderTrail();
}

// ---------------- 探索路径（面包屑 + 后退/前进 + 点云中的高亮折线） ----------------
let touring = false;
function entryLabel(en) { return en.comm != null ? `${commName(en.comm)}簇` : (en.cores.length === 1 ? en.cores[0] : `${en.cores[0]} 等 ${en.cores.length} 个`); }
function explainStep(prev, id) {
  const k = commOf(id), cn = commName(k), br = BRIDGES.get(id);
  const brTxt = br ? `；${id} 是「${cn}」通往「${br.links.map(commName).join('」「')}」的桥梁` : '';
  if (!prev || prev === id) {
    const c = commById[k];
    return c ? `${id} 属「${cn}」簇（${c.size} 个板块，簇核心 ${c.core}）${brTxt}` : `${id} 未归入主要社区（零散）${brTxt}`;
  }
  const r = rhoOf(prev, id), kp = commOf(prev);
  const rel = r == null ? `残差相关未进前列（|ρ|<${DATA.corr_thr ?? '-'}）` : `残差相关 ρ=${r.toFixed(2)}${EDGE_SET.has(prev + '|' + id) ? '（多年复核）' : ''}`;
  const same = (k === kp && k >= 0) ? `同属「${cn}」簇` : `跨簇：${prev} 属「${commName(kp)}」，${id} 属「${cn}」`;
  return `${prev} 与 ${id} ${rel}，${same}${brTxt}`;
}
function communityWhy(k) {
  const c = commById[k]; if (!c) return '';
  const nb = (GRAPH.bridges || []).filter(b => b.comm === k).map(b => b.id);
  return `「${c.name}」簇：${c.size} 个板块，主行业 ${c.l1_top}（${Math.round(c.l1_share * 100)}%），簇核心 ${c.core}${nb.length ? `，桥梁 ${nb.join('、')}` : ''}`;
}
function setPStep(text) { const el = document.getElementById('pStep'); if (el) el.textContent = text || ''; }
function step(cores, { comm = null, push = true, openDetail = true, why = null } = {}) {
  if (!touring) stopTour();
  const cur = trail[trailIdx];
  if (push && cur && comm == null && cur.comm == null && cur.cores.length === cores.length && cur.cores.every((c, i) => c === cores[i])) { applyEntry(cur, { openDetail }); return; }
  const prev = cur ? (cur.comm == null && cur.cores.length === 1 ? cur.cores[0] : null) : null;
  const entry = { cores: [...cores], comm, why: '' };
  entry.why = why || (comm != null ? communityWhy(comm) : (cores.length === 1 ? explainStep(prev, cores[0]) : `查询聚焦 ${cores.length} 个板块：${cores.join('、')}`));
  if (push) {
    trail = trail.slice(0, trailIdx + 1); trail.push(entry);
    if (trail.length > TRAIL_MAX) trail.shift();
    trailIdx = trail.length - 1;
  }
  applyEntry(entry, { openDetail });
}
function applyEntry(en, { openDetail = true } = {}) {
  if (en.comm != null) focusCommunity(en.comm, { openDetail }); else setFocus(en.cores, { openDetail });
  setPStep(en.why);
}
function goGlobal({ keepTrail = true } = {}) {
  if (!touring) stopTour();
  document.getElementById('q').value = ''; document.getElementById('hints').innerHTML = '';
  if (!keepTrail) trail = [];
  trailIdx = -1;
  expandOptions = []; renderExpandChips();
  coreIds = new Set(); commFocus = null; sw1Sel = null; conceptSel = null; ccToken++;
  refreshFocus();
  controls.maxDistance = 380;
  flyTo(HOME.target.clone(), HOME.pos.clone());
  setPStep('');
  renderTrail();
}
function goTrail(idx) {
  if (idx < -1 || idx >= trail.length) return;
  if (idx === -1) { goGlobal(); return; }
  trailIdx = idx; applyEntry(trail[idx]);
}
const TRAIL_CAP = TRAIL_MAX + 1;
const trailGeo = new THREE.BufferGeometry();
trailGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL_CAP * 6), 3));
trailGeo.setDrawRange(0, 0);
const trailLine = new THREE.LineSegments(trailGeo, new THREE.LineBasicMaterial({ color: 0x9ff3ff, transparent: true, opacity: 0.95, depthTest: false, depthWrite: false }));
trailLine.renderOrder = 6; trailLine.frustumCulled = false; scene.add(trailLine);
const trailDotGeo = new THREE.BufferGeometry();
trailDotGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL_CAP * 3), 3));
trailDotGeo.setDrawRange(0, 0);
const trailDots = new THREE.Points(trailDotGeo, new THREE.PointsMaterial({ color: 0x9ff3ff, size: 7, map: ringTex, transparent: true, depthTest: false, depthWrite: false }));
trailDots.renderOrder = 6; trailDots.frustumCulled = false; scene.add(trailDots);
function renderTrail() {
  const ids = trail.slice(0, trailIdx + 1).map(en => en.comm != null ? (commById[en.comm] || {}).core : en.cores[0]).filter(id => nodeById[id]);
  const P = trailGeo.attributes.position.array, D = trailDotGeo.attributes.position.array;
  let seg = 0;
  for (let k = 1; k < ids.length; k++) {
    const a = nodeById[ids[k - 1]], b = nodeById[ids[k]]; if (ids[k - 1] === ids[k]) continue;
    P.set([a.x, a.y, a.z, b.x, b.y, b.z], seg * 6); seg++;
  }
  ids.forEach((id, k) => { const n = nodeById[id]; D.set([n.x, n.y, n.z], k * 3); });
  trailGeo.setDrawRange(0, seg * 2); trailGeo.attributes.position.needsUpdate = true;
  trailDotGeo.setDrawRange(0, ids.length > 1 ? ids.length : 0); trailDotGeo.attributes.position.needsUpdate = true;
  const bar = document.getElementById('trailBar'), crumbs = document.getElementById('crumbs');
  bar.hidden = !trail.length;
  if (!trail.length) { crumbs.innerHTML = ''; return; }
  crumbs.innerHTML = `<span class="crumb ${trailIdx === -1 ? 'on' : ''}" data-i="-1">全局</span>` +
    trail.map((en, i) => `<span class="crumb-sep">›</span><span class="crumb ${i === trailIdx ? 'on' : ''}" data-i="${i}" title="${(en.why || '').replace(/"/g, '&quot;')}">${entryLabel(en)}</span>`).join('');
  crumbs.querySelectorAll('.crumb').forEach(el => el.onclick = () => goTrail(+el.dataset.i));
  const on = crumbs.querySelector('.crumb.on'); if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  document.getElementById('trBack').disabled = trailIdx < 0;
  document.getElementById('trFwd').disabled = trailIdx >= trail.length - 1;
}
document.getElementById('trBack').onclick = () => goTrail(trailIdx - 1);
document.getElementById('trFwd').onclick = () => goTrail(trailIdx + 1);
window.addEventListener('keydown', e => {
  if (!e.altKey || e.target.tagName === 'INPUT') return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); goTrail(trailIdx - 1); }
  if (e.key === 'ArrowRight') { e.preventDefault(); goTrail(trailIdx + 1); }
});

// ---------------- 面板 ----------------
{
  const pStep = document.createElement('div'); pStep.id = 'pStep'; pStep.className = 'pstep';
  document.getElementById('pMeta').before(pStep);
  const pSug = document.createElement('div'); pSug.id = 'pSuggest';
  document.getElementById('pKv').after(pSug);
}
function cdot(k) { const c = commById[k]; return `<span class="cdot" style="background:${c ? c.color : (GRAPH.loose_color || '#6b778c')}"></span>`; }
function commListHtml() {
  if (!COMM.length) return '';
  return `<div class="sec"><span>同步图社区 ${COMM.length} 个 · 模块度 Q=${GRAPH.modularity ?? '-'}</span><span>点击进入</span></div><ul class="list">${COMM.map(c =>
    `<li data-comm="${c.id}"><span>${cdot(c.id)}${c.name}<span class="sub2">${c.size} 个板块 · 核心 ${c.core} · 主行业 ${c.l1_top}</span></span><span data-csum="${c.id}"></span></li>`).join('')}</ul>`;
}
// 下一步建议：簇核心 / 桥梁 / 最强未探索邻居
function renderSuggest(id) {
  const box = document.getElementById('pSuggest'); if (!box) return;
  if (!id) { box.innerHTML = ''; return; }
  const k = commOf(id), c = commById[k];
  const visited = new Set(trail.slice(0, trailIdx + 1).flatMap(en => en.cores || []));
  const items = [], seen = new Set([id]);
  const push = it => { if (!it.id || seen.has(it.id) || !nodeById[it.id]) return; seen.add(it.id); items.push(it); };
  if (c && c.core !== id) push({ id: c.core, tag: 'core', tagText: '核心', why: `「${c.name}」簇内加权度最高` });
  const br = BRIDGES.get(id);
  if (br) {
    for (const lk of br.links.slice(0, 2)) {
      let best = null, bv = -1;
      for (const [x, r] of (ADJ[id] || new Map())) if (commOf(x) === lk && Math.abs(r) > bv) { bv = Math.abs(r); best = x; }
      if (best) push({ id: best, tag: 'bridge', tagText: '跨簇', why: `经桥梁 ${id} 通往「${commName(lk)}」 · ρ=${rhoOf(id, best).toFixed(2)}` });
    }
  } else {
    for (const b of (GRAPH.bridges || []).filter(b => b.comm === k).slice(0, 2))
      push({ id: b.id, tag: 'bridge', tagText: '桥梁', why: `本簇通往「${b.links.map(commName).join('」「')}」` });
  }
  const nbs = [...(ADJ[id] || new Map()).entries()].filter(([x]) => !visited.has(x) && x !== id)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, CFG_UI.suggest_neighbors ?? 3);
  for (const [x, r] of nbs) push({ id: x, tag: 'nb', tagText: '邻居', why: `ρ=${r.toFixed(2)}${commOf(x) !== k ? ` · 跨簇「${commName(commOf(x))}」` : ' · 同簇'}` });
  box.innerHTML = items.length ? `<div class="sec"><span>下一步建议</span><span>逐步参透</span></div><ul class="list sugg">${items.map(it =>
    `<li data-id="${it.id}"><span><span class="stag ${it.tag}">${it.tagText}</span>${it.id}<span class="sub2">${it.why}</span></span><span>›</span></li>`).join('')}</ul>` : '';
  box.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); step([li.dataset.id]); });
}
function renderCommunityPanel(k) {
  const c = commById[k]; if (!c) return;
  document.getElementById('pTitle').textContent = `${c.name}簇`;
  document.getElementById('pMeta').textContent = `${c.size} 个板块 · 簇核心 ${c.core} · ${GRAPH.method || ''}`;
  document.getElementById('pKv').innerHTML = `<b>主行业</b><span>${c.l1_top}（${Math.round(c.l1_share * 100)}%）</span><b>模块度</b><span>Q=${GRAPH.modularity ?? '-'}（全图）</span><b>区间涨跌</b><span data-csum="${k}"></span>`;
  const bs = (GRAPH.bridges || []).filter(b => b.comm === k);
  const wd = GRAPH.wdeg || {};
  document.getElementById('pBody').innerHTML =
    (bs.length ? `<div class="sec"><span>桥梁（通往其他社区）</span></div><ul class="list">${bs.map(b => `<li data-id="${b.id}"><span><span class="stag bridge">桥梁</span>${b.id}<span class="sub2">→ ${b.links.map(commName).join('、')} · 介数 ${b.betweenness}</span></span><span>›</span></li>`).join('')}</ul>` : '') +
    `<div class="sec"><span>成员（按簇内加权度）</span><span>${c.size}</span></div><ul class="list">${c.members.map(id => `<li data-id="${id}"><span>${id === c.core ? '<span class="stag core">核心</span>' : ''}${id}</span><span class="sub2">Σ|ρ| ${(wd[id] ?? 0).toFixed(2)}</span></li>`).join('')}</ul>`;
  document.getElementById('pBody').querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); step([li.dataset.id]); });
  renderMembers(null); renderSuggest(null); updateRetDom();
}
function fmtPct(x) { if (x == null || Number.isNaN(x)) return '-'; return (x * 100).toFixed(0) + '%'; }
function fmtLift(x) { if (x == null || Number.isNaN(x)) return '-'; return (x >= 0 ? '+' : '') + (x * 100).toFixed(0) + 'pt'; }
function fmtPct1(x) { if (x == null || Number.isNaN(x)) return '-'; return (x * 100).toFixed(1) + '%'; }
function fmtLift1(x) { if (x == null || Number.isNaN(x)) return '-'; return (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + 'pt'; }
const OOS_MIN_EVENTS = ((DATA.lead_summary || {}).oos_cfg || {}).min_events ?? 6;
function fmtQ(q) { if (q == null || Number.isNaN(+q)) return '-'; return q < 0.001 ? '<0.001' : (+q).toFixed(3); }
function fmtRet(x) { if (x == null || Number.isNaN(+x)) return '-'; const v = +x; return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
function fromTag(from) { return from && coreIds.size > 1 ? `<span class="from">来自 ${from}</span>` : ''; }
function leadRow(r, arrow) {
  const pass = edgePass(r);
  const badge = r.q_value == null ? '' : (pass ? `<span class="sig ok">FDR✓ q=${fmtQ(r.q_value)}</span>` : `<span class="sig no">未通过 q=${fmtQ(r.q_value)}</span>`);
  const dir = r.xcorr >= 0 ? '同向' : '反向';
  const ins = r.hit != null
    ? `${dir}命中 ${fmtPct(r.hit)}（基准 ${fmtPct(r.base)}，${fmtLift(r.lift)}）`
    : `跟涨 ${fmtPct(r.p_up)}（${fmtLift(r.lift_up)}）· 跟跌 ${fmtPct(r.p_dn)}（${fmtLift(r.lift_dn)}）`;
  let oos = '';
  if (r.oos_n >= OOS_MIN_EVENTS) oos = `样本外 ${r.oos_n} 次/${r.oos_folds} 段 · 命中 ${fmtPct(r.oos_hit)}（基准 ${fmtPct(r.oos_base)}，${fmtLift(r.oos_lift)}）`;
  else if (r.oos_n > 0) oos = `样本外仅 ${r.oos_n} 次事件（<${OOS_MIN_EVENTS}），样本不足`;
  else if (r.oos_n === 0) oos = '样本外：历次训练段均未入选，无验证记录';
  return `<li data-id="${r.id}" class="${pass ? '' : 'failed'}"><span>${r.id}${styleTag(r.id)}${badge}${fromTag(r._from)}<span class="sub2">滞后 ${r.lag} 周 · xcorr ${r.xcorr.toFixed(2)} · ${ins}</span>${oos ? `<span class="sub2">${oos}</span>` : ''}</span><span class="leadc">${arrow}</span></li>`;
}
function leadSummaryText() {
  const s = DATA.lead_summary; if (!s) return '';
  const o = (s.oos || {}).all || {}, f = (s.oos || {}).fdr || {};
  const oos = o.n_events ? `滚动样本外 ${o.n_events} 次事件：命中 ${fmtPct1(o.hit)} vs 基准 ${fmtPct1(o.base)}（${fmtLift1(o.lift)}）` : '样本外暂无事件';
  const fo = f.n_events ? `；其中训练段 FDR 通过的边 ${f.n_events} 次：${fmtPct1(f.hit)}（${fmtLift1(f.lift)}）` : '；训练段内无 FDR 通过的边';
  return `领先候选 ${s.candidates} 条，BH-FDR（α=${s.fdr_alpha}，${s.m_tests} 个有序板块对）通过 ${s.fdr_pass} 条。${oos}${fo}。`;
}
function leadNoticeText() {
  if (LEAD_SIG_COUNT == null) return '';
  return LEAD_SIG_COUNT === 0
    ? '实验功能：当前没有任何领先边通过 BH-FDR，样本外命中与基准持平。箭头仅供提出假设，请以「同步相关」为主。'
    : `实验功能：仅 ${LEAD_SIG_COUNT} 条领先边通过 BH-FDR，请结合样本外命中阅读。`;
}
function leadNotice() {
  if (LEAD_SIG_COUNT == null) return '';
  return `<div class="exp-tag">实验 · ${LEAD_SIG_COUNT} 条通过 FDR${infoBtn('lead', '领先传导说明')}</div>`;
}
function collectFrom(lists) {
  // lists: [[coreId, rows]] → 去重并记录来源核心（取强度最高的来源）
  const best = new Map();
  for (const [core, rows] of lists) for (const r of rows) {
    if (coreIds.has(r.id)) continue;
    const cur = best.get(r.id);
    if (!cur || (r.abs ?? 0) > (cur.abs ?? 0)) best.set(r.id, { ...r, _from: core });
  }
  return [...best.values()];
}
function renderPanel(cores, neighborIds) {
  const title = document.getElementById('pTitle');
  const meta = document.getElementById('pMeta');
  const kv = document.getElementById('pKv');
  const body = document.getElementById('pBody');
  if (!meta || !kv || !body) return;
  syncStyleBadge(cores && cores.length === 1 ? cores[0] : null);
  if (!cores || !cores.length) {
    if (title) title.textContent = styleFilter && mode !== 'lead' ? `风格 · ${styleFilter}` : '全局视图';
    if (mode === 'lead') {
      meta.textContent = `${DATA.n_sectors} 板块 · ${(DATA.lead_edges || []).length} 候选领先边`;
      body.innerHTML = `${leadNotice()}<div class="empty">请先查询或点击一个板块</div>`;
    } else {
      const soft = DATA.edges.filter(e => e.soft).length;
      meta.textContent = `${DATA.n_sectors} 板块 · ${DATA.edges.length - soft} 同步边${soft ? ` · ${soft} 补充` : ''}`;
      if (styleFilter) {
        const rows = DATA.nodes.filter(n => styleOf(n.id) === styleFilter).map(n => ({ id: n.id, v: retOf(n) })).sort((a, b) => (b.v ?? -1e9) - (a.v ?? -1e9));
        meta.textContent = `${styleFilter} ${rows.length} 板块 · ${periodLabel()}均值 ${styleSum[styleFilter] ? fmtRet(styleSum[styleFilter].avg) : '—'}`;
        body.innerHTML = `<ul class="list">${rows.map(r => `<li data-id="${r.id}"><span>${r.id}${STYLE.tags[r.id].s ? `<span class="sub2">副 ${STYLE.tags[r.id].s}</span>` : ''}</span><span class="${(r.v ?? 0) >= 0 ? 'pos' : 'neg'}">${fmtRet(r.v)}</span></li>`).join('')}</ul>`;
        body.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); step([li.dataset.id]); });
        kv.innerHTML = ''; renderMembers(null); renderSuggest(null); return;
      }
      body.innerHTML = commListHtml();
      body.querySelectorAll('li[data-comm]').forEach(li => li.onclick = () => step([], { comm: +li.dataset.comm }));
      updateRetDom();
    }
    kv.innerHTML = ''; renderMembers(null); renderSuggest(null); return;
  }
  if (title) title.textContent = cores.length === 1 ? cores[0] : `聚焦簇（${cores.length}）`;
  if (cores.length === 1) {
    const n = nodeById[cores[0]];
    const k = commOf(n.id);
    kv.innerHTML = `<b>社区</b><span class="commlink" data-comm="${k}">${cdot(k)}${commName(k)}${BRIDGES.has(n.id) ? ' · 桥梁' : ''}</span><b>一级</b><span>${n.l1 || '-'}</span><b>成分</b><span>${n.n || '-'} 只</span><b>日期</b><span data-retdate>${R_DATES[dayIdx] || '-'}</span>${PERIODS.map(P => `<b>${periodLabel(P)}</b><span data-ret="${P}" data-id="${n.id}"></span>`).join('')}`;
    updateRetDom();
    renderMembers(cores[0]);
    const cl = kv.querySelector('.commlink'); if (cl && k >= 0) cl.onclick = () => step([], { comm: k });
  } else {
    const list = cores.join(' · ');
    kv.innerHTML = `<b>核心</b><span class="corelist" title="${list}">${list}</span>`;
    renderMembers(null);
  }
  if (mode === 'lead') {
    const visible = visibleLeadEdges(coreIds).length;
    meta.textContent = `领先·实验 · 核心 ${cores.length} · 显示最强 ${visible} 条连接 · 关联 ${neighborIds.length}`;
    let hidden = 0;
    const filt = rows => rows.filter(r => { if (leadVisible(r)) return true; hidden++; return false; });
    const outs = collectFrom(cores.map(c => [c, filt(DATA.lead_out[c] || [])]));
    const inns = collectFrom(cores.map(c => [c, filt(DATA.lead_in[c] || [])]));
    const order = (a, b) => (edgePass(b) - edgePass(a)) || leadStrength(b) - leadStrength(a);
    outs.sort(order); inns.sort(order);
    const rowOut = outs.slice(0, LEAD_PANEL_LIMIT).map(r => leadRow(r, '→')).join('');
    const rowIn = inns.slice(0, LEAD_PANEL_LIMIT).map(r => leadRow(r, '←')).join('');
    const emptyMsg = hidden && !showFailed ? `暂无通过 FDR 的领先关系` : '暂无明显领先关系';
    const hiddenNote = hidden && !showFailed ? `<div class="short-note">另有 ${hidden} 条未通过 FDR，已隐藏${infoBtn('lead', '领先传导说明')}</div>` : '';
    body.innerHTML = `${leadNotice()}<div class="sec">它领先谁（箭头指出）</div>${outs.length ? `<ul class="list">${rowOut}</ul>` : `<div class="empty">${emptyMsg}</div>`}<div class="sec">谁领先它（箭头指入）</div>${inns.length ? `<ul class="list">${rowIn}</ul>` : `<div class="empty">${emptyMsg}</div>`}${hiddenNote}`;
  } else {
    meta.textContent = `同步相关 · 核心 ${cores.length} · 一跳邻居 ${neighborIds.length}`;
    const verified = new Set(DATA.edges.filter(e => !e.soft).flatMap(e => [`${e.source}|${e.target}`, `${e.target}|${e.source}`]));
    const rows = collectFrom(cores.map(c => [c, DATA.neighbors[c] || []]));
    rows.sort((a, b) => b.abs - a.abs);
    body.innerHTML = rows.length
      ? `<ul class="list">${rows.slice(0, 24).map(r => {
          const ok = verified.has(`${r._from}|${r.id}`);
          return `<li data-id="${r.id}"><span>${r.id}${styleTag(r.id)}${fromTag(r._from)}${ok ? '<span class="tag-ok" title="多年方向复核通过">复核</span>' : ''}</span><span class="${r.corr >= 0 ? 'pos' : 'neg'}">ρ ${r.corr.toFixed(2)}</span></li>`;
        }).join('')}</ul>`
      : `<div class="empty">该簇暂无足够强的稳健相关边，仍可看空间邻近。</div>`;
  }
  body.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); step([li.dataset.id]); });
  renderSuggest(cores.length === 1 ? cores[0] : null);
}
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
function renderBoard() {
  const ul = document.getElementById('boardList');
  document.getElementById('boardLabel').textContent = periodLabel() + (dayIdx < R_DAYS - 1 ? ` @${(R_DATES[dayIdx] || '').slice(5)}` : '');
  const rows = DATA.nodes.map(n => ({ id: n.id, v: retOf(n) })).filter(r => r.v != null);
  rows.sort((a, b) => b.v - a.v);
  ul.innerHTML = rows.map(r => {
    const cls = r.v >= 0 ? 'pos' : 'neg';
    const on = coreIds.has(r.id) ? 'on' : '';
    return `<li class="${on}${styleFilter && styleOf(r.id) !== styleFilter ? ' sdim' : ''}" data-id="${r.id}"><span>${r.id}${styleTag(r.id)}</span><span class="${cls}">${fmtRet(r.v)}</span></li>`;
  }).join('');
  ul.querySelectorAll('li').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); step([li.dataset.id]); });
}
function renderLegend() {
  const isComm = colorBy === 'community';
  document.getElementById('lgRet').style.display = isComm ? 'none' : '';
  document.getElementById('lgCommBox').style.display = COMM.length ? '' : 'none';
  document.getElementById('lgSoft').style.display = DATA.edges.some(e => e.soft) ? '' : 'none';
  document.getElementById('lgSize').textContent = isComm ? '点大小 = 连接强度 Σ|ρ|（越大越居中）' : '点大小 = |涨跌|（「按涨跌」模式）';
  document.getElementById('lgWeb').textContent = TINT_ON
    ? '千丝万缕：两端同涨发红、同跌发绿，方向相反变暗；亮度 ∝ |ρ| × min(|涨跌|)'
    : (isComm ? '千丝万缕 = 全部同步边：两端社区色渐变，越亮 |ρ| 越大' : '千丝万缕 = 全部同步边，越亮 |ρ| 越大');
  renderCommLegend();
  renderLegendScale();
}
function groupRow(key, color, name, v, title, cls = '') {
  const up = v ? Math.round(v.up * 100) : null;
  return `<div class="lc ${cls}" ${key} title="${title}；${periodLabel()}均值 ${v ? fmtRet(v.avg) : '—'}，${up == null ? '—' : up + '%'} 上涨"><span class="cdot" style="background:${color}"></span><span class="lc-name">${name}</span><span class="lc-bar" aria-hidden="true"><i style="width:${up ?? 0}%"></i></span><span class="lc-up">${up == null ? '—' : up + '%'}</span><span class="lc-avg ${v ? (v.avg >= 0 ? 'pos' : 'neg') : ''}">${v ? fmtRet(v.avg) : '—'}</span></div>`;
}
function renderCommLegend() {
  const box = document.getElementById('lgComm'); if (!box) return;
  const isStyle = colorBy === 'style' && STYLE, isSw1 = colorBy === 'sw1';
  document.getElementById('lgTitle').textContent = isStyle ? '风格' : isSw1 ? '申万一级' : '社区';
  document.getElementById('lgColName').textContent = isStyle ? '风格（板块数）' : isSw1 ? '一级（二级数）' : '社区';
  box.classList.toggle('lg-scroll', isSw1);
  if (isSw1) {
    box.innerHTML = SW1_LIST.map(k => groupRow(`data-sw1="${k}" role="button" tabindex="0" aria-pressed="${sw1Sel === k}"`, SW1_COLOR[k], `${k}<small class="lc-n"> ${SW1_MEMBERS[k].length}</small>`, sw1Sum[k],
      `${k}：${SW1_MEMBERS[k].length} 个二级（点击只看该行业，再点取消）`, `sf ${sw1Sel === k ? 'on' : (sw1Sel ? 'off' : '')}`)).join('');
    box.querySelectorAll('[data-sw1]').forEach(el => {
      const go = () => setSw1(sw1Sel === el.dataset.sw1 ? null : el.dataset.sw1);
      el.onclick = go; el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
    });
    return;
  }
  if (isStyle) {
    box.innerHTML = STYLES.map(k => groupRow(`data-style="${k}" role="button" tabindex="0" aria-pressed="${styleFilter === k}"`, styleHex(k), `${k}<small class="lc-n"> ${STYLE.counts[k] ?? 0}</small>`, styleSum[k],
      `${k}：${STYLE.counts[k] ?? 0} 个板块（点击只看该风格，再点取消）`, `sf ${styleFilter === k ? 'on' : (styleFilter ? 'off' : '')}`)).join('');
    box.querySelectorAll('[data-style]').forEach(el => {
      const go = () => setStyleFilter(styleFilter === el.dataset.style ? null : el.dataset.style);
      el.onclick = go; el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
    });
    return;
  }
  box.innerHTML = COMM.map(c => {
    const v = commSum[c.id], up = v ? Math.round(v.up * 100) : null;
    return `<div class="lc" data-comm="${c.id}" title="${c.name}：${c.size} 个板块，核心 ${c.core}；${periodLabel()}均值 ${v ? fmtRet(v.avg) : '—'}，${up == null ? '—' : up + '%'} 上涨">${cdot(c.id)}<span class="lc-name">${c.name}</span><span class="lc-bar" aria-hidden="true"><i style="width:${up ?? 0}%"></i></span><span class="lc-up">${up == null ? '—' : up + '%'}</span><span class="lc-avg ${v ? (v.avg >= 0 ? 'pos' : 'neg') : ''}">${v ? fmtRet(v.avg) : '—'}</span></div>`;
  }).join('');
  box.querySelectorAll('[data-comm]').forEach(el => el.onclick = () => step([], { comm: +el.dataset.comm }));
}
// 风险偏好：进攻均值 − 防御均值（所选区间、所选日期）；折线 = 回放窗口内逐日（1 日）价差的累计，只画到当前日期
const RA_DAILY = (() => {
  if (!STYLE || !RET[1]) return [];
  const out = [];
  for (let d = 0; d < R_DAYS; d++) {
    const s = { 进攻: [0, 0], 防御: [0, 0] };
    for (let i = 0; i < N; i++) { const k = styleOf(DATA.nodes[i].id); const v = RET[1][d][i]; if ((k === '进攻' || k === '防御') && !Number.isNaN(v)) { s[k][0] += v; s[k][1]++; } }
    out.push(s.进攻[1] && s.防御[1] ? s.进攻[0] / s.进攻[1] - s.防御[0] / s.防御[1] : 0);
  }
  return out;
})();
let raNow = null;
function updateRiskAppetite() {
  const box = document.getElementById('rpRA'); if (!box) return;
  if (!STYLE || CFG_UI.risk_appetite === false) { box.hidden = true; return; }
  box.hidden = false;
  const a = styleSum['进攻'], b = styleSum['防御'];
  raNow = a && b ? a.avg - b.avg : null;
  const el = document.getElementById('raVal');
  el.textContent = raNow == null ? '—' : fmtRet(raNow); el.className = raNow == null ? '' : (raNow >= 0 ? 'pos' : 'neg');
  const cum = []; let c = 0; for (let d = 0; d <= dayIdx && d < RA_DAILY.length; d++) { c += RA_DAILY[d]; cum.push(c); }
  const all = []; let c2 = 0; for (const v of RA_DAILY) { c2 += v; all.push(c2); }
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all), span = Math.max(0.5, hi - lo), W = 64, H = 18, n = Math.max(2, RA_DAILY.length);
  const X = d => 2 + (W - 4) * d / (n - 1), Y = v => H - 2 - (H - 4) * (v - lo) / span;
  const pts = cum.map((v, d) => `${X(d).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const last = cum.length ? cum[cum.length - 1] : 0, colr = last >= 0 ? 'var(--up,#ff4d4f)' : 'var(--dn,#22d38a)';
  document.getElementById('raSpark').innerHTML = `<line x1="1" x2="${W - 1}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}" stroke="rgba(139,155,184,.45)" stroke-dasharray="2 2" stroke-width="1"/>` +
    (cum.length > 1 ? `<polyline points="${pts}" fill="none" stroke="${colr}" stroke-width="1.5" stroke-linejoin="round"/>` : '') +
    (cum.length ? `<circle cx="${X(cum.length - 1).toFixed(1)}" cy="${Y(last).toFixed(1)}" r="2" fill="${colr}"/>` : '');
  document.getElementById('rpRA').title = `风险偏好（${periodLabel()}，${R_DATES[dayIdx] || ''}）= 进攻 ${a ? fmtRet(a.avg) : '—'} − 防御 ${b ? fmtRet(b.avg) : '—'}；窗口内累计 ${fmtRet(last)}（只到当前日期）。为正 = 偏进攻`;
}
function renderLegendScale() {
  const sub = document.getElementById('lgSub'); if (sub) sub.textContent = `${periodLabel()} · ${(R_DATES[dayIdx] || '').slice(5)}${styleFilter ? ` · 仅${styleFilter}` : ''}${sw1Sel ? ` · ${sw1Sel}` : ''}${conceptSel ? ` · ${conceptSel.name}` : ''}`;
  const el = document.getElementById('lgScale'); if (!el) return;
  el.textContent = `光晕 = ${periodLabel()}涨跌（${R_DATES[dayIdx] || ''}）：红涨 / 绿跌，大小与亮度 ∝ |涨跌|，±${CLIP[curP()].toFixed(2)}% 封顶（回放窗口 ${Math.round(CLIP_Q * 100)}% 分位）；呼吸 = 强势异动`;
}

// ---------------- 扩展选项（查询时只聚焦精确匹配，其余可选加入） ----------------
function setExpandOptions(ids) { expandOptions = ids; renderExpandChips(); }
function renderExpandChips() {
  const box = document.getElementById('expandBox');
  if (!expandOptions.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
  box.style.display = 'flex';
  box.innerHTML = `<span class="exp-hd">相关扩展</span>` + expandOptions.map(id => `<button type="button" class="chip ${coreIds.has(id) ? 'active' : ''}" data-id="${id}">${coreIds.has(id) ? '✓ ' : '+ '}${id}</button>`).join('');
  box.querySelectorAll('button[data-id]').forEach(b => b.onclick = () => {
    const next = new Set(coreIds);
    if (next.has(b.dataset.id)) next.delete(b.dataset.id); else next.add(b.dataset.id);
    const cur = trail[trailIdx];
    if (cur && cur.comm == null) { cur.cores = [...next]; cur.why = next.size === 1 ? explainStep(null, [...next][0]) : `查询聚焦 ${next.size} 个板块：${[...next].join('、')}`; setPStep(cur.why); }
    setFocus([...next], { openDetail: false, origin: b.dataset.id });
  });
}

function setMode(m) {
  mode = m;
  [...document.getElementById('edgemode').querySelectorAll('button')].forEach(b => b.classList.toggle('active', b.dataset.v === m));
  document.getElementById('showFailed').style.display = m === 'lead' ? '' : 'none';
  document.getElementById('leadSum').style.display = m === 'lead' ? '' : 'none';
  refreshFocus();
}

function updateHints() {
  const box = document.getElementById('hints');
  const q = document.getElementById('q').value.trim();
  if (!q) { box.innerHTML = ''; return; }
  if (qMode !== 'stock') { renderConceptHints(q); return; }
  const hits = searchStocks(q);
  if (!hits.length) { box.innerHTML = `<div class="empty">未匹配到标的</div>`; return; }
  box.innerHTML = hits.map(h => `<div data-sector="${h.sector}"><b>${h.name}</b>${h.code} → ${h.sector}</div>`).join('');
  box.querySelectorAll('div[data-sector]').forEach(el => {
    el.onclick = () => {
      document.getElementById('q').value = el.dataset.sector;
      setExpandOptions([]);
      step([el.dataset.sector]);
      [...box.children].forEach(x => x.classList.remove('on')); el.classList.add('on');
    };
  });
}
function runQuery() {
  const q = document.getElementById('q').value;
  if (qMode === 'stock') {
    const hits = searchStocks(q); updateHints();
    if (!hits.length) { renderPanel(null, []); document.getElementById('pBody').innerHTML = `<div class="empty">未匹配到标的，试试名称或代码。</div>`; return; }
    setExpandOptions([]); step([hits[0].sector]); document.getElementById('q').value = hits[0].sector; return;
  }
  if (qMode === 'concept') {  // 题材模式：只搜题材；没有包含级别的匹配时给相近推荐
    const qt0 = String(q || '').trim(), cm = conceptMatches(qt0);
    if (cm.length && cm[0].score >= 50) selectConcept(cm[0], { alts: cm }); else conceptSuggestPanel(qt0, cm);
    return;
  }
  const sk = styleQuery(q);
  if (sk) {
    if (coreIds.size || commFocus != null) goGlobal();
    document.getElementById('q').value = sk;
    if (colorBy !== 'style') window.__setColorBy('style');
    setDockMode('detail', { fromFocus: true });
    setStyleFilter(sk);
    return;
  }
  const qt = String(q || '').trim();
  if (qt && qt !== '未分类' && SW1_MEMBERS[qt] && !exactMatches(qt).length) {  // 申万一级全名：二级精确匹配优先
    document.getElementById('q').value = qt; setSw1(qt); return;
  }
  if (!exactMatches(qt).length && !(DATA.expand_rules || {})[qt] && conceptQuery(qt, 'exact')) return;  // 题材同名
  const all = expandQuery(q);
  if (!all.length && conceptQuery(qt, 'fuzzy')) return;  // 二级模糊也落空 → 题材部分匹配 / 相近推荐
  if (!all.length) { setExpandOptions([]); renderPanel(null, []); document.getElementById('pBody').innerHTML = `<div class="empty">未匹配到二级板块，试试「电力」「半导体」「白酒」。</div>`; return; }
  const exact = exactMatches(q);
  const cores = exact.length ? exact : all;  // 无精确匹配（如“新能源”）时聚焦整组，仍可逐个取消
  expandOptions = exact.length ? all.filter(id => !exact.includes(id)) : all;
  step(cores);
}

document.getElementById('go').onclick = runQuery;
document.getElementById('reset').onclick = () => { if (styleFilter) setStyleFilter(null); goGlobal(); };
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') runQuery(); });
document.getElementById('q').addEventListener('input', () => updateHints());
document.getElementById('edgemode').onclick = e => { const b = e.target.closest('button'); if (b) setMode(b.dataset.v); };
document.getElementById('qmode').onclick = e => {
  const b = e.target.closest('button'); if (!b) return;
  qMode = b.dataset.v;
  [...document.getElementById('qmode').children].forEach(x => x.classList.toggle('active', x === b));
  document.getElementById('q').placeholder = qMode === 'stock' ? '输入股票名称或代码…' : qMode === 'concept' ? '查询题材 / 概念，如：培育钻石、液冷服务器…' : '查询板块，如：电力、半导体、白酒…';
  updateHints();
};
// ---------------- 区间（1/5/20 日）与逐日回放 ----------------
function syncPeriodButtons() {
  for (const id of ['retmode', 'rpPeriod']) [...document.getElementById(id).children].forEach(x => {
    x.classList.toggle('active', x.dataset.v === retWindow);
    x.style.display = PERIODS.includes(+x.dataset.v) ? '' : 'none';
  });
}
function afterRetChange() {
  renderBoard(); renderLegendScale(); updateRetDom(); renderSignals();
  if (!coreIds.size && commFocus == null && (colorBy !== 'community' || sw1Sel)) rebuildLabels();
  if (!coreIds.size && commFocus == null && sw1Sel) renderSw1Panel(sw1Sel);
  const rd = document.getElementById('rpDate'); if (rd) rd.textContent = (R_DATES[dayIdx] || '').slice(5);
}
function setPeriod(P) {
  if (!PERIODS.includes(+P)) return;
  retWindow = String(P); syncPeriodButtons();
  setRetTargets(); afterRetChange();
}
// ---------------- 异动观察（生成器按日计算，只用当日及之前数据） ----------------
const SIGS = (DATA.signals && DATA.signals.dates && DATA.signals.dates.length) ? DATA.signals : null;
const SIG_BY_DATE = new Map(SIGS ? SIGS.dates.map((d, k) => [d, SIGS.days[k] || []]) : []);
const SIG_LABEL = (SIGS && SIGS.labels) || {};
let sigPrevKeys = new Set(), sigShownDate = null;
function escHtml(x) { return String(x).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function sigKey(o) { return `${o.t}:${o.id}:${(o.p || []).join('|')}`; }
function renderSignals(force) {
  const panel = document.getElementById('sigPanel'); if (!panel) return;
  if (!SIGS) { panel.style.display = 'none'; return; }
  const date = R_DATES[dayIdx] || '';
  if (!force && date === sigShownDate) return;
  const items = SIG_BY_DATE.get(date) || [];
  const fresh = sigShownDate != null;
  document.getElementById('sgDate').textContent = date.slice(5);
  document.getElementById('sgPeek').textContent = items.length ? `${items[0].id} · ${SIG_LABEL[items[0].t] || ''} 等 ${items.length} 条` : '无';
  const ul = document.getElementById('sgList');
  ul.innerHTML = items.length ? items.map((o, k) => {
    const dir = o.d > 0 ? 'pos' : o.d < 0 ? 'neg' : '';
    const cls = [o.weak ? 'weak' : '', fresh && !sigPrevKeys.has(sigKey(o)) ? 'new' : ''].join(' ').trim();
    const nd = o.n > 1 ? `<span class="sg-n">第${o.n}天</span>` : '';
    return `<li class="${cls}" data-k="${k}" title="${escHtml(o.id + ' ' + o.x)}${o.weak ? '（放宽阈值补足）' : ''}"><span class="sg-tag t-${o.t}">${escHtml(SIG_LABEL[o.t] || o.t)}</span><span class="sg-body"><b class="${dir}">${escHtml(o.id)}</b>${o.t === 'style' || o.t === 'disperse' ? '' : styleTag(o.id)}${nd}<span class="sg-x">${escHtml(o.x)}</span></span></li>`;
  }).join('') : '<li class="empty">当日没有达到阈值的异动</li>';
  ul.querySelectorAll('li[data-k]').forEach(li => li.onclick = () => openSignal(items[+li.dataset.k]));
  sigPrevKeys = new Set(items.map(sigKey)); sigShownDate = date;
}
function openSignal(o) {
  if (!o) return;
  const why = `异动观察 · ${SIG_LABEL[o.t] || ''}（${(R_DATES[dayIdx] || '').slice(5)}）：${o.id} ${o.x}`;
  if (o.t === 'disperse' && o.c != null) { step([], { comm: o.c, why }); return; }
  if (o.t === 'style') { if (window.__setColorBy) window.__setColorBy('style'); setPStep && setPStep(why); return; }
  const cores = [o.id, ...(o.t === 'decouple' ? (o.p || []) : [])].filter(id => idxOf[id] != null);
  if (cores.length) step(cores, { why });
}
document.documentElement.style.setProperty('--up', CFG_UI.halo_up_color || '#ff4d4f');
document.documentElement.style.setProperty('--dn', CFG_UI.halo_down_color || '#22d38a');
if (MOBILE_MQ.matches && !CFG_UI.signals_open_mobile) document.getElementById('sigPanel').open = false;
if (!MOBILE_MQ.matches && CFG_UI.signals_open === false) document.getElementById('sigPanel').open = false;
renderSignals(true);
let playing = false, playTimer = 0;
const STEP_MS = CFG_UI.replay_step_ms ?? 800;
function setDay(d, ms) {
  dayIdx = Math.max(0, Math.min(R_DAYS - 1, d));
  document.getElementById('rpSlider').value = String(dayIdx);
  setRetTargets(ms ?? Math.min(STEP_MS * 0.85, CFG_UI.replay_tween_ms ?? 650)); afterRetChange();
}
function playStep() {
  if (!playing) return;
  if (dayIdx >= R_DAYS - 1) { if (CFG_UI.replay_loop) setDay(0); else { pauseReplay(); return; } }
  else setDay(dayIdx + 1);
  playTimer = setTimeout(playStep, STEP_MS);
}
function playReplay() {
  if (R_DAYS < 2) return;
  playing = true; document.getElementById('rpPlay').textContent = '❚❚';
  document.getElementById('rpPlay').setAttribute('aria-label', '暂停');
  if (dayIdx >= R_DAYS - 1) setDay(0, 350);
  clearTimeout(playTimer); playTimer = setTimeout(playStep, STEP_MS);
}
function pauseReplay() {
  playing = false; clearTimeout(playTimer);
  document.getElementById('rpPlay').textContent = '▶';
  document.getElementById('rpPlay').setAttribute('aria-label', '播放');
}
document.getElementById('retmode').onclick = e => { const b = e.target.closest('button'); if (b) setPeriod(+b.dataset.v); };
document.getElementById('rpPeriod').onclick = e => { const b = e.target.closest('button'); if (b) setPeriod(+b.dataset.v); };
document.getElementById('rpPlay').onclick = () => { if (playing) pauseReplay(); else playReplay(); };
{
  const sl = document.getElementById('rpSlider');
  sl.max = String(R_DAYS - 1); sl.value = String(dayIdx);
  sl.addEventListener('input', () => { pauseReplay(); setDay(+sl.value, 300); });
  document.getElementById('replayBar').hidden = R_DAYS < 2;
  // 手机：异动观察面板紧贴回放条下沿（回放条含风险偏好时会换行变高）
  const syncRpBottom = () => { const rb = document.getElementById('replayBar'); const b = rb.hidden ? 52 : rb.getBoundingClientRect().bottom; document.documentElement.style.setProperty('--rp-bottom', Math.round(b) + 'px'); };
  syncRpBottom(); addEventListener('resize', syncRpBottom); requestAnimationFrame(syncRpBottom);
  syncPeriodButtons();
}
setRetTargets(1); stepRet(performance.now() + 10);
{
  const leadBtn = document.querySelector('#edgemode button[data-v="lead"]');
  leadBtn.classList.add('exp');
  leadBtn.innerHTML = LEAD_SIG_COUNT === 0 ? '领先·实验<small>0 显著</small>' : '领先·实验';
  leadBtn.title = LEAD_SIG_COUNT === 0 ? '实验功能：当前没有领先边通过 BH-FDR 显著性检验' : '实验功能：领先—滞后关系，需结合 FDR 与样本外命中阅读';
}
{
  const cm = document.getElementById('colormode');
  const sync = () => [...cm.children].forEach(x => x.classList.toggle('active', x.dataset.v === colorBy));
  if (!COMM.length) cm.style.display = 'none';
  sync();
  if (!STYLE) document.getElementById('cmStyle').style.display = 'none';
  const linkBtn = document.getElementById('styleLinkBtn');
  const syncLink = () => { linkBtn.hidden = colorBy !== 'style'; linkBtn.classList.toggle('active', styleLinksOn); linkBtn.textContent = (styleLinksOn ? '✓ ' : '+ ') + '同风格连线'; };
  window.__setColorBy = v => {
    if (v === 'style' && !STYLE) return;
    colorBy = v; sync(); syncLink();
    if (v !== 'style' && styleFilter) { setStyleFilter(null); }
    applyNodeAppearance();
    if (!coreIds.size && commFocus == null) rebuildLabels();
  };
  cm.onclick = e => { const b = e.target.closest('button'); if (b) window.__setColorBy(b.dataset.v); };
  linkBtn.onclick = () => { styleLinksOn = !styleLinksOn; syncLink(); updateWebColors(); };
  syncLink();
}
// ---------------- 风格：筛选 / 详情徽章 / 判定依据 / 悬停提示 ----------------
function setStyleFilter(k) {
  styleFilter = k && STYLE && STYLES.includes(k) ? k : null;
  for (let i = 0; i < N; i++) sMask[i] = !styleFilter || styleOf(DATA.nodes[i].id) === styleFilter ? 1 : STYLE_DIM;
  if (styleLinks) {
    const c = styleLinks.geometry.attributes.color, c0 = styleLinks.userData.col0, seg = styleLinks.userData.seg;
    for (let k2 = 0; k2 < seg.length; k2++) { const f = !styleFilter || seg[k2] === styleFilter ? 1 : 0.06; for (let q = 0; q < 6; q++) c.array[k2 * 6 + q] = c0[k2 * 6 + q] * f; }
    c.needsUpdate = true;
  }
  for (let i = 0; i < N; i++) paintNode(i);
  paintRings(); updateWebColors(); renderCommLegend(); renderLegendScale(); renderBoard();
  if (!coreIds.size && commFocus == null && !sw1Sel && !conceptSel) { renderPanel(null, []); rebuildLabels(); resetView(); } else updateLabelOpacity();
}
{
  const t = document.getElementById('pTitle');
  if (t) {
    const hd = document.createElement('div'); hd.className = 'p-hd';
    t.parentNode.insertBefore(hd, t); hd.appendChild(t);
    if (STYLE) hd.insertAdjacentHTML('beforeend', `<button type="button" class="info-btn style-badge" id="pStyle" data-info="styleWhy" hidden aria-haspopup="dialog" aria-controls="infoPop" aria-expanded="false"></button>`);
    hd.insertAdjacentHTML('beforeend', `<button type="button" class="sw1-badge" id="pSw1" hidden></button>`);
    document.getElementById('pSw1').onclick = e => { const k = e.currentTarget.dataset.sw1; if (k) { document.getElementById('q').value = k; setSw1(k); } };
  }
}
function setSw1(k) {
  k = k && SW1_MEMBERS[k] ? k : null;
  if (typeof stopTour === 'function' && !touring) stopTour();
  coreIds = new Set(); commFocus = null; conceptSel = null; expandOptions = []; renderExpandChips();
  sw1Sel = k;
  refreshFocus();
  if (k) setDockMode('detail', { fromFocus: true });
  resetView();
  renderTrail();
}
function renderSw1Panel(k) {
  const ids = SW1_MEMBERS[k] || [], a = sw1Agg(k);
  const rows = ids.map(id => ({ id, v: retOf(nodeById[id]) })).sort((x, y) => (y.v ?? -1e9) - (x.v ?? -1e9));
  syncStyleBadge(null);
  document.getElementById('pTitle').textContent = k;
  document.getElementById('pMeta').innerHTML = `申万一级 · ${ids.length} 个二级${infoBtn('sw1', '申万一级说明')}`;
  document.getElementById('pKv').innerHTML = `<b>${periodLabel()}均值</b><span class="${a ? (a.avg >= 0 ? 'pos' : 'neg') : ''}">${a ? fmtRet(a.avg) : '—'}</span><b>上涨</b><span>${a ? `${a.nUp}/${a.n}（${Math.round(a.up * 100)}%）` : '—'}</span><b>日期</b><span data-retdate>${R_DATES[dayIdx] || '-'}</span>`;
  const body = document.getElementById('pBody');
  body.innerHTML = `<ul class="list sw1-list">${rows.map(r => { const c = commOf(r.id); return `<li data-id="${r.id}"><span>${r.id}${styleTag(r.id)}<span class="sub2">${cdot(c)}${commName(c)}</span></span><span class="${r.v == null ? '' : r.v >= 0 ? 'pos' : 'neg'}">${fmtRet(r.v)}</span></li>`; }).join('')}</ul>`;
  body.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); step([li.dataset.id]); });
  renderMembers(null); renderSuggest(null);
}
{
  const presets = document.getElementById('presets');
  if (presets && SW1_LIST.length > 1) {
    presets.insertAdjacentHTML('beforebegin', `<div class="sw1-row"><select id="sw1Select" aria-label="申万一级"><option value="">申万一级 · 全部（${SW1_LIST.length}）</option>${SW1_LIST.map(k => `<option value="${k}">${k}（${SW1_MEMBERS[k].length}）</option>`).join('')}</select><button type="button" class="chip" id="sw1Clear" hidden>× 清除</button>${infoBtn('sw1', '申万一级说明')}</div>`);
    document.getElementById('sw1Select').onchange = e => { const v = e.target.value || null; if (v) document.getElementById('q').value = v; setSw1(v); };
    document.getElementById('sw1Clear').onclick = () => setSw1(null);
  }
}
function syncSw1Ui() {
  const sel = document.getElementById('sw1Select'); if (!sel) return;
  sel.value = sw1Sel || ''; sel.classList.toggle('on', !!sw1Sel);
  document.getElementById('sw1Clear').hidden = !sw1Sel;
}
// ---------------- 题材 / 概念叠加层：同花顺题材、东财概念 → 成分股所在的申万二级 ----------------
// 内嵌只有概念名索引（DATA.concepts.list）；成分明细在 sector_concepts.json，第一次选概念时才加载。
const CONCEPTS = DATA.concepts && Array.isArray(DATA.concepts.list) && DATA.concepts.list.length ? DATA.concepts : null;
const CC_LABEL = (CONCEPTS && CONCEPTS.sources) || {};
const CC_ORDER = Object.fromEntries(Object.keys(CC_LABEL).map((k, i) => [k, i]));  // 同分时按配置里的来源顺序
const CC_LIST = CONCEPTS ? CONCEPTS.list.map(([src, name, n, nIn, nSec]) => ({ src, name, n, nIn, nSec, key: `${src}:${name}`, norm: ccNorm(name) })) : [];
let ccLoad = null, ccToken = 0;
function ccNorm(s) { return String(s || '').trim().toLowerCase().replace(/[\s（）()·\-_]/g, '').replace(/(概念股?|题材|板块)$/, ''); }
function ccBigrams(s) { const out = new Set(); for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2)); if (s.length === 1) out.add(s); return out; }
// 打分：同名 100 > 前缀 80 > 包含 60 > 被包含 50 > 字对重合（相近，只做推荐）20–40
function conceptMatches(q, limit = 12) {
  const qn = ccNorm(q); if (!qn || !CC_LIST.length) return [];
  const qb = ccBigrams(qn), out = [];
  for (const c of CC_LIST) {
    let s = 0;
    if (c.norm === qn) s = 100; else if (c.norm.startsWith(qn)) s = 80; else if (c.norm.includes(qn)) s = 60;
    else if (qn.length >= 2 && c.norm.length >= 2 && qn.includes(c.norm)) s = 50;
    else if (qb.size) { let hit = 0; for (const b of ccBigrams(c.norm)) if (qb.has(b)) hit++; if (hit) s = 20 + 20 * hit / qb.size; }
    if (s) out.push({ ...c, score: s });
  }
  out.sort((a, b) => b.score - a.score || (a.score >= 50 ? a.name.length - b.name.length : 0) || (CC_ORDER[a.src] ?? 9) - (CC_ORDER[b.src] ?? 9) || b.n - a.n);
  return out.slice(0, limit);
}
function loadConcepts() {
  if (!CONCEPTS) return Promise.reject(new Error('未生成概念数据'));
  if (!ccLoad) ccLoad = fetch(CONCEPTS.file, { credentials: 'same-origin', cache: 'no-cache' }).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }).catch(e => { ccLoad = null; throw e; });
  return ccLoad;
}
function ccSrcTag(src) { return `<span class="cc-src cc-${escHtml(src)}">${escHtml(CC_LABEL[src] || src)}</span>`; }
function ccFind(key) { return CC_LIST.find(c => c.key === key) || null; }
async function selectConcept(m, { alts = null } = {}) {
  if (typeof m === 'string') m = ccFind(m);
  if (!m) return false;
  if (typeof stopTour === 'function' && !touring) stopTour();
  const tok = ++ccToken;
  let lz;
  try { lz = await loadConcepts(); } catch (e) {
    document.getElementById('pTitle').textContent = m.name;
    document.getElementById('pBody').innerHTML = `<div class="empty">概念成分加载失败（${escHtml(e.message)}），请重新生成：python -m scripts.reports.gen_sector_corr_cloud --from-cache</div>`;
    setDockMode('detail', { fromFocus: true }); return false;
  }
  if (tok !== ccToken) return false;
  const idx = (lz.concepts || {})[m.key] || [], F = lz.fields || ['code', 'name', 'sector', 'last', 'r20'];
  const stocks = idx.map(i => { const r = lz.stocks[i] || []; return Object.fromEntries(F.map((f, k) => [f, r[k] ?? null])); });
  const bySec = {}, loose = [];
  for (const s of stocks) { if (s.sector && nodeById[s.sector]) (bySec[s.sector] ||= []).push(s); else loose.push(s); }
  const secIds = Object.keys(bySec);
  const cnt = id => bySec[id].length, share = id => cnt(id) / Math.max(cnt(id), +(nodeById[id].n || 0));
  const maxCnt = Math.max(1, ...secIds.map(cnt)), maxShare = Math.max(1e-6, ...secIds.map(share));
  const w = Object.fromEntries(secIds.map(id => [id, 0.65 * Math.sqrt(cnt(id) / maxCnt) + 0.35 * Math.sqrt(share(id) / maxShare)]));
  secIds.sort((a, b) => cnt(b) - cnt(a) || share(b) - share(a));
  const top = secIds.filter(id => cnt(id) === maxCnt);
  const cores = maxCnt >= 2 && top.length <= 3 ? top : [];
  const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
  const lasts = stocks.map(s => s.last).filter(v => v != null), r20s = stocks.map(s => s.r20).filter(v => v != null);
  const same = CC_LIST.filter(c => c.name === m.name && c.key !== m.key);
  const others = (alts || conceptMatches(m.name, 8)).filter(c => c.key !== m.key && !same.some(x => x.key === c.key)).slice(0, 5);
  coreIds = new Set(); commFocus = null; sw1Sel = null; expandOptions = []; renderExpandChips();
  conceptSel = { key: m.key, src: m.src, name: m.name, label: CC_LABEL[m.src] || m.src, stocks, bySec, loose, secIds, w, cores, cnt: Object.fromEntries(secIds.map(id => [id, cnt(id)])),
    share: Object.fromEntries(secIds.map(id => [id, share(id)])), agg: { last: mean(lasts), r20: mean(r20s), nLast: lasts.length, nR20: r20s.length, up: lasts.filter(v => v > 0).length },
    asOf: lz.as_of || CONCEPTS.as_of || '', same, others };
  document.getElementById('q').value = m.name;
  document.getElementById('hints').innerHTML = '';
  refreshFocus();
  setDockMode('detail', { fromFocus: true });
  resetView();
  renderTrail();
  return true;
}
function clearConcept() { if (!conceptSel) return; conceptSel = null; refreshFocus(); resetView(); }
function conceptTransition() {
  const c = conceptSel;
  startTransition({ origin: c.cores[0] || c.secIds[0] || null, cores: new Set(c.cores), hop1: new Set(c.secIds.filter(id => !c.cores.includes(id))), strength: id => c.w[id] ?? 0.3 });
}
function conceptLabels(limit) {
  const c = conceptSel;
  setLabels(c.secIds.slice(0, limit).map((id, i) => ({ id, text: `${id} ${c.cnt[id]}只`, color: i === 0 ? '#ffd27a' : '#e8eefc', priority: 200 - i, pin: c.cores.includes(id) })));
}
function renderConceptPanel() {
  const c = conceptSel, a = c.agg, cls = v => v == null ? '' : v >= 0 ? 'pos' : 'neg';
  syncStyleBadge(null);
  document.getElementById('pTitle').textContent = c.name;
  document.getElementById('pMeta').innerHTML = `${ccSrcTag(c.src)}题材 · ${c.stocks.length} 只成分 · 落在 ${c.secIds.length} 个二级${infoBtn('concept', '题材叠加说明')}`;
  document.getElementById('pKv').innerHTML = `<b>成分</b><span>${c.stocks.length} 只${c.loose.length ? `（点云外 ${c.loose.length}）` : ''}</span><b>等权当日</b><span class="${cls(a.last)}">${fmtRet(a.last)}${a.nLast ? ` · ${a.up}/${a.nLast} 涨` : ''}</span><b>等权近${CONCEPTS.return_days || 20}日</b><span class="${cls(a.r20)}">${fmtRet(a.r20)}</span><b>行情</b><span>${escHtml(c.asOf || '-')}</span>`;
  const chip = (x, on) => `<button type="button" class="chip cc-alt${on ? ' active' : ''}" data-ck="${escHtml(x.key)}">${ccSrcTag(x.src)}${escHtml(x.name)}<small> ${x.n}</small></button>`;
  const altHtml = (c.same.length || c.others.length) ? `<div class="cc-alts">${c.same.map(x => chip(x)).join('')}${c.others.length ? `<span class="cc-alts-l">相近</span>${c.others.map(x => chip(x)).join('')}` : ''}</div>` : '';
  const stk = s => `<li class="cc-stk" data-id="${escHtml(s.sector || '')}"><span>${escHtml(s.name)}<span class="sub2">${escHtml(s.code)}</span></span><span class="cc-r"><b class="${cls(s.last)}">${fmtRet(s.last)}</b><small class="${cls(s.r20)}">${fmtRet(s.r20)}</small></span></li>`;
  const ord = xs => xs.slice().sort((p, q) => (q.last ?? -1e9) - (p.last ?? -1e9));
  const groups = c.secIds.map(id => { const k = commOf(id); return `<li class="cc-sec" data-id="${escHtml(id)}"><span>${escHtml(id)}${styleTag(id)}<span class="sub2">${c.cnt[id]} 只 · 占该二级 ${Math.round(c.share[id] * 100)}% · ${cdot(k)}${commName(k)}</span></span><span class="cc-w" style="--w:${Math.round(c.w[id] * 100)}%"></span></li>${ord(c.bySec[id]).map(stk).join('')}`; }).join('');
  const loose = c.loose.length ? `<li class="cc-sec cc-loose"><span>点云外<span class="sub2">无申万二级映射，或其二级不在点云</span></span><span></span></li>${ord(c.loose).map(stk).join('')}` : '';
  const body = document.getElementById('pBody');
  body.innerHTML = `${altHtml}<div class="sec"><span>按申万二级分组（成分数 · 占比）</span><span>当日 / 近${CONCEPTS.return_days || 20}日</span></div><ul class="list cc-list">${groups}${loose}</ul><div class="cc-foot"><button type="button" class="chip" id="ccClear">× 清除题材</button></div>`;
  body.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { const id = li.dataset.id; if (!id || !nodeById[id]) return; document.getElementById('q').value = id; setExpandOptions([]); step([id]); });
  body.querySelectorAll('.cc-alt').forEach(b => b.onclick = () => selectConcept(b.dataset.ck));
  document.getElementById('ccClear').onclick = () => { clearConcept(); };
  renderMembers(null); renderSuggest(null);
}
function conceptSuggestPanel(q, cm) {
  renderPanel(null, []);
  document.getElementById('pTitle').textContent = '未找到';
  document.getElementById('pMeta').innerHTML = `没有名为「${escHtml(q)}」的二级、一级或题材${infoBtn('concept', '题材叠加说明')}`;
  document.getElementById('pKv').innerHTML = '';
  document.getElementById('pBody').innerHTML = cm.length ? `<div class="sec"><span>相近题材（点击查看）</span><span></span></div><div class="cc-alts">${cm.slice(0, 8).map(x => `<button type="button" class="chip cc-alt" data-ck="${escHtml(x.key)}">${ccSrcTag(x.src)}${escHtml(x.name)}<small> ${x.n}</small></button>`).join('')}</div>` : `<div class="empty">也没有相近的题材。</div>`;
  document.getElementById('pBody').querySelectorAll('.cc-alt').forEach(b => b.onclick = () => selectConcept(b.dataset.ck));
  setDockMode('detail', { fromFocus: true });
}
// 题材提示：题材模式列出全部匹配（含相近）；板块模式只在没有二级 / 一级同名时补充 ≥ 包含级别的题材
function renderConceptHints(q) {
  const box = document.getElementById('hints');
  if (!CONCEPTS) { box.innerHTML = ''; return; }
  let cm = conceptMatches(q, qMode === 'concept' ? 10 : 5);
  if (qMode !== 'concept') { const qt = q.trim(); if (exactMatches(qt).length || SW1_MEMBERS[qt] || styleQuery(qt) || qt.length < 2) cm = []; cm = cm.filter(x => x.score >= 50); }
  if (!cm.length) { box.innerHTML = qMode === 'concept' ? `<div class="empty">未匹配到题材</div>` : ''; return; }
  box.innerHTML = cm.map(x => `<div data-ck="${escHtml(x.key)}" class="cc-hint"><b>${escHtml(x.name)}</b>${ccSrcTag(x.src)} ${x.n} 只 · ${x.nSec} 个二级${x.score < 50 ? ' · 相近' : ''}</div>`).join('');
  box.querySelectorAll('div[data-ck]').forEach(el => el.onclick = () => selectConcept(el.dataset.ck, { alts: cm }));
}
// 板块模式的题材分支：同名题材（无二级同名 / 查询扩展同名词时）直接选中；二级模糊也落空时取包含级别的题材，否则给相近推荐
function conceptQuery(qt, stage) {
  if (!CONCEPTS || !qt) return false;
  const cm = conceptMatches(qt);
  if (stage === 'exact') { if (cm.length && cm[0].score >= 100) { selectConcept(cm[0], { alts: cm }); return true; } return false; }
  if (cm.length && cm[0].score >= 50) { selectConcept(cm[0], { alts: cm }); return true; }
  if (cm.length) { conceptSuggestPanel(qt, cm); return true; }
  return false;
}
if (CONCEPTS) {
  const qm = document.getElementById('qmode');
  qm.insertAdjacentHTML('beforeend', `<button type="button" data-v="concept" title="同花顺题材 / 东财概念">题材</button>`);
}
function syncSw1Badge(id) {
  const b = document.getElementById('pSw1'); if (!b) return;
  b.hidden = !id; if (!id) return;
  const k = sw1Of(id); b.dataset.sw1 = k; b.textContent = `一级 · ${k}`; b.title = `申万一级「${k}」：点击查看该行业全部 ${(SW1_MEMBERS[k] || []).length} 个二级`;
}
function syncStyleBadge(id) {
  syncSw1Badge(id);
  const b = document.getElementById('pStyle'); if (!b) return;
  const t = id && STYLE ? STYLE.tags[id] : null;
  b.hidden = !t; if (!t) return;
  b.style.setProperty('--sc', styleHex(t.p)); b.style.setProperty('--sc2', t.s ? styleHex(t.s) : '');
  b.innerHTML = `<i></i>${t.p}${t.s ? `<small>/ ${t.s}</small>` : ''}${t.src !== 'rule' ? '<small>· 人工</small>' : ''}`;
  b.title = `${id} · ${styleText(id)}：点击查看判定依据`;
  b.setAttribute('aria-label', `${id} 风格 ${styleText(id)}，查看判定依据`);
  b.dataset.id = id;
}
function styleWhyHtml(id) {
  const t = STYLE && STYLE.tags[id]; if (!t) return '<p>暂无风格数据</p>';
  const R = STYLE.rules || {}, W = R.risk_weights || { beta: 0.5, vol: 0.3, mdd: 0.2 };
  const pc = x => x == null ? '—' : Math.round(x * 100) + '%', f2 = x => x == null ? '—' : (+x).toFixed(2);
  const hi = R.risk_hi ?? 0.7, lo = R.risk_lo ?? 0.3, l1 = (nodeById[id] || {}).l1 || '-';
  const sw = k => `<b style="color:${styleHex(k)}">${k}</b>`;
  const lines = [];
  if (t.src !== 'rule') {
    lines.push(`<b>人工指定</b>：${t.src === 'override' ? `二级覆盖 <code>style.overrides</code>「${escHtml(id)}」` : `一级覆盖 <code>style.l1_overrides</code>「${escHtml(l1)}」`} → ${sw(t.p)}${t.rule && t.rule !== t.p ? `；规则结果为 ${sw(t.rule)}，${t.s === t.rule ? '降为副标签' : '未采用'}` : '；与规则结果一致'}`);
  }
  const verdict = t.risk == null ? '无足够样本' : t.risk >= hi ? `≥ ${hi} → 进攻` : t.risk <= lo ? `≤ ${lo} → 防御` : `介于 ${lo}–${hi}，风险维度不定`;
  lines.push(`<b>风险分</b> <span class="why-v">${f2(t.risk)}</span>（${verdict}）= ${W.beta}×beta 分位 <span class="why-v">${pc(t.bp)}</span> + ${W.vol}×波动分位 <span class="why-v">${pc(t.vp)}</span> + ${W.mdd}×回撤分位 <span class="why-v">${pc(t.mp)}</span>`);
  lines.push(`原始值：beta <span class="why-v">${f2(t.beta)}</span> · 年化波动 <span class="why-v">${t.vol == null ? '—' : t.vol + '%'}</span> · 最大回撤 <span class="why-v">${pc(t.mdd)}</span>（近 ${STYLE.lookback || '-'} 个交易日，对 ${escHtml(STYLE.bench || '基准')}）`);
  const nat = {
    cyc_l1: `${sw('周期')}：一级行业「${escHtml(l1)}」在周期名单（<code>style.cyclical_l1</code>）`,
    cyc_sw2: `${sw('周期')}：该二级在周期名单（<code>style.cyclical_sw2</code>）`,
    pe: `${sw('成长')}：市盈率中位数 <span class="why-v">${t.pe ?? '—'}</span>，横截面分位 <span class="why-v">${pc(t.pp)}</span> ≥ ${pc(R.growth_pe_pct ?? 0.6)}`,
    loss: `${sw('成长')}：亏损股占比 <span class="why-v">${pc(t.loss)}</span> ≥ ${pc(R.loss_share_growth ?? 0.4)}（市盈率分位 ${pc(t.pp)}）`,
    value: `${sw('价值')}：市盈率分位 <span class="why-v">${pc(t.pp)}</span> < ${pc(R.growth_pe_pct ?? 0.6)}${t.pe == null ? '（无市盈率数据，按价值处理）' : `，市盈率中位数 ${t.pe}`}`,
  }[t.nat];
  if (nat) lines.push(`<b>属性</b>：${nat}`);
  lines.push(t.src !== 'rule' ? '主标签取人工指定；规则结果见上。' : (t.risk != null && (t.risk >= hi || t.risk <= lo) ? '风险维度命中 → 主标签取进攻/防御，属性作副标签。' : '风险维度未命中 → 主标签取属性；风险分 ≥0.6 / ≤0.4 时副标签为进攻 / 防御。'));
  return `<p>${sw(t.p)}${t.s ? ` / ${sw(t.s)}（副）` : ''} · 一级 ${escHtml(l1)}</p>${infoList(lines)}<p style="opacity:.8">风格标签是结构描述，不是投资建议。</p>`;
}
const nodeTip = document.createElement('div'); nodeTip.className = 'node-tip'; nodeTip.id = 'nodeTip'; nodeTip.setAttribute('role', 'tooltip'); document.body.appendChild(nodeTip);
let tipTimer = 0, hoverRaf = 0, hoverEv = null, tipId = null;
function showNodeTip(id, x, y, ms = 0) {
  const r = retOf(nodeById[id]);
  nodeTip.innerHTML = `<b>${escHtml(id)}</b>${styleTag(id, true)}${STYLE && STYLE.tags[id] && STYLE.tags[id].s ? `<small style="color:var(--muted)">/ ${STYLE.tags[id].s}</small>` : ''}<span class="tip-l1">${escHtml(sw1Of(id))}</span><span class="${r == null ? '' : r >= 0 ? 'pos' : 'neg'}">${periodLabel()} ${fmtRet(r)}</span>`;
  nodeTip.classList.add('show'); tipId = id;
  const w = nodeTip.offsetWidth, h = nodeTip.offsetHeight;
  if (ms && MOBILE_MQ.matches) {  // 手机点按：镜头会飞走、抽屉会弹出，提示固定放在顶部浮层下方居中
    const sp = document.getElementById('sigPanel'), sr = sp ? sp.getBoundingClientRect() : null, top = sr && sr.height ? sr.bottom + 8 : 12;
    nodeTip.style.left = Math.max(6, (innerWidth - w) / 2) + 'px'; nodeTip.style.top = top + 'px';
  } else {
    nodeTip.style.left = Math.max(6, Math.min(innerWidth - w - 6, x + 14)) + 'px';
    nodeTip.style.top = Math.max(6, Math.min(innerHeight - h - 6, y - h - 12)) + 'px';
  }
  clearTimeout(tipTimer); if (ms) tipTimer = setTimeout(hideNodeTip, ms);
}
function hideNodeTip() { nodeTip.classList.remove('show'); tipId = null; clearTimeout(tipTimer); }
canvas.addEventListener('pointermove', ev => {
  if (ev.pointerType !== 'mouse') return;
  if (ev.buttons) { hideNodeTip(); return; }
  hoverEv = ev;
  if (hoverRaf) return;
  hoverRaf = requestAnimationFrame(() => {
    hoverRaf = 0;
    const i = pickNode(hoverEv.clientX, hoverEv.clientY);
    canvas.style.cursor = i >= 0 ? 'pointer' : '';
    if (i >= 0) showNodeTip(DATA.nodes[i].id, hoverEv.clientX, hoverEv.clientY); else hideNodeTip();
  });
});
canvas.addEventListener('pointerleave', ev => { if (ev.pointerType !== 'mouse') return; hideNodeTip(); canvas.style.cursor = ''; });  // 触屏抬手也会触发 leave，不能关掉点按提示
// ---------------- 上下文说明（共享 ⓘ popover：点外部 / Esc 关闭；不支持 popover 时退回 class 切换） ----------------
function infoBtn(key, label) {
  return `<button type="button" class="info-btn" data-info="${key}" aria-haspopup="dialog" aria-controls="infoPop" aria-expanded="false" aria-label="${label || '说明'}">ⓘ</button>`;
}
let footFullHtml = '';
const infoList = items => `<ul class="ip-list">${items.filter(Boolean).map(x => `<li>${x}</li>`).join('')}</ul>`;
const INFO = {
  legend: { title: '读图说明 · 图例编码', node: 'lgGuideSrc' },
  ctrl: { title: '关于这张图', html: () => {
    const sub = (document.querySelector('#paneCtrl .sub') || {}).textContent || '';
    return `<p>${escHtml(sub.trim())}</p><p>${escHtml(DATA.note || '')}</p>`;
  } },
  board: { title: '涨跌榜说明', html: () => `<p>按当前收益窗口（<b>${periodLabel()}</b>）排序，行情日期 <b>${DATA.market_as_of || DATA.end || '-'}</b>；红涨绿跌。点击条目聚焦该板块，回放时随日期变化。</p>` },
  detail: { title: '方法与口径', html: () => {
    const soft = DATA.edges.filter(e => e.soft).length;
    return infoList([
      `${DATA.n_sectors} 个申万二级板块；同步边 <b>${DATA.edges.length - soft}</b> 条多年复核${soft ? `，另 <b>${soft}</b> 条补充（未复核，仅让孤立板块有参照）` : ''}；阈值 |ρ|≥${DATA.corr_thr}`,
      escHtml(DATA.note || ''),
      '全局视图下点击社区进入该簇；聚焦后显示核心板块与一跳邻居。',
    ]) + (footFullHtml ? `<p><b>数据与样本</b></p>${infoList(footFullHtml.split(' · '))}` : '');
  } },
  data: { title: '数据与样本', html: () => infoList(footFullHtml.split(' · ')) },
  lead: { title: '领先传导（实验）', html: () => infoList([
    escHtml(leadNoticeText()),
    escHtml(leadSummaryText()),
    `箭头 = 周频残差交叉相关领先（滞后周数），循环平移零分布 + BH-FDR（α=${(DATA.lead_summary || {}).fdr_alpha ?? '-'}）检验，并做滚动样本外验证。非因果，仅统计倾向。`,
    '只显示当前中心板块最强的入向与出向连接；未通过 FDR 的候选默认隐藏，可在「控」中打开「显示未通过FDR的边」以虚线查看。',
  ]) },
  signals: { title: '异动观察 · 说明', html: () => `<p>描述性信号，不是预测：每天只用当日及之前的板块日收益，说明「发生了什么」；ρ、社区与桥梁为全样本结构估计。点击条目聚焦。</p>` + infoList([
    '<b>逆簇</b>：方向与所在社区多数相反',
    '<b>脱钩</b>：长期高 ρ 的搭档近几日累计收益背离',
    '<b>持续</b>：同向连涨/连跌且放大，或其后的首个反向日',
    '<b>桥梁</b>：桥梁板块大幅波动，观察远端社区 1–2 日内是否跟随',
    '<b>分化</b>：社区中位数接近 0 但内部离散度高',
    '<b>风格</b>：进攻 − 防御价差一方连续占优后被反超，或处于近期极值',
    '分数折算为当日横截面稳健 σ 的倍数后统一排序；严格阈值下条目不足时放宽阈值补足（淡色显示）；「第 N 天」= 连续出现天数。',
  ]) },
  risk: { title: '风险偏好', html: () => {
    const now = (document.getElementById('rpRA') || {}).title || '';
    return `<p>风险偏好 = 进攻板块均值 − 防御板块均值（所选收益区间）；为正 = 偏进攻（risk-on）。小折线 = 回放窗口内逐日价差的累计，只画到当前日期。</p>${now ? `<p class="ip-kv">${escHtml(now)}</p>` : ''}<p>进攻 / 防御标签的划分见「着色与风格」说明。</p>`;
  } },
  style: { title: '着色与风格', html: () => infoList([
    '<b>按簇</b>：节点色 = 同步图社区；<b>按涨跌</b>：节点色 = 所选区间涨跌（红涨绿跌）。',
    STYLE ? `<b>风格视角</b>：节点色 = 风格（${STYLES.map(k => `${k} ${STYLE.counts[k] ?? 0}`).join(' / ')}），「同风格连线」连接最近的同风格板块；图例换成各风格的均值与上涨占比。` : '',
    STYLE && STYLE.note ? escHtml(STYLE.note) : '',
    STYLE ? '进攻 / 防御由风险分划分（beta、波动、回撤），周期 / 成长 / 价值来自行业属性与估值；风格标签为结构描述，不是投资建议。' : '',
  ]) },
};
INFO.sw1 = { title: '申万一级', html: () => {
  const a = sw1Sel ? sw1Agg(sw1Sel) : null;
  return infoList([
    `申万一级 = 板块所属的申万一级行业（${SW1_LIST.length} 个，来自成分股的行业映射）；点云里每个点仍是申万二级。`,
    '选择一个一级行业：高亮它的二级板块、其余变暗，镜头飞过去取景；详情列出各二级的区间涨跌、风格标签和所属同步社区。',
    '一级汇总 = 所含二级板块的等权平均涨跌与上涨占比（二级之间不按市值加权）；回放时随日期更新。',
    '再点同一行业、选「全部」、点「× 清除」或「全局」即取消；点其中一个二级会转为聚焦该板块。',
    '「按申万一级」着色：节点色 = 一级行业；图例每行是一个一级（二级数 | 上涨占比 | 均值），点击行与上面的选择相同。',
    '搜索框输入一级全名（如「电力设备」「医药生物」）即选中该行业；若输入同时是某个二级名（忽略末尾「Ⅱ」）或查询扩展里的精确项，按原规则聚焦二级；其余模糊词（如「新能源」）仍走查询扩展。',
    a ? `当前：<b>${sw1Sel}</b> · ${periodLabel()}均值 ${fmtRet(a.avg)} · 上涨 ${a.nUp}/${a.n}（${R_DATES[dayIdx] || ''}）` : '',
  ]);
} };
INFO.concept = { title: '题材 / 概念叠加', html: () => {
  const c = conceptSel, by = {};
  for (const x of CC_LIST) by[x.src] = (by[x.src] || 0) + 1;
  return infoList([
    `来源：${Object.entries(by).map(([k, v]) => `${escHtml(CC_LABEL[k] || k)} ${v} 个`).join('、')}（本地分类库，成分为 0/1 关系，无权重；成分少于 ${CONCEPTS ? CONCEPTS.min_members : '-'} 只的不收录；东财板块已滤掉行业 / 地域 / 融资融券等市场属性板块）。`,
    '选中一个题材：点亮其成分股所在的申万二级，其余变暗。亮度和大小 = 该二级里的成分数（主）与占该二级成分的比例（辅）；成分最多的二级加亮放大。',
    '详情按二级分组列出成分股：最近一日涨跌 / 近 20 日累计涨跌；汇总为全部成分的等权平均。这是最新行情快照，不随回放日期变化。',
    '搜索：「题材」模式只搜题材（支持部分名称，并给出相近题材）。「板块」模式下先按原规则：风格词 → 二级同名 → 一级全名 → 查询扩展同名词；然后是题材同名 → 二级模糊匹配 → 题材部分匹配；都没有时列出相近题材。',
    '点二级行或成分股转为聚焦该二级；「× 清除题材」或「全局」取消。',
    c ? `当前：<b>${escHtml(c.name)}</b>（${escHtml(c.label)}）· ${c.stocks.length} 只 · ${c.secIds.length} 个二级 · 行情 ${escHtml(c.asOf || '-')}` : '',
  ]);
} };
INFO.styleWhy = { title: '风格判定依据', html: () => { const id = (document.getElementById('pStyle') || {}).dataset?.id || [...coreIds][0]; return id ? `<p><b>${escHtml(id)}</b></p>${styleWhyHtml(id)}` : '<p>请先聚焦一个板块</p>'; } };
const infoPop = document.getElementById('infoPop');
const infoState = { key: null, btn: null, moved: null, downKey: null, dismissAt: -1e9 };
{
  const pop = infoPop, body = document.getElementById('infoBody'), title = document.getElementById('infoTitle');
  const native = typeof pop.showPopover === 'function';
  const isOpen = () => native ? pop.matches(':popover-open') : pop.classList.contains('open');
  const resolve = (k) => k === 'pane' ? ((document.querySelector('.fly-pane.active') || {}).dataset || {}).pane || 'ctrl' : k;
  const restore = () => {
    if (infoState.moved) { document.getElementById('infoSrc').appendChild(infoState.moved); infoState.moved = null; }
    if (infoState.btn) infoState.btn.setAttribute('aria-expanded', 'false');
  };
  const place = (anchor) => {
    const vw = innerWidth, vh = (window.visualViewport && visualViewport.height) || innerHeight, m = 10, mobile = vw <= 900;
    const w = Math.min(mobile ? vw - 2 * m : 380, vw - 2 * m);
    pop.style.width = w + 'px';
    const r = anchor && anchor.isConnected ? anchor.getBoundingClientRect() : { left: vw / 2, right: vw / 2, width: 0, top: vh / 3, bottom: vh / 3 };
    const below = vh - r.bottom - m - 8, above = r.top - m - 8;
    const useBelow = below >= 240 || below >= above;
    pop.style.maxHeight = Math.max(140, Math.min(useBelow ? below : above, vh * (mobile ? 0.62 : 0.7), 560)) + 'px';
    pop.style.left = (mobile ? m : Math.min(Math.max(m, r.left + r.width / 2 - w / 2), vw - w - m)) + 'px';
    const h = pop.offsetHeight;
    pop.style.top = (useBelow ? Math.min(r.bottom + 8, vh - h - m) : Math.max(m, r.top - 8 - h)) + 'px';
  };
  const close = () => { if (native) { if (isOpen()) pop.hidePopover(); } else if (isOpen()) { pop.classList.remove('open'); onClosed(); } };
  const onClosed = () => { if (isOpen()) return; restore(); infoState.key = null; infoState.btn = null; };
  const open = (key, anchor) => {
    const k = resolve(key), def = INFO[k]; if (!def) return;
    restore();
    title.textContent = def.title; body.innerHTML = '';
    if (def.node) { const n = document.getElementById(def.node); if (n) { body.appendChild(n); infoState.moved = n; } }
    else body.innerHTML = def.html();
    infoState.key = k; infoState.btn = anchor || null;
    if (anchor) anchor.setAttribute('aria-expanded', 'true');
    if (!isOpen()) { if (native) pop.showPopover(); else pop.classList.add('open'); }
    pop.scrollTop = 0; place(anchor);
  };
  if (native) pop.addEventListener('toggle', e => { if (e.newState === 'closed') onClosed(); });
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('.info-btn'); if (!b) return;
    e.preventDefault(); e.stopPropagation();
    const k = resolve(b.dataset.info);
    // 原生轻触关闭在 pointerup 时已收起弹层：按下时若同一说明正开着，这次点击就只负责关闭
    const wasOpen = infoState.downKey === k || (infoState.key === k && isOpen());
    infoState.downKey = null;
    if (wasOpen) { close(); if (!native || !isOpen()) onClosed(); return; }
    open(b.dataset.info, b);
  }, true);
  document.getElementById('infoClose').onclick = close;
  // 原生 popover 已有轻触关闭；再补一层 pointerdown/touchstart，兼容触屏与旧版 Safari（点 ⓘ 由 click 处理）
  const outside = e => {
    const b = e.target.closest && e.target.closest('.info-btn');
    if (b) { infoState.downKey = isOpen() && infoState.key === resolve(b.dataset.info) ? infoState.key : null; return; }
    if (isOpen() && !pop.contains(e.target)) { infoState.dismissAt = performance.now(); close(); }  // 这一下只负责关闭，不穿透去选中板块
  };
  document.addEventListener('pointerdown', outside, true);
  document.addEventListener('touchstart', outside, { capture: true, passive: true });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && isOpen()) close(); });
  addEventListener('resize', () => { if (isOpen()) place(infoState.btn); });
  const fh = document.getElementById('flyTitle');
  if (fh) fh.insertAdjacentHTML('afterend', infoBtn('pane', '当前面板说明'));
  window.__scc_info = { open: (k) => open(k, document.querySelector(`.info-btn[data-info="${k}"]`)), close, isOpen, get key() { return infoState.key; }, native, keys: Object.keys(INFO) };
}
// ---------------- 导览：从全局到局部（社区 → 核心 → 桥梁 → 另一社区核心） ----------------
let tour = null;
function buildTour() {
  const steps = [];
  if (!COMM.length) return steps;
  steps.push({ cap: `全局：${COMM.length} 个同步社区（按簇着色），亮丝 = 强同步相关`, run: () => goGlobal() });
  const A = COMM[0];
  steps.push({ cap: `社区「${A.name}」：${A.size} 个板块，主行业 ${A.l1_top}`, run: () => step([], { comm: A.id }) });
  steps.push({ cap: `簇核心 ${A.core}：簇内加权度最高`, run: () => step([A.core]) });
  const br = (GRAPH.bridges || []).find(b => b.comm === A.id) || (GRAPH.bridges || [])[0];
  if (br) {
    steps.push({ cap: `桥梁 ${br.id}：连接「${commName(br.comm)}」与「${br.links.map(commName).join('」「')}」`, run: () => step([br.id]) });
    const B = commById[br.comm === A.id ? br.links[0] : br.comm];
    if (B) steps.push({ cap: `跨到「${B.name}」，簇核心 ${B.core}`, run: () => step([B.core]) });
  }
  return steps;
}
function showCap(t) { const el = document.getElementById('tourCap'); el.textContent = t; el.classList.toggle('show', !!t); }
function nextTour() {
  if (!tour) return;
  tour.i++;
  if (tour.i >= tour.steps.length) { stopTour(); return; }
  const st = tour.steps[tour.i];
  touring = true; try { st.run(); } finally { touring = false; }
  showCap(`导览 ${tour.i + 1}/${tour.steps.length} · ${st.cap}`);
  tour.timer = setTimeout(nextTour, CFG_UI.tour_step_ms ?? 4200);
}
function startTour() {
  const steps = buildTour(); if (!steps.length) return;
  stopTour(); tour = { steps, i: -1, timer: 0 };
  document.getElementById('tourBtn').textContent = '导览 ■';
  nextTour();
}
function stopTour() {
  if (!tour) return;
  clearTimeout(tour.timer); tour = null;
  document.getElementById('tourBtn').textContent = '导览 ▶';
  showCap('');
}
document.getElementById('tourBtn').onclick = () => { if (tour) stopTour(); else startTour(); };
if (!COMM.length) document.getElementById('tourBtn').style.display = 'none';

document.getElementById('showFailed').onclick = () => {
  showFailed = !showFailed;
  const btn = document.getElementById('showFailed');
  btn.classList.toggle('active', showFailed);
  btn.textContent = showFailed ? '隐藏未通过FDR的边' : '显示未通过FDR的边';
  refreshFocus();
};
document.getElementById('showFailed').classList.toggle('active', showFailed);
if (DATA.lead_summary) document.getElementById('leadSum').innerHTML = `领先候选 ${DATA.lead_summary.candidates} · FDR 通过 ${DATA.lead_summary.fdr_pass}${infoBtn('lead', '领先传导说明')}`;

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

// ---------------- 拾取：屏幕像素距离（手指半径更大） ----------------
function pickNode(clientX, clientY) {
  const r = isCoarse() ? (CFG_UI.pick_radius_touch_px ?? 28) : (CFG_UI.pick_radius_px ?? 14);
  let best = -1, bestD = r * r;
  for (let i = 0; i < N; i++) {
    const n = DATA.nodes[i];
    _v.set(n.x, n.y, n.z).project(camera);
    if (_v.z > 1) continue;
    const [sx, sy] = toScreen(_v);
    let d = (sx - clientX) ** 2 + (sy - clientY) ** 2;
    if (focusIds.size && !focusIds.has(n.id)) d *= 1.6;  // 暗化节点略降优先级
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}
let ptrDown = null, ptrDragged = false;
canvas.addEventListener('pointerdown', ev => { if (ev.button != null && ev.button !== 0) return; ptrDown = { x: ev.clientX, y: ev.clientY }; ptrDragged = false; });
canvas.addEventListener('pointermove', ev => { if (!ptrDown) return; if (Math.hypot(ev.clientX - ptrDown.x, ev.clientY - ptrDown.y) > TAP_CANCEL_PX) ptrDragged = true; });
canvas.addEventListener('pointercancel', () => { ptrDown = null; ptrDragged = false; });
canvas.addEventListener('pointerup', ev => {
  if (!ptrDown) return;
  const moved = ptrDragged || Math.hypot(ev.clientX - ptrDown.x, ev.clientY - ptrDown.y) > TAP_CANCEL_PX;
  ptrDown = null; ptrDragged = false; if (moved) return;
  if (performance.now() - infoState.dismissAt < 700) return;
  const i = pickNode(ev.clientX, ev.clientY);
  if (i >= 0) {
    const id = DATA.nodes[i].id; document.getElementById('q').value = id; setExpandOptions([]); step([id]);
    if (ev.pointerType !== 'mouse') showNodeTip(id, ev.clientX, ev.clientY, 2600);
    swallowClickUntil = performance.now() + 450;  // 触屏：抽屉刚弹出，别让这次点按的 click 落到新出现的按钮上
  }
});
let swallowClickUntil = 0;
window.addEventListener('click', e => { if (performance.now() < swallowClickUntil && e.target !== canvas) { e.preventDefault(); e.stopPropagation(); } }, true);
window.addEventListener('resize', () => { renderer.setSize(innerWidth, innerHeight); applyViewOffset(); });

// ---------------- 停靠面板 ----------------
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
  document.querySelectorAll('.rail-btn[data-mode]').forEach(b => b.classList.toggle('active', open && b.dataset.mode === dockMode));
  document.querySelectorAll('.fly-pane').forEach(p => p.classList.toggle('active', open && p.dataset.pane === dockMode));
  if (open) document.getElementById('flyTitle').textContent = MODE_TITLE[dockMode] || '';
  if (persist) { try { sessionStorage.setItem(DOCK_KEY, dockMode); } catch (e) {} }
  applyViewOffset();
  maybeShowZenTip();
}
document.getElementById('dockRail').onclick = e => {
  const b = e.target.closest('.rail-btn'); if (!b) return;
  if (b.dataset.act === 'home') { resetView(); return; }
  const m = b.dataset.mode;
  if (dockMode === m) setDockMode('hidden'); else setDockMode(m);
};
document.getElementById('flyClose').onclick = () => setDockMode('hidden');
if (window.ResizeObserver) { const ro = new ResizeObserver(() => applyViewOffset()); for (const id of ['flyout', 'legend', 'sigPanel', 'replayBar']) { const e = document.getElementById(id); if (e) ro.observe(e); } }

// ---------------- 告警（基准退回 / 关系样本过期） ----------------
(function renderWarnings() {
  const warns = [];
  const bench = DATA.benchmark || {};
  if (bench.warning) warns.push(`⚠ 市场基准：${bench.warning}`);
  const end = DATA.end ? new Date(DATA.end) : null;
  const latest = [DATA.market_as_of, DATA.data_as_of].filter(Boolean).map(d => new Date(d)).sort((a, b) => b - a)[0];
  const staleWeeks = DATA.stale_weeks ?? 2;
  const foot = document.querySelector('.foot');
  if (end && latest) {
    const lagW = (latest - end) / (7 * 86400e3);
    if (lagW > staleWeeks) {
      const msg = `关系样本截止 ${DATA.end}，落后行情 ${lagW.toFixed(1)} 周（>${staleWeeks} 周），请重算：python -m scripts.reports.gen_sector_corr_cloud --no-nav`;
      warns.push(`⚠ ${msg}`);
      if (foot) foot.insertAdjacentHTML('beforeend', ` · <span class="stale">${msg}</span>`);
    }
  }
  if (foot) {
    if (bench.name) foot.insertAdjacentHTML('beforeend', ` · 基准 ${bench.name}${bench.ok ? '' : ' <span class="stale">（非沪深300）</span>'} · 滚动 beta ${DATA.beta_window || '-'} 日`);
    foot.innerHTML = foot.innerHTML.replace(/跟涨观察 \S+ 日/, `事件窗=滞后周 · FDR α=${(DATA.lead_summary || {}).fdr_alpha ?? '-'}`);
    footFullHtml = foot.innerHTML;
    const alerts = [...foot.querySelectorAll('.stale')].map(x => x.outerHTML).join(' · ');
    const soft = DATA.edges.filter(e => e.soft).length;
    foot.innerHTML = `${DATA.n_sectors} 板块 · ${DATA.edges.length - soft} 同步边 · 涨跌榜 ${DATA.market_as_of || DATA.end || '-'}${alerts ? ' · ' + alerts : ''}${infoBtn('data', '数据与样本说明')}`;
  }
  const bar = document.getElementById('warnBar');
  if (warns.length) { bar.innerHTML = warns.map(w => `<div>${w}</div>`).join(''); bar.classList.add('show'); }
})();

// ---------------- 初始化 ----------------
(function initDock() {
  let saved = null;
  try { saved = sessionStorage.getItem(DOCK_KEY); } catch (e) {}
  if (!['ctrl', 'board', 'detail', 'hidden'].includes(saved)) saved = 'ctrl';
  dockMode = '__init__';
  setDockMode(saved, { persist: false });
})();
if (MOBILE_MQ.matches && !CFG_UI.legend_open_mobile) document.getElementById('legend').open = false;
setMode(mode);
applyDeepLink();

let frame = 0;
const frameTimes = [];
function fps() { if (frameTimes.length < 2) return 0; return Math.round((frameTimes.length - 1) * 1000 / (frameTimes[frameTimes.length - 1] - frameTimes[0])); }
(function tick(now) {
  requestAnimationFrame(tick);
  const ms = performance.now();
  frameTimes.push(ms); while (frameTimes.length > 2 && ms - frameTimes[0] > 2000) frameTimes.shift();
  const t = (now || ms) * 0.001;
  const flying = stepFlight(ms);
  breathT = ms / 1000;
  const retMoving = stepRet(ms);
  const litMoving = stepLit(ms);
  if (retMoving && !litMoving) { for (let i = 0; i < N; i++) paintNode(i); paintRings(); updateWebColors(); }
  else if (!litMoving && movers.size && !REDUCED) for (const i of movers) paintNode(i);
  stepPulses(ms);
  if (flowFade < 1) {
    flowFade = Math.max(0, Math.min(1, (ms - flowFadeT0) / flowFadeDur));
    for (const ch of flowGroup.children) if (ch.material && ch.material.userData.base != null) ch.material.opacity = ch.material.userData.base * flowFade;
  }
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
  if (!flying) controls.update();
  if (++frame % 6 === 0) declutterLabels();
  { const cd = camera.position.distanceTo(controls.target); scene.fog.density = 0.0018 * Math.min(1, 380 / Math.max(1, cd)); }  // 取景拉远（手机可见区很小 / 题材分散）时减淡雾，远处高亮点不至于看不清
  renderer.render(scene, camera);
})();
window.__scc = {
  camera, controls, pickNode, three: THREE_SOURCE, fps,
  screenOf: id => { const n = nodeById[id]; if (!n) return null; camera.updateMatrixWorld(); _v.set(n.x, n.y, n.z).project(camera); return _v.z > 1 ? null : toScreen(_v); }, step, goTrail, goGlobal, startTour, stopTour, graph: GRAPH,
  get coreIds() { return [...coreIds]; }, get mode() { return mode; }, get colorBy() { return colorBy; },
  setColorBy: v => window.__setColorBy(v), get riskAppetite() { return raNow; }, get styleSum() { return styleSum; }, get styleLinkCount() { return styleLinks ? styleLinks.userData.n : 0; }, info: () => window.__scc_info, setStyleFilter: k => setStyleFilter(k), get styleFilter() { return styleFilter; }, styleWhy: id => styleWhyHtml(id), get tipId() { return tipId; }, visibleRect: () => visibleRect(), frameIds: () => coreIds.size ? [...new Set([...coreIds, ...egoOf([...coreIds]).one])] : commFocus != null && commById[commFocus] ? commById[commFocus].members.slice() : sw1Sel ? (SW1_MEMBERS[sw1Sel] || []).slice() : conceptSel ? conceptSel.secIds.slice() : styleFilter ? DATA.nodes.filter(n => styleOf(n.id) === styleFilter).map(n => n.id) : [], setSw1: k => setSw1(k), get sw1Sel() { return sw1Sel; }, sw1List: () => SW1_LIST.map(k => [k, SW1_MEMBERS[k].length]), sw1Agg: k => sw1Agg(k), nodeScreen: id => { const n = nodeById[id]; _v.set(n.x, n.y, n.z).project(camera); return toScreen(_v); },
  selectConcept: (k, o) => selectConcept(k, o), clearConcept, conceptMatches: (q, n) => conceptMatches(q, n).map(({ norm, ...x }) => x), loadConcepts,
  get conceptSel() { const c = conceptSel; return c && { key: c.key, name: c.name, src: c.src, n: c.stocks.length, loose: c.loose.length, secs: c.secIds.map(id => [id, c.cnt[id], +c.share[id].toFixed(3), +c.w[id].toFixed(3)]), cores: c.cores, agg: c.agg, asOf: c.asOf }; },
  get commFocus() { return commFocus; }, get trail() { return trail.map(entryLabel); }, get trailIdx() { return trailIdx; },
  get flying() { return !!flight; }, get animating() { return performance.now() < litAnimUntil; }, get pulses() { return pulses.length; },
  get drawCalls() { return renderer.info.render.calls; }, get dayIdx() { return dayIdx; }, get period() { return curP(); },
  get playing() { return playing; }, get movers() { return movers.size; }, get commSummary() { return COMM.map(c => `${c.name} ${sumText(c.id)}`); },
  replayDates: R_DATES, playReplay, pauseReplay, setDay, setPeriod, get webSegments() { return WEB.list.length; }, reduced: REDUCED,
};
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
        payload["lead_display"] = CFG["display"]  # 展示参数随配置即时生效，无需重算结构
        payload["stale_weeks"] = int(CFG["stale_weeks"])
        print("从缓存加载，刷新 stock_index / sector_members…")
        payload = attach_daily(attach_graph(attach_stock_payload(payload)))
        payload = attach_concepts(payload)  # 题材叠加：内嵌概念索引 + 懒加载 output/sector_concepts.json
        OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_HTML.write_text(render_html(payload), encoding="utf-8")
        print(f"从缓存重渲 {OUTPUT_HTML}（节点 {payload.get('n_sectors')} · 边 {len(payload.get('edges', []))} · 股票索引 {len(payload.get('stock_index', []))}）")
        if not args.no_nav:
            update_nav()
        return

    print(f"加载 {START}→今 日收益（含 beta 预热），聚合申万二级…")
    piv, meta = load_sw2_returns()
    mkt, bench = load_benchmark(piv)
    print(f"  基准 {bench['name']}（{bench['code']}）覆盖 {bench['coverage']:.1%}" + (f" ⚠ {bench['warning']}" if bench.get("warning") else ""))
    print(f"去市场 beta（滚动 {BETA_WINDOW} 日，t-1 估计），MDS + 同步边…")
    resid = rolling_residualize(piv, mkt, BETA_WINDOW, BETA_MIN_PERIODS)
    piv, resid = piv.loc[piv.index >= START], resid.loc[resid.index >= START]
    print(f"  板块 {piv.shape[1]} · 交易日 {piv.shape[0]} · 截止 {piv.index.max().date()}")
    payload = attach_daily(attach_graph(pack_payload(piv, resid, meta, bench=bench)))
    payload = attach_concepts(payload)
    g = payload["graph"]
    print(f"  社区 {len(g['communities'])} 个（Q={g['modularity']}）· 桥梁 {len(g['bridges'])} 个")
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
        print(f"  领先样例 {e['source']} → {e['target']} lag={e['lag']} xcorr={e['xcorr']} q={e['q_value']} fdr={e['fdr_pass']} oos_lift={e['oos_lift']}")

    if not args.no_nav:
        update_nav()
        # refresh index to pick up any title tweaks
        import subprocess
        subprocess.run([sys.executable, "-m", "scripts.reports.gen_index"], cwd=PROJECT_DIR, check=False)


if __name__ == "__main__":
    main()
