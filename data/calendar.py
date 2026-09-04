"""A 股交易日历助手。

优先使用本地缓存（由新浪公开交易日接口刷新）；失败时退回工作日近似，
并保留已知半日市列表供训练/回测判断。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

import pandas as pd

from data.storage import atomic_write_parquet

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = PROJECT_DIR / "cache" / "trading_calendar.parquet"

DateLike = Union[str, date, datetime, pd.Timestamp]

# 近年常见半日市（提前收盘到 11:30）。覆盖不全时不影响全日市判断。
KNOWN_HALF_DAYS = {
    "2024-02-09",  # 春节前
    "2024-04-04",  # 清明前
    "2024-04-30",  # 劳动节前
    "2024-09-30",  # 国庆前
    "2024-12-31",
    "2025-01-27",
    "2025-04-03",
    "2025-04-30",
    "2025-09-30",
    "2025-12-31",
    "2026-01-29",
    "2026-02-13",
    "2026-04-03",
    "2026-04-30",
    "2026-09-30",
    "2026-12-31",
}

HALF_DAY_CLOSE = "11:30"
FULL_DAY_CLOSE = "15:00"


def _to_date(value: DateLike) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return pd.Timestamp(value).date()


def _to_text(value: DateLike) -> str:
    return _to_date(value).isoformat()


class TradingCalendar:
    """交易日查询；缓存为空时用周一到周五近似（不含节假日）。"""

    def __init__(self, path=CACHE_FILE):
        self.path = Path(path)
        self._dates: Optional[set[str]] = None
        self._ordered: Optional[list[str]] = None

    def _ensure_loaded(self) -> None:
        if self._dates is not None:
            return
        if self.path.exists():
            frame = pd.read_parquet(self.path, columns=["日期"])
            ordered = sorted({
                pd.Timestamp(value).strftime("%Y-%m-%d")
                for value in frame["日期"].tolist()
            })
            self._ordered = ordered
            self._dates = set(ordered)
            return
        self._ordered = []
        self._dates = set()

    @property
    def dates(self) -> list[str]:
        self._ensure_loaded()
        return list(self._ordered or [])

    def is_trading_day(self, value: DateLike) -> bool:
        text = _to_text(value)
        self._ensure_loaded()
        if self._dates:
            return text in self._dates
        day = _to_date(value)
        return day.weekday() < 5

    def is_half_day(self, value: DateLike) -> bool:
        return _to_text(value) in KNOWN_HALF_DAYS

    def session_close(self, value: DateLike) -> str:
        return HALF_DAY_CLOSE if self.is_half_day(value) else FULL_DAY_CLOSE

    def next_trading_day(self, value: DateLike, n: int = 1) -> Optional[str]:
        if n < 1:
            raise ValueError("n 必须是正整数")
        text = _to_text(value)
        self._ensure_loaded()
        if self._ordered:
            later = [item for item in self._ordered if item > text]
            return later[n - 1] if len(later) >= n else None
        day = _to_date(value)
        remaining = n
        while remaining > 0:
            day += timedelta(days=1)
            if day.weekday() < 5:
                remaining -= 1
        return day.isoformat()

    def prev_trading_day(self, value: DateLike, n: int = 1) -> Optional[str]:
        if n < 1:
            raise ValueError("n 必须是正整数")
        text = _to_text(value)
        self._ensure_loaded()
        if self._ordered:
            earlier = [item for item in self._ordered if item < text]
            return earlier[-n] if len(earlier) >= n else None
        day = _to_date(value)
        remaining = n
        while remaining > 0:
            day -= timedelta(days=1)
            if day.weekday() < 5:
                remaining -= 1
        return day.isoformat()

    def range(self, start: DateLike, end: DateLike) -> list[str]:
        start_text, end_text = _to_text(start), _to_text(end)
        self._ensure_loaded()
        if self._ordered:
            return [item for item in self._ordered if start_text <= item <= end_text]
        days = []
        day = _to_date(start)
        last = _to_date(end)
        while day <= last:
            if day.weekday() < 5:
                days.append(day.isoformat())
            day += timedelta(days=1)
        return days

    def update(self, *, progress: bool = False) -> dict:
        """从新浪交易日接口刷新缓存；失败时保留旧缓存并返回空新增。"""
        del progress  # 接口很快，保留参数以匹配其他 data 模块签名
        try:
            import akshare as ak
            frame = ak.tool_trade_date_hist_sina()
        except Exception as exc:  # noqa: BLE001 — 网络/源失败时优雅降级
            logger.warning("交易日历刷新失败: %s", exc)
            self._ensure_loaded()
            return {
                "ok": False,
                "message": f"交易日历刷新失败: {exc}",
                "rows": len(self._ordered or []),
                "new_rows": 0,
            }
        if frame is None or len(frame) == 0:
            return {"ok": False, "message": "交易日历接口返回空", "rows": 0, "new_rows": 0}
        column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
        cleaned = pd.DataFrame({
            "日期": pd.to_datetime(frame[column], errors="coerce"),
        }).dropna()
        cleaned["日期"] = cleaned["日期"].dt.normalize()
        cleaned = cleaned.drop_duplicates("日期").sort_values("日期")
        cleaned["半日市"] = cleaned["日期"].dt.strftime("%Y-%m-%d").isin(KNOWN_HALF_DAYS)
        previous = len(self.dates)
        atomic_write_parquet(cleaned, self.path)
        self._dates = None
        self._ordered = None
        self._ensure_loaded()
        return {
            "ok": True,
            "message": f"交易日历已更新至 {self.dates[-1] if self.dates else '—'}",
            "rows": len(self.dates),
            "new_rows": max(0, len(self.dates) - previous),
        }


def is_trading_day(value: DateLike, calendar: Optional[TradingCalendar] = None) -> bool:
    return (calendar or TradingCalendar()).is_trading_day(value)


def is_half_day(value: DateLike) -> bool:
    return TradingCalendar().is_half_day(value)


def trading_days(start: DateLike, end: DateLike, calendar: Optional[TradingCalendar] = None) -> list[str]:
    return (calendar or TradingCalendar()).range(start, end)
