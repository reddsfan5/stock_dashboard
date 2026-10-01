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

from scripts.reports.sector_lead_stats import build_lead_edges_sig, load_config, rolling_residualize

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
.legend{position:fixed;right:14px;bottom:14px;z-index:5;width:262px;font-size:11px;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:6px 10px 8px;backdrop-filter:blur(10px)}
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
  .legend{right:auto;left:10px;bottom:auto;top:calc(10px + env(safe-area-inset-top,0px));width:auto;max-width:calc(100vw - 20px)}
  .legend[open]{width:min(290px,calc(100vw - 20px))}
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
<details class="legend" id="legend" open>
  <summary>图例</summary>
  <div class="lg-row"><span class="lg-sw"><span class="lg-grad"></span></span><span id="lgScale">红涨 · 绿跌</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-dot" style="width:6px;height:6px"></span><span class="lg-dot" style="width:12px;height:12px"></span></span><span>点大小 = |涨跌|（非成交额/市值）；灰点 = 无行情</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-line"></span></span><span>同步边 · 多年方向复核（红正 / 绿负，越亮越强）</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-line soft"></span></span><span>同步边 · 补充（未复核，仅让孤立板块有参照）</span></div>
  <div class="lg-row"><span class="lg-sw"><span class="lg-arrow"></span></span><span>领先箭头（实验）：金 = 同向，蓝 = 反向；虚线 = 未通过 FDR</span></div>
</details>

<nav class="rail" id="dockRail" aria-label="分析轨道">
  <button type="button" class="rail-btn active" data-mode="ctrl" id="railCtrl" title="搜索与筛选">控</button>
  <button type="button" class="rail-btn" data-mode="board" id="railBoard" title="涨跌榜">榜</button>
  <button type="button" class="rail-btn" data-mode="detail" id="railDetail" title="聚焦详情">详</button>
  <button type="button" class="rail-btn home" data-act="home" id="railHome" title="复位视角" aria-label="复位视角">⟲</button>
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
      <div class="chips expand" id="expandBox" style="display:none"></div>
      <div class="row">
        <div class="seg" id="retmode">
          <button type="button" class="active" data-v="last">当日</button>
          <button type="button" data-v="cum20">近20日</button>
        </div>
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
let retWindow = 'last';
let qMode = 'sector';
let mode = CFG_UI.default_mode === 'lead' ? 'lead' : 'sync';
let coreIds = new Set();
let focusIds = new Set();
let expandOptions = [];      // 查询扩展出的可选板块（默认不聚焦）
let userInteracted = false;
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
camera.position.copy(HOME.pos);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.autoRotateSpeed = CFG_UI.auto_rotate_speed ?? 0.55;
controls.minDistance = 35;
controls.maxDistance = 380;
function syncAutoRotate() { controls.autoRotate = (CFG_UI.auto_rotate ?? true) && !userInteracted && !coreIds.size; }
function markInteracted() { if (!userInteracted) { userInteracted = true; syncAutoRotate(); } }
controls.addEventListener('start', markInteracted);
canvas.addEventListener('wheel', markInteracted, { passive: true });
canvas.addEventListener('touchstart', markInteracted, { passive: true });
canvas.addEventListener('pointerdown', markInteracted);
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

