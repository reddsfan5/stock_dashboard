"""观察池按加入批次的后续收益监控。

本模块只读本地日 K/指数缓存，并把结果写入 ``state/watchlist.sqlite3``。
批次日期严格使用观察池条目的 ``created_at``（上海时区），筛出日仅作为
辅助信息展示。计算口径与每日精选监控一致：加入日后的第一个交易日开盘入场，
窗口内最高价为理论峰值，窗口末收盘为可复核结果。
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

import pandas as pd

from data.etf import CACHE_FILE as ETF_CACHE_FILE
from data.forward_returns import calculate_forward_window
from data.index import CACHE_FILE as INDEX_CACHE_FILE
from data.kline import CACHE_FILE as STOCK_CACHE_FILE
from data.shortlist_monitor import (
    BENCHMARK_CODE,
    WINDOWS,
    _normal_code,
    _parquet_latest,
    _read_bars,
    _text_date,
)
from data.storage import atomic_write_json
from data.watchlist import DEFAULT_DB_PATH, STATUSES, STATUS_LABELS, WatchlistRepository


PROJECT_DIR = Path(__file__).resolve().parents[1]
MONITOR_CACHE = PROJECT_DIR / "cache" / "watchlist_monitor.json"
SOURCE_VERSION = "watchlist-bars-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now() -> str:
    return datetime.now(tz=SHANGHAI).isoformat(timespec="seconds")


def _finite(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _iso_date(value, field="日期") -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def _joined_date(created_at) -> str:
    """把 created_at 转换为上海本地批次日期。"""
    text = str(created_at or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI)
        return parsed.astimezone(SHANGHAI).date().isoformat()
    except (TypeError, ValueError):
        return _iso_date(text[:10], "加入时间")


def _values(rows: list[dict], key: str) -> list[float]:
    return [float(row[key]) for row in rows if _finite(row.get(key)) is not None]


def _summary(rows: list[dict]) -> dict:
    peak, close, adverse = (_values(rows, key) for key in ("peak_return_pct", "close_return_pct", "adverse_return_pct"))
    bench_close = _values(rows, "benchmark_close_return_pct")
    excess = _values(rows, "close_excess_pct")
    statuses = {status: sum(1 for row in rows if (row.get("outcome_status") or row.get("result_status")) == status) for status in ("complete", "partial", "pending", "unavailable")}
    return {
        "count": len(rows),
        "completed": statuses["complete"],
        "partial": statuses["partial"],
        "pending": statuses["pending"],
        "unavailable": statuses["unavailable"],
        "completion_pct": round(statuses["complete"] / len(rows) * 100, 2) if rows else 0.0,
        "peak_mean_pct": round(mean(peak), 4) if peak else None,
        "peak_median_pct": round(median(peak), 4) if peak else None,
        "peak_hit_5pct_rate_pct": round(sum(value >= 5 for value in peak) / len(peak) * 100, 2) if peak else None,
        "close_mean_pct": round(mean(close), 4) if close else None,
        "close_median_pct": round(median(close), 4) if close else None,
        "close_positive_rate_pct": round(sum(value > 0 for value in close) / len(close) * 100, 2) if close else None,
        "adverse_median_pct": round(median(adverse), 4) if adverse else None,
        "benchmark_close_median_pct": round(median(bench_close), 4) if bench_close else None,
        "close_excess_median_pct": round(median(excess), 4) if excess else None,
    }


class WatchlistMonitorRepository:
    """观察池收益结果表的幂等写入与分页查询。"""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlist_outcome (
                    user_id INTEGER NOT NULL,
                    item_id INTEGER NOT NULL,
                    joined_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'watching',
                    source_module TEXT NOT NULL DEFAULT '',
                    screen_date TEXT,
                    horizon_days INTEGER NOT NULL,
                    outcome_status TEXT NOT NULL,
                    entry_date TEXT, entry_open REAL,
                    peak_high REAL, peak_high_date TEXT, peak_return_pct REAL,
                    close_value REAL, close_date TEXT, close_return_pct REAL,
                    low_value REAL, low_date TEXT, adverse_return_pct REAL,
                    benchmark_entry_open REAL, benchmark_peak_high REAL,
                    benchmark_peak_return_pct REAL, benchmark_close_value REAL,
                    benchmark_close_return_pct REAL,
                    peak_excess_pct REAL, close_excess_pct REAL,
                    available_through TEXT, computed_at TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    PRIMARY KEY(user_id, item_id, horizon_days)
                );
                CREATE INDEX IF NOT EXISTS idx_watchlist_outcome_batch
                    ON watchlist_outcome(user_id, joined_date, horizon_days);
                CREATE INDEX IF NOT EXISTS idx_watchlist_outcome_filter
                    ON watchlist_outcome(user_id, status, source_module, horizon_days);
                CREATE TABLE IF NOT EXISTS watchlist_monitor_meta (
                    user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(user_id, key)
                );
                """
            )
            connection.commit()

    def replace_items(self, user_id: int, item_ids: Iterable[int], rows: Iterable[dict]):
        item_ids = list(dict.fromkeys(int(value) for value in item_ids))
        rows = list(rows)
        columns = [
            "user_id", "item_id", "joined_date", "code", "name", "status", "source_module", "screen_date",
            "horizon_days", "outcome_status", "entry_date", "entry_open", "peak_high", "peak_high_date",
            "peak_return_pct", "close_value", "close_date", "close_return_pct", "low_value", "low_date",
            "adverse_return_pct", "benchmark_entry_open", "benchmark_peak_high", "benchmark_peak_return_pct",
            "benchmark_close_value", "benchmark_close_return_pct", "peak_excess_pct", "close_excess_pct",
            "available_through", "computed_at", "source_version",
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                connection.execute(
                    f"DELETE FROM watchlist_outcome WHERE user_id=? AND item_id IN ({placeholders})",
                    (user_id, *item_ids),
                )
            if rows:
                placeholders = ",".join("?" for _ in columns)
                connection.executemany(
                    f"INSERT INTO watchlist_outcome ({','.join(columns)}) VALUES ({placeholders})",
                    [tuple(row.get(column) for column in columns) for row in rows],
                )
            self._set_meta(connection, user_id, "last_refresh", _now())
            self._set_meta(connection, user_id, "last_error", "")
            connection.commit()

    def record_failure(self, user_id: int, error: str):
        with self._connect() as connection:
            self._set_meta(connection, user_id, "last_error", str(error or "未知错误")[:1000])
            connection.commit()

    def set_resolved_as_of(self, user_id: int, value: str | None):
        with self._connect() as connection:
            self._set_meta(connection, user_id, "resolved_as_of", value or "")
            connection.commit()

    @staticmethod
    def _set_meta(connection, user_id, key, value):
        connection.execute(
            "INSERT INTO watchlist_monitor_meta(user_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (int(user_id), key, str(value)),
        )

    def meta(self, user_id: int) -> dict:
        with self._connect() as connection:
            rows = connection.execute("SELECT key,value FROM watchlist_monitor_meta WHERE user_id=?", (user_id,)).fetchall()
        return {row["key"]: row["value"] for row in rows}

    def query(self, user_id: int, *, date_from=None, date_to=None, batch=None, horizon=5,
              status=None, source=None, sort="joined_date", offset=0, limit=100) -> dict:
        horizon = int(horizon)
        if horizon not in WINDOWS:
            raise ValueError("horizon 必须是 1,3,5,10,20 之一")
        if sort not in {"joined_date", "peak_return_pct", "close_return_pct"}:
            raise ValueError("sort 不合法")
        date_from = _iso_date(date_from, "from") if date_from else None
        date_to = _iso_date(date_to, "to") if date_to else None
        if date_from and date_to and date_from > date_to:
            raise ValueError("from 不能晚于 to")
        if batch:
            batch = _iso_date(batch, "batch")
            date_from = date_to = batch
        if status and status not in STATUSES:
            raise ValueError("status 不合法")
        offset, limit = max(0, int(offset)), min(500, max(1, int(limit)))
        clauses = ["o.user_id=?", "o.horizon_days=?", "o.item_id=i.id", "i.deleted_at IS NULL"]
        args: list = [user_id, horizon]
        if date_from: clauses.append("o.joined_date>=?"); args.append(date_from)
        if date_to: clauses.append("o.joined_date<=?"); args.append(date_to)
        if status: clauses.append("i.status=?"); args.append(status)
        if source: clauses.append("o.source_module=?"); args.append(str(source).strip())
        where = " AND ".join(clauses)
        order = {
            "joined_date": "o.joined_date DESC, o.item_id DESC",
            "peak_return_pct": "o.peak_return_pct IS NULL, o.peak_return_pct DESC, o.joined_date DESC",
            "close_return_pct": "o.close_return_pct IS NULL, o.close_return_pct DESC, o.joined_date DESC",
        }[sort]
        select = f"SELECT o.*, i.status AS current_status, i.source_module AS current_source FROM watchlist_outcome o JOIN watch_item i ON i.id=o.item_id WHERE {where} ORDER BY {order}"
        with self._connect() as connection:
            all_rows = [dict(row) for row in connection.execute(select, args).fetchall()]
            batch_rows = connection.execute(
                "SELECT joined_date, COUNT(*) count FROM watchlist_outcome o JOIN watch_item i ON i.id=o.item_id "
                "WHERE o.user_id=? AND o.horizon_days=? AND i.deleted_at IS NULL GROUP BY joined_date ORDER BY joined_date DESC",
                (user_id, horizon),
            ).fetchall()
            sources = connection.execute(
                "SELECT DISTINCT o.source_module FROM watchlist_outcome o JOIN watch_item i ON i.id=o.item_id "
                "WHERE o.user_id=? AND o.horizon_days=? AND i.deleted_at IS NULL AND o.source_module!='' ORDER BY o.source_module",
                (user_id, horizon),
            ).fetchall()
        for row in all_rows:
            row["result_status"] = row.get("outcome_status")
            if row.get("current_status"):
                row["watch_status"] = row["current_status"]
            if row.get("current_source") is not None:
                row["source_module"] = row["current_source"]
            row["current_status"] = row.get("watch_status") or row.get("status")
            row["watch_status"] = row["current_status"]
            row["status"] = row.get("outcome_status")
            row["status_label"] = {"complete": "已完成", "partial": "观察中", "pending": "等待入场", "unavailable": "无数据"}.get(row.get("status"), row.get("status"))
            row["watch_status_label"] = STATUS_LABELS.get(row.get("watch_status"), row.get("watch_status"))
            row["result_status_label"] = {"complete": "已完成", "partial": "观察中", "pending": "等待入场", "unavailable": "无数据"}.get(row.get("result_status"), row.get("result_status"))
        cohorts = []
        grouped = {}
        for row in all_rows:
            grouped.setdefault(row["joined_date"], []).append(row)
        for joined in sorted(grouped, reverse=True):
            screen_dates = sorted({str(row.get("screen_date")) for row in grouped[joined] if row.get("screen_date")})
            cohorts.append({"joined_date": joined, "screen_date": screen_dates[0] if len(screen_dates) == 1 else ("多日" if screen_dates else None), **_summary(grouped[joined])})
        page = all_rows[offset:offset + limit]
        meta = self.meta(user_id)
        latest = meta.get("resolved_as_of") or max((row.get("available_through") for row in all_rows if row.get("available_through")), default=None)
        return {
            "items": page,
            "batches": [{"joined_date": row[0], "count": int(row[1])} for row in batch_rows],
            "sources": [row[0] for row in sources],
            "cohorts": cohorts,
            "summary": _summary(all_rows),
            "resolved_as_of": latest,
            "meta": meta,
            "pagination": {"offset": offset, "limit": limit, "returned": len(page), "total": len(all_rows), "has_more": offset + len(page) < len(all_rows)},
        }


class WatchlistMonitorService:
    def __init__(self, repository: WatchlistMonitorRepository | None = None, watchlist=None):
        self.repository = repository or WatchlistMonitorRepository()
        self.watchlist = watchlist or WatchlistRepository(self.repository.path)

    @staticmethod
    def _resolved_as_of(requested, include_etf=False):
        paths = [Path(STOCK_CACHE_FILE), Path(INDEX_CACHE_FILE)] + ([Path(ETF_CACHE_FILE)] if include_etf else [])
        latest = [_parquet_latest(path) for path in paths]
        latest = [item for item in latest if item is not None]
        if not latest:
            return pd.Timestamp(requested).normalize() if requested else None
        resolved = max(latest)
        if requested:
            resolved = min(resolved, pd.Timestamp(requested).normalize())
        return resolved

    @staticmethod
    def _outcome(item, joined_date, horizon, bars, benchmark, resolved):
        code = _normal_code(item["code"])
        computed = _now()
        base = {
            "item_id": item["id"], "joined_date": joined_date, "code": code, "name": item.get("name") or code,
            "status": item.get("status") or "watching", "source_module": item.get("source_module") or "",
            "screen_date": item.get("screen_date"), "horizon_days": horizon, "outcome_status": "unavailable",
            "entry_date": None, "entry_open": None, "peak_high": None, "peak_high_date": None, "peak_return_pct": None,
            "close_value": None, "close_date": None, "close_return_pct": None, "low_value": None, "low_date": None,
            "adverse_return_pct": None, "benchmark_entry_open": None, "benchmark_peak_high": None,
            "benchmark_peak_return_pct": None, "benchmark_close_value": None, "benchmark_close_return_pct": None,
            "peak_excess_pct": None, "close_excess_pct": None, "available_through": _text_date(resolved),
            "computed_at": computed, "source_version": SOURCE_VERSION,
        }
        source = bars[bars["代码"].astype(str) == code].sort_values("日期") if len(bars) else bars
        if len(source) == 0:
            # 加入日期晚于当前行情截止点时，尚无入场交易日，而不是无行情。
            if pd.Timestamp(joined_date).normalize() > resolved:
                base["outcome_status"] = "pending"
            return base
        future = source[(source["日期"] > pd.Timestamp(joined_date).normalize()) & (source["日期"] <= resolved)].sort_values("日期")
        outcome = calculate_forward_window(future, benchmark, horizon)
        base.update({key: value for key, value in outcome.items() if key in base})
        base["outcome_status"] = outcome["status"]
        if outcome.get("close_date"):
            base["available_through"] = outcome["close_date"]
        return base

    def refresh(
        self, *, user_id: int, as_of: str | None = None, rebuild: bool = False,
        item_ids: Iterable[int] | None = None,
    ) -> dict:
        items = self.watchlist.list_items(user_id=user_id, limit=2000)
        if item_ids is not None:
            wanted = {int(item_id) for item_id in item_ids}
            items = [item for item in items if int(item["id"]) in wanted]
        if not items:
            return {"user_id": user_id, "rows": 0, "items": 0, "resolved_as_of": None, "message": "暂无需要更新的观察池记录"}
        codes = [_normal_code(item["code"]) for item in items]
        include_etf = any(str(code).startswith(("sh5", "sh56", "sh58", "sz5", "sz56", "sz58")) for code in codes)
        resolved = self._resolved_as_of(as_of, include_etf=include_etf)
        if resolved is None:
            return {"rows": 0, "items": len(items), "resolved_as_of": None, "message": "行情缓存为空"}
        joined = [_joined_date(item.get("created_at")) for item in items]
        start = min(pd.Timestamp(value).normalize() for value in joined)
        stocks = [code for code in codes if not str(code).lower().replace("etf:", "").startswith(("sh5", "sh56", "sh58"))]
        etfs = [code[2:] if code[:2] in {"sh", "sz"} else code for code in codes if code not in stocks]
        stock = _read_bars(Path(STOCK_CACHE_FILE), stocks, start, resolved)
        etf = _read_bars(Path(ETF_CACHE_FILE), etfs, start, resolved)
        if len(etf):
            etf = etf.copy(); etf["代码"] = etf["代码"].map(lambda value: _normal_code(str(value)))
        bars = pd.concat([stock, etf], ignore_index=True) if len(etf) else stock
        benchmark = _read_bars(Path(INDEX_CACHE_FILE), [BENCHMARK_CODE], start, resolved)
        rows = [self._outcome(item, joined_date, horizon, bars, benchmark, resolved)
                for item, joined_date in zip(items, joined) for horizon in WINDOWS]
        self.repository.replace_items(user_id, [item["id"] for item in items], [{"user_id": user_id, **row} for row in rows])
        self.repository.set_resolved_as_of(user_id, _text_date(resolved))
        result = {"user_id": user_id, "rows": len(rows), "items": len(items), "resolved_as_of": _text_date(resolved), "message": "观察池收益监控已刷新", "rebuild": bool(rebuild), "generated_at": _now()}
        atomic_write_json(result, MONITOR_CACHE)
        return result

    def query(self, *, user_id: int, **kwargs) -> dict:
        horizon = int(kwargs.pop("horizon", 5))
        result = self.repository.query(user_id, horizon=horizon, **kwargs)
        meta = result.get("meta", {})
        warnings = ["理论路径峰值只表示窗口内曾出现的价格空间，不代表可以精准在最高价成交；未计手续费、滑点和涨跌停限制"]
        if meta.get("last_error"):
            warnings.append(f"最近一次计算失败：{meta['last_error']}。页面保留可用历史结果")
        result.update({"schema_version": "watchlist-monitor-v1", "request": {"horizon": horizon, **kwargs},
                      "requested_range": {"from": kwargs.get("date_from"), "to": kwargs.get("date_to"), "batch": kwargs.get("batch"), "horizon": horizon},
                      "temporal_scope": "加入日后第一个交易日开盘入场；窗口内最高/最低/末收盘；不使用未来数据",
                      "freshness": meta.get("last_refresh"), "provenance": {"daily_bars": "本地 Parquet", "benchmark": BENCHMARK_CODE}, "warnings": warnings})
        return result


__all__ = ["MONITOR_CACHE", "SOURCE_VERSION", "WatchlistMonitorRepository", "WatchlistMonitorService"]
