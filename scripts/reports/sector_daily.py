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


def daily_frame(bars: pd.DataFrame, mapping: pd.DataFrame, sector_ids: list[str], cfg: dict | None = None,
                n_sessions: int | None = None) -> pd.DataFrame:
    """最近 n_sessions 个交易日 × 板块的等权日收益（%）；bars: 代码/日期/收盘/前收；mapping: 代码/sector。"""
    cfg = {**DAILY_DEFAULTS, **(cfg or {})}
    if bars is None or bars.empty:
        return pd.DataFrame(columns=list(sector_ids), dtype=float)
    b = bars[["代码", "日期", "收盘", "前收"]].copy()
    b["日期"] = pd.to_datetime(b["日期"], errors="coerce")
    sessions = pd.Index(b["日期"].dropna().unique()).sort_values()
    if n_sessions:
        sessions = sessions[-int(n_sessions):]
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
    return daily.reindex(index=sessions, columns=list(sector_ids)).astype(float)


def _need(cfg: dict) -> int:
    return int(cfg["replay_days"]) + max(int(p) for p in cfg["periods"]) - 1


def pack_daily(daily: pd.DataFrame, sector_ids: list[str], cfg: dict | None = None) -> dict:
    """把日收益表的最后 need 天压成整数基点数组（嵌入 HTML）。"""
    cfg = {**DAILY_DEFAULTS, **(cfg or {})}
    periods = sorted({int(p) for p in cfg["periods"]})
    if daily is None or daily.empty:
        return {"dates": [], "replay_days": 0, "periods": periods, "ids": list(sector_ids), "bp": []}
    daily = daily.iloc[-_need(cfg):]
    bp = [[None if not np.isfinite(v) else int(round(v * 100)) for v in daily[s].to_numpy(dtype=float)] for s in sector_ids]
    return {
        "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in daily.index],
        "replay_days": min(int(cfg["replay_days"]), len(daily)),
        "periods": periods,
        "ids": list(sector_ids),
        "bp": bp,
    }


def sector_daily_returns(bars: pd.DataFrame, mapping: pd.DataFrame, sector_ids: list[str], cfg: dict | None = None) -> dict:
    """bars: 代码/日期/收盘/前收；mapping: 代码/sector。返回紧凑日收益结构。"""
    cfg = {**DAILY_DEFAULTS, **(cfg or {})}
    return pack_daily(daily_frame(bars, mapping, sector_ids, cfg, _need(cfg)), sector_ids, cfg)


def load_daily_bundle(kline_path: Path, info: pd.DataFrame, sw2_col: str, sector_ids: list[str],
                      cfg: dict | None = None, history_days: int = 0) -> tuple[dict, pd.DataFrame]:
    """返回 (嵌入用紧凑结构, 更长的日收益表)；长表供异动信号估计历史价差，不嵌入页面。"""
    cfg = {**DAILY_DEFAULTS, **(cfg or {})}
    n = max(_need(cfg), int(history_days or 0))
    dates = pd.to_datetime(pd.read_parquet(kline_path, columns=["日期"])["日期"], errors="coerce").dropna()
    sessions = pd.Index(dates.unique()).sort_values()
    if sessions.empty:
        empty = pd.DataFrame(columns=list(sector_ids), dtype=float)
        return pack_daily(empty, sector_ids, cfg), empty
    start = pd.Timestamp(sessions[-(n + 1):][0])  # 多取一天，供缺「前收」时回退上一收盘
    bars = pd.read_parquet(kline_path, columns=["代码", "日期", "收盘", "前收"], filters=[("日期", ">=", start)])
    mapping = info[["代码", sw2_col]].rename(columns={sw2_col: "sector"}).dropna()
    frame = daily_frame(bars, mapping, sector_ids, cfg, n)
    return pack_daily(frame, sector_ids, cfg), frame


def load_daily_payload(kline_path: Path, info: pd.DataFrame, sw2_col: str, sector_ids: list[str], cfg: dict | None = None) -> dict:
    return load_daily_bundle(kline_path, info, sw2_col, sector_ids, cfg)[0]
