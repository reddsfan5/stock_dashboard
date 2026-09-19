"""每日精选的后续收益监控。

这个模块只读取本地日 K、指数和短名单快照，不发起网络请求，也不修改行情
Parquet。主口径是“短名单日之后第一个交易日开盘入场”，并记录窗口内曾经
出现过的最高价路径收益，同时保存窗口末收盘收益和沪深 300 超额收益。
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Iterator, Optional

import pandas as pd

from data.etf import CACHE_FILE as ETF_CACHE_FILE
from data.forward_returns import calculate_forward_window
from data.index import CACHE_FILE as INDEX_CACHE_FILE
from data.kline import CACHE_FILE as STOCK_CACHE_FILE
from data.shortlist import DEFAULT_DB_PATH, ShortlistRepository
from data.storage import atomic_write_json


PROJECT_DIR = Path(__file__).resolve().parents[1]
MONITOR_CACHE = PROJECT_DIR / "cache" / "shortlist_monitor.json"
WINDOWS = (1, 3, 5, 10, 20)
BENCHMARK_CODE = "sh000300"
SOURCE_VERSION = "daily-bars-v1"


def _iso_date(value, field: str = "日期") -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 YYYY-MM-DD") from exc


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _finite(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pct(value, base):
    value = _finite(value)
    base = _finite(base)
    if value is None or base is None or base <= 0:
        return None
    return (value / base - 1.0) * 100.0


def _text_date(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _parquet_latest(path: Path) -> Optional[pd.Timestamp]:
    """从 Parquet 元数据读取最新日期，避免启动时加载整份行情。"""
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as parquet

        reader = parquet.ParquetFile(path)
        names = reader.schema_arrow.names
        try:
            index = names.index("日期")
        except ValueError:
            index = None
        if index is not None:
            latest = None
            for group in reader.metadata.row_groups:
                stats = group.column(index).statistics
                if stats is None or stats.max is None:
                    continue
                current = pd.Timestamp(stats.max).normalize()
                latest = current if latest is None else max(latest, current)
            if latest is not None:
                return latest
    except Exception:
        pass
    try:
        frame = pd.read_parquet(path, columns=["日期"])
        if len(frame):
            return pd.to_datetime(frame["日期"], errors="coerce").max().normalize()
    except Exception:
        return None
    return None


def _read_bars(path: Path, codes: Iterable[str], start, end) -> pd.DataFrame:
    """按代码和日期过滤读取 OHLC，优先使用 Parquet predicate pushdown。"""
    requested = list(dict.fromkeys(str(code) for code in codes if str(code)))
    columns = ["代码", "日期", "开盘", "最高", "最低", "收盘"]
    if not requested or not path.exists():
        return pd.DataFrame(columns=columns)
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    filters = [
        ("代码", "in", requested),
        ("日期", ">=", start),
        ("日期", "<=", end),
    ]
    try:
        frame = pd.read_parquet(path, columns=columns, filters=filters)
    except Exception:
        try:
            frame = pd.read_parquet(path, columns=columns)
        except Exception:
            return pd.DataFrame(columns=columns)
        frame = frame[
            frame["代码"].astype(str).isin(requested)
            & pd.to_datetime(frame["日期"], errors="coerce").between(start, end)
        ]
    if len(frame) == 0:
        return pd.DataFrame(columns=columns)
    frame = frame.copy()
    frame["代码"] = frame["代码"].astype(str)
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.normalize()
    for column in columns[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["日期"]).sort_values(["代码", "日期"])


def _is_etf(code: str) -> bool:
    digits = str(code).lower().replace("etf:", "")
    digits = digits[2:] if digits.startswith(("sh", "sz")) else digits
    return digits.startswith(("5", "56", "58"))


def _raw_etf_code(code: str) -> str:
    value = str(code).lower().replace("etf:", "")
    return value[2:] if value.startswith(("sh", "sz")) else value


def _normal_code(code: str) -> str:
    value = str(code or "").strip().lower()
    if value.startswith("etf:"):
        value = value[4:]
    if value.startswith(("sh", "sz", "bj")):
        return value
    digits = "".join(char for char in value if char.isdigit())
    if len(digits) != 6:
        return value
    return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits


class ShortlistMonitorRepository:
    """收益结果的只读查询与幂等写入仓储。"""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shortlist_outcome (
                    market_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    rank INTEGER NOT NULL DEFAULT 0,
                    sector TEXT NOT NULL DEFAULT '',
                    score REAL,
                    horizon_days INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    entry_date TEXT,
                    entry_open REAL,
                    signal_close REAL,
                    peak_high REAL,
                    peak_high_date TEXT,
                    peak_return_pct REAL,
                    close_value REAL,
                    close_date TEXT,
                    close_return_pct REAL,
                    low_value REAL,
                    low_date TEXT,
                    adverse_return_pct REAL,
                    benchmark_entry_open REAL,
                    benchmark_peak_high REAL,
                    benchmark_peak_return_pct REAL,
                    benchmark_close_value REAL,
                    benchmark_close_return_pct REAL,
                    peak_excess_pct REAL,
                    close_excess_pct REAL,
                    signal_close_peak_return_pct REAL,
                    available_through TEXT,
                    computed_at TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    PRIMARY KEY (market_date, code, horizon_days)
                );
                CREATE INDEX IF NOT EXISTS idx_shortlist_outcome_date_horizon
                    ON shortlist_outcome(market_date, horizon_days);
                CREATE INDEX IF NOT EXISTS idx_shortlist_outcome_status
                    ON shortlist_outcome(status, horizon_days);
                CREATE TABLE IF NOT EXISTS shortlist_monitor_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def replace_dates(self, dates: Iterable[str], rows: Iterable[dict]) -> int:
        dates = list(dict.fromkeys(_iso_date(value) for value in dates))
        rows = list(rows)
        if not dates:
            return 0
        columns = [
            "market_date", "code", "name", "rank", "sector", "score",
            "horizon_days", "status", "entry_date", "entry_open", "signal_close",
            "peak_high", "peak_high_date", "peak_return_pct", "close_value",
            "close_date", "close_return_pct", "low_value", "low_date",
            "adverse_return_pct", "benchmark_entry_open", "benchmark_peak_high",
            "benchmark_peak_return_pct", "benchmark_close_value",
            "benchmark_close_return_pct", "peak_excess_pct", "close_excess_pct",
            "signal_close_peak_return_pct", "available_through", "computed_at",
            "source_version",
        ]
        placeholders = ",".join("?" for _ in columns)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "DELETE FROM shortlist_outcome WHERE market_date = ?",
                [(value,) for value in dates],
            )
            values = []
            for row in rows:
                values.append(tuple(row.get(column) for column in columns))
            if values:
                connection.executemany(
                    f"INSERT INTO shortlist_outcome ({','.join(columns)}) VALUES ({placeholders})",
                    values,
                )
            connection.execute(
                "INSERT INTO shortlist_monitor_meta(key,value) VALUES('last_refresh',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_now(),),
            )
            connection.execute(
                "INSERT INTO shortlist_monitor_meta(key,value) VALUES('last_error','') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            connection.commit()
        return len(values)

    def record_failure(self, error: str) -> None:
        """记录旁路刷新失败，供页面提示；不触碰行情和候选快照。"""
        message = str(error or "未知错误").strip()[:1000]
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO shortlist_monitor_meta(key,value) VALUES('last_error',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (message,),
            )
            connection.commit()

    def meta(self) -> dict:
        with self._connect() as connection:
            rows = connection.execute("SELECT key,value FROM shortlist_monitor_meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def refresh_state(self, dates: Iterable[str]) -> dict[str, dict]:
        """返回批次已有结果的轻量状态，供每日增量刷新判断。"""
        values = list(dict.fromkeys(_iso_date(value) for value in dates))
        if not values:
            return {}
        placeholders = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT market_date,
                       COUNT(*) AS row_count,
                       SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS complete_count,
                       SUM(CASE WHEN status IN ('pending','partial','unavailable') THEN 1 ELSE 0 END) AS open_count,
                       MAX(computed_at) AS computed_at
                FROM shortlist_outcome
                WHERE market_date IN ({placeholders})
                GROUP BY market_date
                """,
                values,
            ).fetchall()
        return {row["market_date"]: dict(row) for row in rows}

    def query(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        horizon: int = 5,
        rank_max: int | None = None,
        sector: str | None = None,
        status: str | None = None,
        sort: str = "rank",
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        if horizon not in WINDOWS:
            raise ValueError(f"horizon 必须是 {','.join(map(str, WINDOWS))} 之一")
        if sort not in {"rank", "peak_return_pct", "close_return_pct"}:
            raise ValueError("sort 不合法")
        date_from = _iso_date(date_from, "from") if date_from else None
        date_to = _iso_date(date_to, "to") if date_to else None
        if date_from and date_to and date_from > date_to:
            raise ValueError("from 不能晚于 to")
        if rank_max is not None and int(rank_max) < 1:
            raise ValueError("rank_max 必须为正整数")
        offset = max(0, int(offset))
        limit = min(max(int(limit), 1), 500)
        clauses = ["horizon_days = ?"]
        args: list = [horizon]
        if date_from:
            clauses.append("market_date >= ?")
            args.append(date_from)
        if date_to:
            clauses.append("market_date <= ?")
            args.append(date_to)
        if rank_max is not None:
            clauses.append("rank <= ?")
            args.append(int(rank_max))
        if sector:
            clauses.append("sector = ?")
            args.append(str(sector).strip())
        if status:
            if status not in {"pending", "partial", "complete", "unavailable"}:
                raise ValueError("status 不合法")
            clauses.append("status = ?")
            args.append(status)
        where = " AND ".join(clauses)
        sort_sql = {
            "rank": "market_date DESC, rank ASC, code ASC",
            "peak_return_pct": "peak_return_pct IS NULL, peak_return_pct DESC, market_date DESC, rank ASC",
            "close_return_pct": "close_return_pct IS NULL, close_return_pct DESC, market_date DESC, rank ASC",
        }[sort]
        select = f"SELECT * FROM shortlist_outcome WHERE {where} ORDER BY {sort_sql}"
        sector_clauses = ["horizon_days = ?"]
        sector_args: list = [horizon]
        if date_from:
            sector_clauses.append("market_date >= ?")
            sector_args.append(date_from)
        if date_to:
            sector_clauses.append("market_date <= ?")
            sector_args.append(date_to)
        with self._connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM shortlist_outcome WHERE {where}", args).fetchone()[0])
            all_rows = [dict(row) for row in connection.execute(select, args).fetchall()]
            batches = [dict(row) for row in connection.execute(
                "SELECT market_date, COUNT(*) AS count FROM shortlist_outcome "
                "WHERE horizon_days = ? GROUP BY market_date ORDER BY market_date DESC",
                (horizon,),
            ).fetchall()]
            sectors = [row[0] for row in connection.execute(
                f"SELECT DISTINCT sector FROM shortlist_outcome WHERE {' AND '.join(sector_clauses)} AND sector != '' ORDER BY sector", sector_args
            ).fetchall()]
        page_rows = all_rows[offset: offset + limit]
        return {
            "items": page_rows,
            "total": total,
            "summary": _summary(all_rows),
            "cohorts": _cohorts(all_rows),
            "batches": batches,
            "sectors": sectors,
            "resolved_as_of": max(
                (row.get("available_through") for row in all_rows if row.get("available_through")),
                default=None,
            ),
            "meta": self.meta(),
            "pagination": {
                "offset": offset,
                "limit": limit,
                "returned": len(page_rows),
                "total": total,
                "has_more": offset + len(page_rows) < total,
            },
        }


def _summary(rows: list[dict]) -> dict:
    def vals(key):
        return [float(row[key]) for row in rows if _finite(row.get(key)) is not None]

    peak = vals("peak_return_pct")
    close = vals("close_return_pct")
    adverse = vals("adverse_return_pct")
    peak_excess = vals("peak_excess_pct")
    close_excess = vals("close_excess_pct")
    benchmark_peak = vals("benchmark_peak_return_pct")
    benchmark_close = vals("benchmark_close_return_pct")
    statuses = {}
    for row in rows:
        statuses[row.get("status") or "unknown"] = statuses.get(row.get("status") or "unknown", 0) + 1
    return {
        "count": len(rows),
        "completed": statuses.get("complete", 0),
        "partial": statuses.get("partial", 0),
        "pending": statuses.get("pending", 0),
        "unavailable": statuses.get("unavailable", 0),
        "completion_pct": round(statuses.get("complete", 0) / len(rows) * 100, 2) if rows else 0.0,
        "peak_mean_pct": round(mean(peak), 4) if peak else None,
        "peak_median_pct": round(median(peak), 4) if peak else None,
        "peak_positive_rate_pct": round(sum(value > 0 for value in peak) / len(peak) * 100, 2) if peak else None,
        "peak_hit_5pct_rate_pct": round(sum(value >= 5 for value in peak) / len(peak) * 100, 2) if peak else None,
        "close_mean_pct": round(mean(close), 4) if close else None,
        "close_median_pct": round(median(close), 4) if close else None,
        "close_positive_rate_pct": round(sum(value > 0 for value in close) / len(close) * 100, 2) if close else None,
        "adverse_median_pct": round(median(adverse), 4) if adverse else None,
        "benchmark_peak_median_pct": round(median(benchmark_peak), 4) if benchmark_peak else None,
        "benchmark_close_median_pct": round(median(benchmark_close), 4) if benchmark_close else None,
        "peak_excess_median_pct": round(median(peak_excess), 4) if peak_excess else None,
        "close_excess_median_pct": round(median(close_excess), 4) if close_excess else None,
    }


def _cohorts(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("market_date") or ""), []).append(row)
    result = []
    for market_date in sorted(grouped, reverse=True):
        group = grouped[market_date]
        summary = _summary(group)
        result.append({"market_date": market_date, **summary})
    return result


