"""宽基指数开盘缺口事件研究。

信号日开盘相对前收高/低开达到阈值后，以信号日开盘作为可成交起点，
统计到第 ``horizon`` 个交易日收盘的路径。模块只读取传入日线，不负责更新数据，
供网页 API、脚本和测试共同复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("日期", "开盘", "最高", "最低", "收盘")


@dataclass(frozen=True)
class MarketGapConfig:
    threshold_pct: float = 0.7
    horizon: int = 3
    start_date: str = "2020-01-01"
    end_date: str | None = None

    def validate(self) -> None:
        if not 0.1 <= float(self.threshold_pct) <= 10:
            raise ValueError("缺口阈值须在 0.1%～10% 之间")
        if not 1 <= int(self.horizon) <= 20:
            raise ValueError("持有窗口须为 1～20 个交易日")
        start = pd.Timestamp(self.start_date)
        if self.end_date and pd.Timestamp(self.end_date) < start:
            raise ValueError("结束日期不能早于起始日期")


def _safe_float(value, digits: int = 4):
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def _summary(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "samples": 0, "avg_gap_pct": None, "mean_return_pct": None,
            "median_return_pct": None, "win_rate_pct": None,
            "mfe_pct": None, "mae_pct": None, "gap_fill_rate_pct": None,
            "close_vs_prev_pct": None, "close_recovery_rate_pct": None,
        }
    ret = frame["return_pct"]
    return {
        "samples": int(len(frame)),
        "avg_gap_pct": _safe_float(frame["gap_pct"].mean()),
        "mean_return_pct": _safe_float(ret.mean()),
        "median_return_pct": _safe_float(ret.median()),
        "win_rate_pct": _safe_float((ret > 0).mean() * 100),
        "mfe_pct": _safe_float(frame["mfe_pct"].mean()),
        "mae_pct": _safe_float(frame["mae_pct"].mean()),
        "gap_fill_rate_pct": _safe_float(frame["gap_filled"].mean() * 100),
        "close_vs_prev_pct": _safe_float(frame["close_vs_prev_pct"].mean()),
        "close_recovery_rate_pct": _safe_float(frame["close_recovered"].mean() * 100),
    }


def _non_overlapping(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    keep: list[int] = []
    next_allowed = -1
    for idx, row in frame.sort_values("position").iterrows():
        position = int(row["position"])
        if position >= next_allowed:
            keep.append(idx)
            next_allowed = position + horizon
    return frame.loc[keep].copy()


def _severity_rows(events: pd.DataFrame, threshold: float) -> list[dict]:
    # 默认 0.7% 对应用户已核验的 0.7～1.0、1.0～1.5、>=1.5 三档。
    first = max(1.0, round(threshold + 0.3, 2))
    second = max(1.5, round(threshold + 0.8, 2))
    bins = [(threshold, first), (first, second), (second, np.inf)]
    rows = []
    for direction in ("low", "high"):
        scoped = events[events["direction"] == direction]
        magnitude = scoped["gap_pct"].abs()
        for lower, upper in bins:
            selected = scoped[(magnitude >= lower) & (magnitude < upper)]
            label = f"{lower:g}～{upper:g}%" if np.isfinite(upper) else f"≥{lower:g}%"
            stat = _summary(selected)
            rows.append({"direction": direction, "bucket": label, **stat})
    return rows


def _yearly_rows(events: pd.DataFrame) -> list[dict]:
    rows = []
    if events.empty:
        return rows
    for (year, direction), frame in events.groupby(["year", "direction"], sort=True):
        rows.append({"year": int(year), "direction": direction, **_summary(frame)})
    return rows


def _event_rows(events: pd.DataFrame) -> list[dict]:
    columns = [
        "date", "direction", "gap_pct", "open", "prev_close", "target_date",
        "target_close", "return_pct", "mfe_pct", "mae_pct", "gap_filled",
        "close_vs_prev_pct", "close_recovered", "source",
    ]
    rows = []
    for record in events.sort_values("date", ascending=False)[columns].to_dict("records"):
        record["date"] = pd.Timestamp(record["date"]).strftime("%Y-%m-%d")
        record["target_date"] = pd.Timestamp(record["target_date"]).strftime("%Y-%m-%d")
        for key, value in list(record.items()):
            if isinstance(value, (float, np.floating)):
                record[key] = _safe_float(value)
            elif isinstance(value, (bool, np.bool_)):
                record[key] = bool(value)
        rows.append(record)
    return rows


def run_market_gap_study(daily: pd.DataFrame, config: MarketGapConfig) -> dict:
    """计算单指数高低开事件及路径统计，返回适合 JSON 序列化的数据。"""
    config.validate()
    missing = [column for column in REQUIRED_COLUMNS if column not in daily.columns]
    if missing:
        raise ValueError("指数日线缺少字段：" + "、".join(missing))

    frame = daily.copy()
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
    for column in REQUIRED_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(REQUIRED_COLUMNS)).sort_values("日期")
    frame = frame.drop_duplicates("日期", keep="last").reset_index(drop=True)
    frame = frame[frame["日期"] >= pd.Timestamp(config.start_date)].reset_index(drop=True)
    if config.end_date:
        frame = frame[frame["日期"] <= pd.Timestamp(config.end_date)].reset_index(drop=True)
    if len(frame) <= config.horizon:
        raise LookupError("指定区间内没有足够的指数日线")

    prev_close = frame["收盘"].shift(1)
    gap_pct = (frame["开盘"] / prev_close - 1) * 100
    last_start = len(frame) - config.horizon
    events = []
    for position in range(1, last_start + 1):
        gap = float(gap_pct.iloc[position])
        if gap <= -config.threshold_pct:
            direction = "low"
        elif gap >= config.threshold_pct:
            direction = "high"
        else:
            continue
        window = frame.iloc[position:position + config.horizon]
        entry = float(frame.at[position, "开盘"])
        previous = float(prev_close.iloc[position])
        target = window.iloc[-1]
        if direction == "low":
            filled = bool((window["最高"] >= previous).any())
            recovered = float(target["收盘"]) >= previous
        else:
            filled = bool((window["最低"] <= previous).any())
            recovered = float(target["收盘"]) >= previous
        path = [
            (float(frame.at[position + step, "收盘"]) / entry - 1) * 100
            for step in range(config.horizon)
        ]
        events.append({
            "position": position,
            "date": frame.at[position, "日期"],
            "year": int(frame.at[position, "日期"].year),
            "direction": direction,
            "gap_pct": gap,
            "open": entry,
            "prev_close": previous,
            "target_date": target["日期"],
            "target_close": float(target["收盘"]),
            "return_pct": path[-1],
            "path_pct": path,
            "mfe_pct": (float(window["最高"].max()) / entry - 1) * 100,
            "mae_pct": (float(window["最低"].min()) / entry - 1) * 100,
            "gap_filled": filled,
            "close_vs_prev_pct": (float(target["收盘"]) / previous - 1) * 100,
            "close_recovered": bool(recovered),
            "source": str(frame.at[position, "来源"]) if "来源" in frame.columns else "local",
        })

    event_frame = pd.DataFrame(events)
    if event_frame.empty:
        event_frame = pd.DataFrame(columns=[
            "position", "date", "year", "direction", "gap_pct", "open", "prev_close",
            "target_date", "target_close", "return_pct", "path_pct", "mfe_pct", "mae_pct",
            "gap_filled", "close_vs_prev_pct", "close_recovered", "source",
        ])

    eligible = frame.iloc[1:last_start + 1].copy()
    entry = eligible["开盘"].to_numpy(dtype=float)
    exits = frame["收盘"].shift(-(config.horizon - 1)).iloc[1:last_start + 1].to_numpy(dtype=float)
    baseline_returns = (exits / entry - 1) * 100
    baseline = {
        "samples": int(len(baseline_returns)),
        "mean_return_pct": _safe_float(np.mean(baseline_returns)),
        "median_return_pct": _safe_float(np.median(baseline_returns)),
        "win_rate_pct": _safe_float(np.mean(baseline_returns > 0) * 100),
    }

    paths = []
    for direction in ("low", "high"):
        selected = event_frame[event_frame["direction"] == direction]
        for step in range(config.horizon):
            values = [row[step] for row in selected["path_pct"]] if len(selected) else []
            paths.append({
                "direction": direction,
                "day": step + 1,
                "label": "T0" if step == 0 else f"T+{step}",
                "mean_return_pct": _safe_float(np.mean(values)) if values else None,
                "median_return_pct": _safe_float(np.median(values)) if values else None,
            })

    quality_sources = (
        frame["来源"].fillna("unknown").astype(str).value_counts().to_dict()
        if "来源" in frame.columns else {"local": int(len(frame))}
    )
    return {
        "config": {
            "threshold_pct": float(config.threshold_pct),
            "horizon": int(config.horizon),
            "start_date": config.start_date,
            "end_date": config.end_date,
        },
        "range": {
            "start": frame["日期"].min().strftime("%Y-%m-%d"),
            "end": frame["日期"].max().strftime("%Y-%m-%d"),
            "rows": int(len(frame)),
        },
        "summary": {
            "low": _summary(event_frame[event_frame["direction"] == "low"]),
            "high": _summary(event_frame[event_frame["direction"] == "high"]),
            "baseline": baseline,
        },
        "non_overlapping": {
            direction: _summary(_non_overlapping(
                event_frame[event_frame["direction"] == direction], config.horizon
            )) for direction in ("low", "high")
        },
        "paths": paths,
        "severity": _severity_rows(event_frame, config.threshold_pct),
        "yearly": _yearly_rows(event_frame),
        "events": _event_rows(event_frame),
        "quality": {
            "duplicate_dates": int(daily.duplicated("日期").sum()) if "日期" in daily else 0,
            "missing_ohlc": int(daily[list(REQUIRED_COLUMNS[1:])].isna().any(axis=1).sum()),
            "sources": {str(k): int(v) for k, v in quality_sources.items()},
        },
        "methodology": {
            "entry": "信号日开盘价",
            "exit": f"第 {config.horizon} 个交易日收盘价（信号日计第 1 日）",
            "gap": "(信号日开盘 / 前一交易日收盘 - 1) × 100%",
            "costs": "事件研究未计手续费、滑点和冲击成本",
        },
    }