// ---------------- 收益 → 颜色/大小（分位截断，抗离群值） ----------------
function retOf(n) {
  const v = retWindow === 'cum20' ? (n.cum20 ?? n.cum_20 ?? n.ret20) : (n.last ?? n.ret1 ?? n.day);
  return (v == null || Number.isNaN(+v)) ? null : +v;
}
function quantile(arr, q) {
  if (!arr.length) return 0;
  const a = [...arr].sort((x, y) => x - y), pos = (a.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return a[lo] + (a[hi] - a[lo]) * (pos - lo);
}
const CLIP_Q = CFG_UI.color_clip_pct ?? 0.9;
let RET_SCALE = 1;
function computeRetScale() {
  const v = DATA.nodes.map(retOf).filter(x => x != null).map(Math.abs);
  RET_SCALE = Math.max(CFG_UI.min_ret_scale ?? 0.5, quantile(v, CLIP_Q));
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
function applyNodeAppearance() {
  computeRetScale();
  const has = focusIds.size > 0;
  for (let i = 0; i < N; i++) {
    const n = DATA.nodes[i], id = n.id, v = retOf(n);
    let dim = 1, sizeMul = 1, gOp = 0.28;
    const base = sizeFromRet(v);
    if (has) {
      if (coreIds.has(id)) { sizeMul = 1.5; gOp = 0.95; }
      else if (focusIds.has(id)) {
        const s = maxCorrToCores(id);
        const t = Math.min(1, Math.max(0, s <= 0 ? 0.2 : (s - 0.2) / 0.55));
        dim = 0.35 + 0.65 * t; sizeMul = 0.85 + 0.45 * t; gOp = 0.16 + 0.7 * t;
      } else { dim = 0.16; sizeMul = 0.45; gOp = 0.03; }
    }
    const c = colorFromRet(v, dim);
    discs[i].material.color.copy(c);
    discs[i].material.opacity = has && !focusIds.has(id) ? 0.5 : 1;
    const ds = Math.max(1.4, base * 0.34 * sizeMul);
    discs[i].scale.set(ds, ds, 1);
    glows[i].material.color.copy(c);
    glows[i].material.opacity = gOp;
    const gs = base * 1.35 * sizeMul;
    glows[i].scale.set(gs, gs, 1);
  }
  renderLegendScale();
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
const syncLines = buildSyncLines(false); scene.add(syncLines);
const softLines = buildSyncLines(true); scene.add(softLines);

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
  flowMats.push(mat);
  const line = new THREE.Line(geo, mat); line.computeLineDistances(); flowGroup.add(line);
  const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.Float32BufferAttribute(new Array(21).fill(0), 3));
  const pts = new THREE.Points(pg, new THREE.PointsMaterial({ color, size: 1.7, map: discTex, alphaTest: 0.3, transparent: true, opacity: opacity * 0.9, depthWrite: false, blending: THREE.AdditiveBlending }));
  pts.userData = { ax: a.x, ay: a.y, az: a.z, bx: b.x, by: b.y, bz: b.z, n: 7, phase: Math.random() };
  flowGroup.add(pts);
}
function rebuildFlow() {
  clearFlow();
  if (!focusIds.size) {
    if (mode === 'sync') {
      const top = DATA.edges.filter(e => !e.soft).sort((a, b) => b.abs - a.abs).slice(0, 36);
      for (const e of top) {
        const a = nodeById[e.source], b = nodeById[e.target]; if (!a || !b) continue;
        addFlow(a, b, e.sign >= 0 ? 0xff7a7a : 0x3ddc97, 0.2 + 0.22 * Math.min(1, (e.abs - 0.4) / 0.4));
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
  sp.scale.set(w / 10, 4.8, 1); sp.renderOrder = 5; sp.userData.priority = priority;
  return sp;
}
function setLabels(items) {
  disposeChildren(labelGroup);
  for (const it of items) {
    const n = nodeById[it.id]; if (!n) continue;
    const lab = makeLabel(it.text || n.name, it.color || '#e8eefc', it.priority || 0);
    lab.position.set(n.x, n.y + 4.5, n.z); labelGroup.add(lab);
  }
  declutterLabels();
}
function topMovers() {
  const k = CFG_UI.top_movers_labels ?? 8;
  const rows = DATA.nodes.map(n => ({ id: n.id, v: retOf(n) })).filter(r => r.v != null);
  const up = rows.filter(r => r.v > 0).sort((a, b) => b.v - a.v).slice(0, Math.ceil(k / 2));
  const dn = rows.filter(r => r.v < 0).sort((a, b) => a.v - b.v).slice(0, Math.floor(k / 2));
  return [...up, ...dn];
}
function rebuildLabels() {
  if (!coreIds.size) {
    setLabels(topMovers().map((r, i) => ({ id: r.id, text: `${r.id} ${fmtRet(r.v)}`, color: r.v >= 0 ? '#ff9d9d' : '#7fe7b8', priority: 50 - i })));
    return;
  }
  const limit = CFG_UI.label_limit ?? 18;
  const neighbors = [...focusIds].filter(id => !coreIds.has(id))
    .map(id => ({ id, s: maxCorrToCores(id) })).sort((a, b) => b.s - a.s).slice(0, limit);
  setLabels([
    ...[...coreIds].map(id => ({ id, color: '#5ad1ff', priority: 1000 })),
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
  syncLines.visible = mode === 'sync' && !has;
  softLines.visible = mode === 'sync' && !has;
  leadGroup.visible = mode === 'lead';
  if (mode === 'lead') rebuildLeadArrows(has ? coreIds : null);
  rebuildFlow();
  renderBoard();
}
// 面板遮挡时偏移投影中心：桌面左侧面板 → 向右；手机底部抽屉 → 向上，让聚焦点落在可见区域中央。
function applyViewOffset() {
  const W = innerWidth, H = innerHeight;
  const fly = document.getElementById('flyout');
  let ox = 0, oy = 0;
  if (fly.classList.contains('open')) {
    const r = fly.getBoundingClientRect();
    if (MOBILE_MQ.matches) oy = Math.max(0, H - r.top) / 2;
    else ox = -Math.min(r.right, W * 0.45) / 2;
  }
  if (ox || oy) camera.setViewOffset(W, H, ox, oy, W, H); else camera.clearViewOffset();
  camera.aspect = W / H; camera.updateProjectionMatrix();
}
function frameCores() {
  const pts = [...coreIds].map(id => nodeById[id]).filter(Boolean);
  if (!pts.length) return;
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  const cz = pts.reduce((s, p) => s + p.z, 0) / pts.length;
  // 取景覆盖核心 + 一跳邻居（按距离 80% 分位，避免个别远点把镜头拉太远）
  const { one } = egoOf([...coreIds]);
  const ds = [...one].map(id => nodeById[id]).filter(Boolean)
    .map(p => Math.hypot(p.x - cx, p.y - cy, p.z - cz)).sort((a, b) => a - b);
  const r = Math.max(18, ds.length ? ds[Math.min(ds.length - 1, Math.floor(ds.length * 0.8))] : 18);
  const fov = camera.fov * Math.PI / 180;
  const dist = Math.min(260, r / Math.tan(fov / 2) * 1.08);
  const dir = new THREE.Vector3(55, 28, 95).normalize();
  controls.target.set(cx, cy, cz);
  camera.position.set(cx + dir.x * dist, cy + dir.y * dist, cz + dir.z * dist);
  controls.update();
}
function resetView() {
  if (coreIds.size) { frameCores(); return; }
  controls.target.copy(HOME.target); camera.position.copy(HOME.pos); controls.update();
}
function refreshFocus() {
  if (coreIds.size) { const { one, two } = egoOf([...coreIds]); focusIds = new Set([...one, ...two]); }
  else focusIds = new Set();
  applyFocusVisual();
  if (coreIds.size) renderPanel([...coreIds], [...focusIds].filter(id => !coreIds.has(id)));
  else renderPanel(null, []);
  rebuildLabels();
  syncAutoRotate();
}
function setFocus(cores, { moveCamera = true, openDetail = true } = {}) {
  coreIds = new Set(cores);
  refreshFocus();
  if (moveCamera) { if (cores.length) frameCores(); else resetView(); }
  if (openDetail && cores.length) setDockMode('detail', { fromFocus: true });
  renderExpandChips();
}

// ---------------- 面板 ----------------
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
  return `<li data-id="${r.id}" class="${pass ? '' : 'failed'}"><span>${r.id}${badge}${fromTag(r._from)}<span class="sub2">滞后 ${r.lag} 周 · xcorr ${r.xcorr.toFixed(2)} · ${ins}</span>${oos ? `<span class="sub2">${oos}</span>` : ''}</span><span class="leadc">${arrow}</span></li>`;
}
function leadSummaryText() {
  const s = DATA.lead_summary; if (!s) return '';
  const o = (s.oos || {}).all || {}, f = (s.oos || {}).fdr || {};
  const oos = o.n_events ? `滚动样本外 ${o.n_events} 次事件：命中 ${fmtPct1(o.hit)} vs 基准 ${fmtPct1(o.base)}（${fmtLift1(o.lift)}）` : '样本外暂无事件';
  const fo = f.n_events ? `；其中训练段 FDR 通过的边 ${f.n_events} 次：${fmtPct1(f.hit)}（${fmtLift1(f.lift)}）` : '；训练段内无 FDR 通过的边';
  return `领先候选 ${s.candidates} 条，BH-FDR（α=${s.fdr_alpha}，${s.m_tests} 个有序板块对）通过 ${s.fdr_pass} 条。${oos}${fo}。`;
}
function leadNotice() {
  if (LEAD_SIG_COUNT == null) return '';
  return LEAD_SIG_COUNT === 0
    ? `<div class="exp-note">实验功能：当前没有任何领先边通过 BH-FDR，样本外命中与基准持平。箭头仅供提出假设，请以「同步相关」为主。</div>`
    : `<div class="exp-note">实验功能：仅 ${LEAD_SIG_COUNT} 条领先边通过 BH-FDR，请结合样本外命中阅读。</div>`;
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
  if (!cores || !cores.length) {
    if (title) title.textContent = '全局视图';
    if (mode === 'lead') {
      meta.textContent = `${DATA.n_sectors} 个二级 · 尚未聚焦中心板块 · 共 ${(DATA.lead_edges || []).length} 条候选领先边`;
      body.innerHTML = `${leadNotice()}<div class="empty">请先查询或点击一个板块。聚焦后只显示该中心板块最强的入向与出向连接。<br/>${leadSummaryText()}</div>`;
    } else {
      const soft = DATA.edges.filter(e => e.soft).length;
      meta.textContent = `${DATA.n_sectors} 个二级 · 同步边 ${DATA.edges.length - soft} 条多年复核${soft ? ` + ${soft} 条补充` : ''}（|ρ|≥${DATA.corr_thr}）`;
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
    const hiddenNote = hidden && !showFailed ? `<div class="empty">另有 ${hidden} 条候选未通过 BH-FDR（α=${(DATA.lead_summary || {}).fdr_alpha ?? '-'}），已隐藏；可在「控」中打开「显示未通过FDR的边」以虚线查看。</div>` : '';
    body.innerHTML = `${leadNotice()}<div class="sec">它领先谁（箭头指出）</div>${outs.length ? `<ul class="list">${rowOut}</ul>` : `<div class="empty">${emptyMsg}</div>`}<div class="sec">谁领先它（箭头指入）</div>${inns.length ? `<ul class="list">${rowIn}</ul>` : `<div class="empty">${emptyMsg}</div>`}${hiddenNote}<div class="leadsum">${leadSummaryText()}</div>`;
  } else {
    meta.textContent = `同步相关 · 核心 ${cores.length} · 一跳邻居 ${neighborIds.length}`;
    const verified = new Set(DATA.edges.filter(e => !e.soft).flatMap(e => [`${e.source}|${e.target}`, `${e.target}|${e.source}`]));
    const rows = collectFrom(cores.map(c => [c, DATA.neighbors[c] || []]));
    rows.sort((a, b) => b.abs - a.abs);
    body.innerHTML = rows.length
      ? `<ul class="list">${rows.slice(0, 24).map(r => {
          const ok = verified.has(`${r._from}|${r.id}`);
          return `<li data-id="${r.id}"><span>${r.id}${fromTag(r._from)}${ok ? '<span class="tag-ok" title="多年方向复核通过">复核</span>' : ''}</span><span class="${r.corr >= 0 ? 'pos' : 'neg'}">ρ ${r.corr.toFixed(2)}</span></li>`;
        }).join('')}</ul>`
      : `<div class="empty">该簇暂无足够强的稳健相关边，仍可看空间邻近。</div>`;
  }
  body.querySelectorAll('li[data-id]').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); setFocus([li.dataset.id]); });
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
  document.getElementById('boardLabel').textContent = retWindow === 'cum20' ? '近20日' : '当日';
  const rows = DATA.nodes.map(n => ({ id: n.id, v: retOf(n) })).filter(r => r.v != null);
  rows.sort((a, b) => b.v - a.v);
  ul.innerHTML = rows.map(r => {
    const cls = r.v >= 0 ? 'pos' : 'neg';
    const on = coreIds.has(r.id) ? 'on' : '';
    return `<li class="${on}" data-id="${r.id}"><span>${r.id}</span><span class="${cls}">${fmtRet(r.v)}</span></li>`;
  }).join('');
  ul.querySelectorAll('li').forEach(li => li.onclick = () => { document.getElementById('q').value = li.dataset.id; setExpandOptions([]); setFocus([li.dataset.id]); });
}
function renderLegendScale() {
  const el = document.getElementById('lgScale'); if (!el) return;
  el.textContent = `红涨 · 绿跌（${retWindow === 'cum20' ? '近20日' : '当日'}，颜色在 ±${RET_SCALE.toFixed(2)}% 封顶 = |涨跌| 的 ${Math.round(CLIP_Q * 100)}% 分位）`;
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
    setFocus([...next], { openDetail: false });
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
  if (qMode !== 'stock' || !q) { box.innerHTML = ''; return; }
  const hits = searchStocks(q);
  if (!hits.length) { box.innerHTML = `<div class="empty">未匹配到标的</div>`; return; }
  box.innerHTML = hits.map(h => `<div data-sector="${h.sector}"><b>${h.name}</b>${h.code} → ${h.sector}</div>`).join('');
  box.querySelectorAll('div[data-sector]').forEach(el => {
    el.onclick = () => {
      document.getElementById('q').value = el.dataset.sector;
      setExpandOptions([]);
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
    setExpandOptions([]); setFocus([hits[0].sector]); document.getElementById('q').value = hits[0].sector; return;
  }
  const all = expandQuery(q);
  if (!all.length) { setExpandOptions([]); renderPanel(null, []); document.getElementById('pBody').innerHTML = `<div class="empty">未匹配到二级板块，试试「电力」「半导体」「白酒」。</div>`; return; }
  const exact = exactMatches(q);
  const cores = exact.length ? exact : all;  // 无精确匹配（如“新能源”）时聚焦整组，仍可逐个取消
  expandOptions = exact.length ? all.filter(id => !exact.includes(id)) : all;
  setFocus(cores);
}

document.getElementById('go').onclick = runQuery;
document.getElementById('reset').onclick = () => {
  document.getElementById('q').value = ''; document.getElementById('hints').innerHTML = '';
  setExpandOptions([]); setFocus([]);
  controls.target.copy(HOME.target); camera.position.copy(HOME.pos); controls.update();
};
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
  if (!coreIds.size) rebuildLabels();
  if (coreIds.size === 1) renderPanel([...coreIds], [...focusIds].filter(id => !coreIds.has(id)));
};
{
  const leadBtn = document.querySelector('#edgemode button[data-v="lead"]');
  leadBtn.classList.add('exp');
  leadBtn.innerHTML = LEAD_SIG_COUNT === 0 ? '领先·实验<small>0 显著</small>' : '领先·实验';
  leadBtn.title = LEAD_SIG_COUNT === 0 ? '实验功能：当前没有领先边通过 BH-FDR 显著性检验' : '实验功能：领先—滞后关系，需结合 FDR 与样本外命中阅读';
}
document.getElementById('showFailed').onclick = () => {
  showFailed = !showFailed;
  const btn = document.getElementById('showFailed');
  btn.classList.toggle('active', showFailed);
  btn.textContent = showFailed ? '隐藏未通过FDR的边' : '显示未通过FDR的边';
  refreshFocus();
};
document.getElementById('showFailed').classList.toggle('active', showFailed);
document.getElementById('leadSum').textContent = leadSummaryText();

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
  const i = pickNode(ev.clientX, ev.clientY);
  if (i >= 0) { const id = DATA.nodes[i].id; document.getElementById('q').value = id; setExpandOptions([]); setFocus([id]); }
});
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
  if (b.dataset.act === 'home') { markInteracted(); resetView(); return; }
  const m = b.dataset.mode;
  if (dockMode === m) setDockMode('hidden'); else setDockMode(m);
};
document.getElementById('flyClose').onclick = () => setDockMode('hidden');
if (window.ResizeObserver) new ResizeObserver(() => applyViewOffset()).observe(document.getElementById('flyout'));

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
  controls.update();
  if (++frame % 6 === 0) declutterLabels();
  renderer.render(scene, camera);
})();
window.__scc = { camera, controls, pickNode, get coreIds() { return [...coreIds]; }, get mode() { return mode; }, three: THREE_SOURCE };
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
        payload = attach_stock_payload(payload)
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
    payload = pack_payload(piv, resid, meta, bench=bench)
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