class ShortlistMonitorService:
    """从短名单快照和本地行情生成收益结果。"""

    def __init__(self, repository: ShortlistMonitorRepository | None = None, shortlist=None):
        self.repository = repository or ShortlistMonitorRepository()
        self.shortlist = shortlist or ShortlistRepository(self.repository.path)

    def _resolved_as_of(self, requested, *, include_etf: bool) -> pd.Timestamp | None:
        latest = [_parquet_latest(Path(STOCK_CACHE_FILE)), _parquet_latest(Path(INDEX_CACHE_FILE))]
        if include_etf:
            latest.append(_parquet_latest(Path(ETF_CACHE_FILE)))
        latest = [value for value in latest if value is not None]
        if not latest:
            return pd.Timestamp(requested).normalize() if requested else None
        resolved = max(latest)
        if requested:
            resolved = min(resolved, pd.Timestamp(requested).normalize())
        return resolved

    @staticmethod
    def _rows_for_code(frame: pd.DataFrame, code: str) -> pd.DataFrame:
        if len(frame) == 0:
            return frame
        return frame[frame["代码"].astype(str) == code].sort_values("日期")

    def _outcome(self, card: dict, market_date: str, horizon: int, bars: pd.DataFrame, benchmark: pd.DataFrame, as_of: pd.Timestamp) -> dict:
        code = _normal_code(card.get("code", ""))
        computed_at = _now()
        base = {
            "market_date": market_date,
            "code": code,
            "name": str(card.get("name") or ""),
            "rank": int(card.get("rank") or 0),
            "sector": str(card.get("sector") or ""),
            "score": _finite(card.get("score")),
            "horizon_days": horizon,
            "status": "unavailable",
            "entry_date": None,
            "entry_open": None,
            "signal_close": None,
            "peak_high": None,
            "peak_high_date": None,
            "peak_return_pct": None,
            "close_value": None,
            "close_date": None,
            "close_return_pct": None,
            "low_value": None,
            "low_date": None,
            "adverse_return_pct": None,
            "benchmark_entry_open": None,
            "benchmark_peak_high": None,
            "benchmark_peak_return_pct": None,
            "benchmark_close_value": None,
            "benchmark_close_return_pct": None,
            "peak_excess_pct": None,
            "close_excess_pct": None,
            "signal_close_peak_return_pct": None,
            "available_through": _text_date(as_of),
            "computed_at": computed_at,
            "source_version": SOURCE_VERSION,
        }
        source = self._rows_for_code(bars, code)
        if len(source) == 0:
            return base
        market_day = pd.Timestamp(market_date).normalize()
        source = source[source["日期"] <= as_of]
        signal = source[source["日期"] == market_day]
        if len(signal) == 0:
            return base
        signal_close = _finite(signal.iloc[-1]["收盘"])
        base["signal_close"] = signal_close
        future = source[source["日期"] > market_day].sort_values("日期")
        outcome = calculate_forward_window(future, benchmark, horizon)
        base.update({key: value for key, value in outcome.items() if key in base})
        base["status"] = outcome["status"]
        if outcome.get("close_date"):
            base["available_through"] = outcome["close_date"]
        base["signal_close_peak_return_pct"] = _pct(base["peak_high"], signal_close)
        return base

    def refresh(self, *, as_of: str | None = None, rebuild: bool = False) -> dict:
        date_rows = self.shortlist.dates(limit=500)
        dates = [row["market_date"] for row in date_rows]
        if not dates:
            return {"rows": 0, "dates": 0, "resolved_as_of": None, "message": "暂无每日精选历史"}
        snapshots = [self.shortlist.get(market_date) for market_date in dates]
        existing = self.repository.refresh_state(dates)
        force_recompute = bool(rebuild or as_of)
        pending_dates = []
        for meta, payload in zip(date_rows, snapshots):
            market_date = meta["market_date"]
            cards = payload.get("cards") or []
            state = existing.get(market_date)
            expected_rows = len(cards) * len(WINDOWS)
            snapshot_changed = False
            if state and state.get("computed_at") and meta.get("generated_at"):
                try:
                    snapshot_changed = pd.Timestamp(meta["generated_at"]) > pd.Timestamp(state["computed_at"])
                except (TypeError, ValueError):
                    snapshot_changed = True
            if (
                force_recompute
                or not state
                or int(state.get("row_count") or 0) != expected_rows
                or int(state.get("open_count") or 0) > 0
                or snapshot_changed
            ):
                pending_dates.append(market_date)
        if not pending_dates:
            include_etf = any(
                _is_etf(_normal_code(card.get("code", "")))
                for payload in snapshots
                for card in payload.get("cards") or []
            )
            latest = self._resolved_as_of(as_of, include_etf=include_etf)
            result = {
                "rows": 0,
                "dates": len(dates),
                "refreshed_dates": 0,
                "skipped_dates": len(dates),
                "resolved_as_of": _text_date(latest),
                "message": "每日精选收益监控无需更新",
                "rebuild": bool(rebuild),
                "generated_at": _now(),
            }
            atomic_write_json(result, MONITOR_CACHE)
            return result
        selected = [(payload, date_rows[index]) for index, payload in enumerate(snapshots) if date_rows[index]["market_date"] in pending_dates]
        cards = [card for payload, _ in selected for card in payload.get("cards") or []]
        codes = [_normal_code(card.get("code", "")) for card in cards]
        include_etf = any(_is_etf(code) for code in codes)
        resolved = self._resolved_as_of(as_of, include_etf=include_etf)
        if resolved is None:
            return {"rows": 0, "dates": len(dates), "resolved_as_of": None, "message": "行情缓存为空"}
        start = min(pd.Timestamp(value).normalize() for value in pending_dates)
        stock_codes = [code for code in codes if not _is_etf(code)]
        etf_codes = [_raw_etf_code(code) for code in codes if _is_etf(code)]
        stock = _read_bars(Path(STOCK_CACHE_FILE), stock_codes, start, resolved)
        etf = _read_bars(Path(ETF_CACHE_FILE), etf_codes, start, resolved)
        if len(etf):
            etf = etf.copy()
            etf["代码"] = etf["代码"].map(lambda value: _normal_code(str(value)))
        bars = pd.concat([stock, etf], ignore_index=True) if len(etf) else stock
        benchmark = _read_bars(Path(INDEX_CACHE_FILE), [BENCHMARK_CODE], start, resolved)
        rows = []
        for payload, _meta in selected:
            market_date = _iso_date(payload.get("market_date"))
            for card in payload.get("cards") or []:
                for horizon in WINDOWS:
                    rows.append(self._outcome(card, market_date, horizon, bars, benchmark, resolved))
        self.repository.replace_dates(pending_dates, rows)
        result = {
            "rows": len(rows),
            "dates": len(dates),
            "refreshed_dates": len(pending_dates),
            "skipped_dates": len(dates) - len(pending_dates),
            "resolved_as_of": _text_date(resolved),
            "message": "每日精选收益监控已刷新",
            "rebuild": bool(rebuild),
            "generated_at": _now(),
        }
        atomic_write_json(result, MONITOR_CACHE)
        return result

    def query(self, **kwargs) -> dict:
        horizon = int(kwargs.pop("horizon", 5))
        result = self.repository.query(horizon=horizon, **kwargs)
        meta = result.get("meta", {})
        warnings = ["理论路径峰值不代表可以精准在最高价成交，未计手续费、滑点和涨跌停限制"]
        if meta.get("last_error"):
            warnings.append(f"最近一次刷新失败：{meta['last_error']}")
        result.update({
            "schema_version": "shortlist-monitor-v1",
            "request": {"horizon": horizon, **kwargs},
            "requested_range": {
                "from": kwargs.get("date_from"),
                "to": kwargs.get("date_to"),
                "horizon": horizon,
            },
            "resolved_as_of": result.get("resolved_as_of"),
            "temporal_scope": "次日开盘入场；窗口内最高价/窗口末收盘；不使用未来数据",
            "freshness": meta.get("last_refresh"),
            "provenance": {"daily_bars": "本地 Parquet", "benchmark": BENCHMARK_CODE},
            "warnings": warnings,
        })
        return result


__all__ = [
    "BENCHMARK_CODE",
    "MONITOR_CACHE",
    "ShortlistMonitorRepository",
    "ShortlistMonitorService",
    "WINDOWS",
]
