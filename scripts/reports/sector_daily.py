"""近 N 个交易日的申万二级板块日收益（成分股等权），供点云的光晕 / 边着色 / 逐日回放使用。

输出紧凑结构（嵌入 HTML）：
    {"dates": [...], "replay_days": R, "periods": [1, 5, 20], "ids": [...], "bp": [[...], ...]}
- dates 覆盖 replay_days + max(periods) - 1 个交易日，使回放窗口第一天也能算 20 日收益；
- bp 为日收益 ×100 取整（基点），缺失为 null；前端按区间复利得到 1/5/20 日收益。
- 日收益 = 收盘/前收-1；缓存早期行缺「前收」时用该股上一交易日收盘代替。
- 单只股票日收益超过 max_abs_ret（%）视为异常（复权/新股），不计入均值。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DAILY_DEFAULTS: dict[str, Any] = {
    "replay_days": 20,
    "periods": [1, 5, 20],
    "max_abs_ret": 35.0,
    "min_members": 1,
}


def _bare(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c[len(p):]
    return c


def sector_daily_returns(bars: pd.DataFrame, mapping: pd.DataFrame, sector_ids: list[str], cfg: dict | None = None) -> dict:
    """bars: 代码/日期/收盘/前收；mapping: 代码/sector。返回紧凑日收益结构。"""
    cfg = {**DAILY_DEFAULTS, **(cfg or {})}
    periods = sorted({int(p) for p in cfg["periods"]})
    need = int(cfg["replay_days"]) + max(periods) - 1
    empty = {"dates": [], "replay_days": 0, "periods": periods, "ids": list(sector_ids), "bp": []}
    if bars is None or bars.empty:
        return empty
    b = bars[["代码", "日期", "收盘", "前收"]].copy()
    b["日期"] = pd.to_datetime(b["日期"], errors="coerce")
    sessions = pd.Index(b["日期"].dropna().unique()).sort_values()[-need:]
    b = b.sort_values(["代码", "日期"])
    close = pd.to_numeric(b["收盘"], errors="coerce")
    prev = pd.to_numeric(b["前收"], errors="coerce")
    prev = prev.where(prev > 0, close.groupby(b["代码"]).shift(1))
    keep = b["日期"].isin(sessions).to_numpy()
    b, close, prev = b[keep].copy(), close[keep], prev[keep]
    b["ret"] = np.where(prev > 0, (close / prev - 1.0) * 100.0, np.nan)
    b.loc[b["ret"].abs() > float(cfg["max_abs_ret"]), "ret"] = np.nan
    m = mapping[["代码", "sector"]].copy()
    m["bare"] = m["代码"].map(_bare)
    m = m.drop_duplicates("bare")
    b["bare"] = b["代码"].map(_bare)
    b = b.merge(m[["bare", "sector"]], on="bare", how="inner").dropna(subset=["ret"])
    g = b.groupby(["日期", "sector"])["ret"]
    daily = g.mean().where(g.count() >= int(cfg["min_members"])).unstack("sector")
    daily = daily.reindex(index=sessions, columns=list(sector_ids))
    bp = [[None if not np.isfinite(v) else int(round(v * 100)) for v in daily[s].to_numpy(dtype=float)] for s in sector_ids]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in sessions],
        "replay_days": min(int(cfg["replay_days"]), len(sessions)),
        "periods": periods,
        "ids": list(sector_ids),
        "bp": bp,
    }


def load_daily_payload(kline_path: Path, info: pd.DataFrame, sw2_col: str, sector_ids: list[str], cfg: dict | None = None) -> dict:
    cfg = {**DAILY_DEFAULTS, **(cfg or {})}
    need = int(cfg["replay_days"]) + max(int(p) for p in cfg["periods"]) - 1
    dates = pd.to_datetime(pd.read_parquet(kline_path, columns=["日期"])["日期"], errors="coerce").dropna()
    sessions = pd.Index(dates.unique()).sort_values()
    if sessions.empty:
        return sector_daily_returns(pd.DataFrame(), pd.DataFrame(columns=["代码", "sector"]), sector_ids, cfg)
    start = pd.Timestamp(sessions[-(need + 1):][0])  # 多取一天，供缺「前收」时回退上一收盘
    bars = pd.read_parquet(kline_path, columns=["代码", "日期", "收盘", "前收"], filters=[("日期", ">=", start)])
    mapping = info[["代码", sw2_col]].rename(columns={sw2_col: "sector"}).dropna()
    return sector_daily_returns(bars, mapping, sector_ids, cfg)
