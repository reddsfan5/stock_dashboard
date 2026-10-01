"""申万二级板块风格标签：进攻 / 防御 / 周期 / 成长 / 价值（一个主标签 + 可选副标签）。

规则（参数见 config style:）：
1. 风险维度：近 lookback_days 个交易日的 beta（对沪深300）、年化波动率、最大回撤，分别取横截面分位，
   按 risk_weights 加权得到风险分 risk∈[0,1]。risk ≥ risk_hi → 进攻；risk ≤ risk_lo → 防御。
2. 属性维度：一级行业属于 cyclical_l1（或二级在 cyclical_sw2）→ 周期；否则成分股动态市盈率中位数的
   横截面分位 ≥ growth_pe_pct，或亏损股占比 ≥ loss_share_growth → 成长；其余 → 价值。
3. 主标签：风险维度命中则取进攻/防御，副标签为属性维度；否则主标签为属性维度，副标签在 risk ≥ 0.6 / ≤ 0.4
   时取进攻/防御。
4. 人工覆盖：overrides（二级）优先于 l1_overrides（一级）；被覆盖时，规则算出的主标签（若不同）降为副标签。
没有 10 年期国债收益率数据，未使用利率敏感度。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

STYLES = ["进攻", "防御", "周期", "成长", "价值"]
STYLE_DEFAULTS: dict[str, Any] = {
    "lookback_days": 500,
    "min_obs": 250,
    "colors": {"进攻": "#ff9f43", "防御": "#5ad1ff", "周期": "#c49a6c", "成长": "#c792ea", "价值": "#f2cc60"},
    "risk_weights": {"beta": 0.5, "vol": 0.3, "mdd": 0.2},
    "risk_hi": 0.70,
    "risk_lo": 0.30,
    "cyclical_l1": ["有色金属", "钢铁", "煤炭", "基础化工", "石油石化", "建筑材料"],
    "cyclical_sw2": [],
    "growth_pe_pct": 0.60,
    "loss_share_growth": 0.40,
    "l1_overrides": {},
    "overrides": {},
}


def _pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True, method="average")


def _max_drawdown(r: np.ndarray) -> float:
    r = r[np.isfinite(r)]
    if r.size == 0:
        return np.nan
    nav = np.cumprod(1 + r / 100.0)
    peak = np.maximum.accumulate(nav)
    return float(np.max(1 - nav / peak))


def risk_metrics(daily: pd.DataFrame, bench: pd.Series, cfg: dict) -> pd.DataFrame:
    """daily: 日期 × 板块 日收益（%）；bench: 日期 → 基准日收益（%）。"""
    d = daily.iloc[-int(cfg["lookback_days"]):]
    b = bench.reindex(d.index).astype(float)
    rows = {}
    for s in d.columns:
        r = d[s].to_numpy(dtype=float)
        ok = np.isfinite(r) & np.isfinite(b.to_numpy())
        if ok.sum() < int(cfg["min_obs"]):
            continue
        rb = b.to_numpy()[ok]
        beta = float(np.cov(r[ok], rb, ddof=1)[0, 1] / np.var(rb, ddof=1))
        rows[s] = {"beta": beta, "vol": float(np.nanstd(r, ddof=1) * np.sqrt(250)), "mdd": _max_drawdown(r)}
    m = pd.DataFrame.from_dict(rows, orient="index")
    if m.empty:
        return m
    w = cfg["risk_weights"]
    m["risk"] = (w["beta"] * _pct_rank(m["beta"]) + w["vol"] * _pct_rank(m["vol"]) + w["mdd"] * _pct_rank(m["mdd"])) / sum(w.values())
    return m


def compute_style_tags(daily: pd.DataFrame, bench: pd.Series, l1_of: dict[str, str],
                       pe: pd.DataFrame | None = None, cfg: dict | None = None) -> dict:
    """返回 {styles, colors, tags: {sector: {p, s, src, beta, vol, mdd, risk, pe, loss}}, counts, note}。
    pe: index=板块，列 pe（动态市盈率中位数，正值）与 loss（亏损股占比），可为空。"""
    cfg = {**STYLE_DEFAULTS, **(cfg or {})}
    m = risk_metrics(daily, bench, cfg)
    pe = pe if pe is not None else pd.DataFrame(columns=["pe", "loss"])
    pe_pct = _pct_rank(pe["pe"].dropna()) if len(pe) else pd.Series(dtype=float)
    cyc_l1, cyc_sw2 = set(cfg["cyclical_l1"]), set(cfg["cyclical_sw2"])
    tags = {}
    for s in daily.columns:
        l1 = l1_of.get(s, "")
        risk = float(m.loc[s, "risk"]) if s in m.index else np.nan
        loss = float(pe.loc[s, "loss"]) if s in pe.index and pd.notna(pe.loc[s, "loss"]) else np.nan
        pp = float(pe_pct.get(s, np.nan))
        if l1 in cyc_l1 or s in cyc_sw2:
            nature = "周期"
        elif (np.isfinite(pp) and pp >= cfg["growth_pe_pct"]) or (np.isfinite(loss) and loss >= cfg["loss_share_growth"]):
            nature = "成长"
        else:
            nature = "价值"
        if np.isfinite(risk) and risk >= cfg["risk_hi"]:
            p, sec = "进攻", nature
        elif np.isfinite(risk) and risk <= cfg["risk_lo"]:
            p, sec = "防御", nature
        else:
            p = nature
            sec = "进攻" if np.isfinite(risk) and risk >= 0.6 else ("防御" if np.isfinite(risk) and risk <= 0.4 else None)
        src = "rule"
        ov = cfg["overrides"].get(s) or cfg["l1_overrides"].get(l1)
        if ov:
            src = "override" if s in cfg["overrides"] else "l1_override"
            sec = p if p != ov else (sec if sec != ov else None)
            p = ov
        rnd = lambda x, k=3: None if x is None or not np.isfinite(x) else round(float(x), k)  # noqa: E731
        tags[s] = {"p": p, "s": sec, "src": src,
                   "beta": rnd(m.loc[s, "beta"]) if s in m.index else None,
                   "vol": rnd(m.loc[s, "vol"], 1) if s in m.index else None,
                   "mdd": rnd(m.loc[s, "mdd"]) if s in m.index else None,
                   "risk": rnd(risk), "pe": rnd(float(pe.loc[s, "pe"]), 1) if s in pe.index and pd.notna(pe.loc[s, "pe"]) else None,
                   "loss": rnd(loss, 2)}
    counts = {k: sum(1 for t in tags.values() if t["p"] == k) for k in STYLES}
    return {"styles": STYLES, "colors": cfg["colors"], "tags": tags, "counts": counts,
            "lookback": int(min(len(daily), int(cfg["lookback_days"]))),
            "note": "风险分 = beta/波动/回撤分位加权；属性 = 一级行业周期名单 + 市盈率分位；未使用利率敏感度（无 10 年国债收益率数据）"}


def style_spread(daily: pd.DataFrame, tags: dict, a: str = "进攻", b: str = "防御") -> pd.Series:
    """逐日 主标签 a 的等权均值 − b 的等权均值（%）。"""
    ca = [s for s, t in tags.items() if t["p"] == a and s in daily.columns]
    cb = [s for s, t in tags.items() if t["p"] == b and s in daily.columns]
    return daily[ca].mean(axis=1) - daily[cb].mean(axis=1)


def sector_pe(basic: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """basic: 代码/日期/市盈率_动态（取最新日期）；mapping: 代码(bare)/sector。"""
    if basic is None or basic.empty:
        return pd.DataFrame(columns=["pe", "loss"])
    b = basic.copy()
    b["日期"] = pd.to_datetime(b["日期"])
    b = b[b["日期"] == b["日期"].max()]
    b = b.merge(mapping, on="bare", how="inner")
    pe = pd.to_numeric(b["市盈率_动态"], errors="coerce")
    b["pos"] = pe.where(pe > 0)
    b["loss"] = (pe <= 0).where(pe.notna())
    g = b.groupby("sector")
    return pd.DataFrame({"pe": g["pos"].median(), "loss": g["loss"].mean()})


def load_style(daily: pd.DataFrame, l1_of: dict[str, str], info: pd.DataFrame, sw2_col: str,
               index_path, basic_path, bench_code: str = "sh000300", cfg: dict | None = None) -> dict:
    """从缓存读取基准与市盈率后打标签；基准缺失时 beta 退回板块等权均值。"""
    from scripts.reports.sector_daily import _bare

    bench = None
    try:
        ix = pd.read_parquet(index_path, columns=["代码", "日期", "收盘"])
        ix = ix[ix["代码"] == bench_code].copy()
        ix["日期"] = pd.to_datetime(ix["日期"])
        ix = ix.sort_values("日期").drop_duplicates("日期", keep="last").set_index("日期")
        bench = ix["收盘"].astype(float).pct_change(fill_method=None) * 100
    except Exception:
        bench = None
    bench_name = bench_code
    if bench is None or bench.reindex(daily.index).notna().mean() < 0.9:
        bench, bench_name = daily.mean(axis=1), "板块等权"
    pe = None
    try:
        basic = pd.read_parquet(basic_path, columns=["代码", "日期", "市盈率_动态"])
        basic["bare"] = basic["代码"].map(_bare)
        mp = info[["代码", sw2_col]].rename(columns={sw2_col: "sector"}).dropna()
        mp["bare"] = mp["代码"].map(_bare)
        pe = sector_pe(basic, mp[["bare", "sector"]].drop_duplicates("bare"))
    except Exception:
        pe = None
    out = compute_style_tags(daily, bench, l1_of, pe, cfg)
    out["bench"] = bench_name
    out["pe_available"] = pe is not None and len(pe) > 0
    return out
