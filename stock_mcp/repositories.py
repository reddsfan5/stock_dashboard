"""只读数据仓储。

所有路径由项目根目录和固定白名单派生；调用方不能传 SQL、文件路径或 URL。
Parquet 查询优先使用列裁剪和过滤条件，SQLite 固定使用 ``mode=ro``。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq

from data.instruments import Instrument, InstrumentResolver
from stock_mcp.contracts import json_safe


SHANGHAI = ZoneInfo("Asia/Shanghai")
PROJECT_DIR = Path(__file__).resolve().parents[1]

DAILY_COLUMN_MAP = {
    "代码": "code", "日期": "date", "开盘": "open", "最高": "high",
    "最低": "low", "收盘": "close", "前收": "previous_close",
    "成交量": "volume", "成交量(手)": "volume_lots", "成交额": "amount",
    "换手率%": "turnover_rate_pct", "来源": "source",
}
MINUTE_COLUMN_MAP = {
    "代码": "code", "时间": "time", "开盘": "open", "最高": "high",
    "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
}

DATASETS = {
    "stock_daily": ("cache/stock_kline_cache.parquet", "日期", "代码"),
    "etf_daily": ("cache/etf_kline_cache.parquet", "日期", "代码"),
    "index_daily": ("cache/index_kline_cache.parquet", "日期", "代码"),
    "minute": ("cache/minute_kline_cache.parquet", "时间", "代码"),
    "index_minute": ("cache/index_minute_cache.parquet", "时间", "代码"),
    "daily_basic": ("cache/daily_basic_cache.parquet", "日期", "代码"),
    "stock_info": ("cache/stock_info.parquet", None, "代码"),
}

ARTIFACT_ALLOWLIST = {
    "dashboard.html", "dashboard_mobile.html", "market_overview.html",
    "market_heatmap.html", "market_proverbs.html", "slow_rise_backtest.html",
    "quant_report.html", "stats_report.html", "screen_to_trade_report.html",
    "screen_to_trade_report.json", "grid_search_520500_best.json",
    "weekday_stats.html", "backtest_break_resume.html", "best_worst_charts.html",
    "best_worst_windows.html", "strategy_01.html", "strategy_02.html",
    "strategy_03.html", "strategy_04.html", "strategy_05.html", "strategy_06.html",
    "strategy_07.html", "strategy_09.html", "strategy_10.html", "strategy_11.html",
    "strategy_12.html", "strategy_13.html", "strategy_14.html",
}


class RepositoryError(RuntimeError):
    """缓存缺失、损坏或不符合数据契约。"""


def _iso_mtime(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, SHANGHAI).isoformat(timespec="seconds")
    except OSError:
        return None


def _signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None, None


def _parse_date(value, field="date") -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value).normalize()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是有效日期") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{field} 必须是有效日期")
    return timestamp


def _read_parquet(path: Path, *, columns=None, filters=None) -> pd.DataFrame:
    if not path.exists():
        raise RepositoryError(f"本地缓存缺失：{path.name}")
    try:
        return pd.read_parquet(path, columns=columns, filters=filters)
    except Exception as exc:  # pyarrow 的损坏文件异常类型较多
        raise RepositoryError(f"本地缓存无法读取：{path.name}（{type(exc).__name__}）") from exc


@dataclass
class ReadOnlyScreenData:
    """满足既有 ``find_all`` 接口的只读、日期已截断数据对象。"""

    frame: pd.DataFrame
    names: dict[str, str]

    @property
    def cache(self) -> pd.DataFrame:
        return self.frame

    def get_kline(self, code: str, days: int = 60) -> pd.DataFrame:
        return self.frame[self.frame["代码"] == code].sort_values("日期").tail(days).copy()

    def get_stock_name(self, code: str) -> str:
        return self.names.get(code, code)


class LocalResearchRepository:
    """本地 Parquet、SQLite 和报告的统一只读入口。"""

    def __init__(self, project_dir: Path | str = PROJECT_DIR, resolver=None):
        self.project_dir = Path(project_dir)
        self.cache_dir = self.project_dir / "cache"
        self.state_dir = self.project_dir / "state"
        self.output_dir = self.project_dir / "output"
        self.resolver = resolver or InstrumentResolver(self.project_dir)
        self._metadata_cache: dict[str, tuple[tuple, dict]] = {}

    def path_for(self, dataset: str) -> Path:
        if dataset not in DATASETS:
            raise ValueError(f"未知数据集：{dataset}")
        return self.project_dir / DATASETS[dataset][0]

    def provenance(self, dataset: str) -> dict:
        path = self.path_for(dataset)
        return {
            "dataset": dataset,
            "storage": "local_parquet",
            "modified_at": _iso_mtime(path),
        }

    def _parquet_metadata(self, dataset: str) -> dict:
        path = self.path_for(dataset)
        signature = _signature(path)
        cached = self._metadata_cache.get(dataset)
        if cached and cached[0] == signature:
            return cached[1]
        if not path.exists():
            value = {"status": "missing", "rows": 0, "modified_at": None}
            self._metadata_cache[dataset] = (signature, value)
            return value
        try:
            parquet = pq.ParquetFile(path)
            meta = parquet.metadata
            date_column = DATASETS[dataset][1]
            minimum = maximum = None
            if date_column and date_column in parquet.schema.names:
                column_index = parquet.schema.names.index(date_column)
                for group_index in range(meta.num_row_groups):
                    stats = meta.row_group(group_index).column(column_index).statistics
                    if not stats or not stats.has_min_max:
                        continue
                    minimum = stats.min if minimum is None else min(minimum, stats.min)
                    maximum = stats.max if maximum is None else max(maximum, stats.max)
            value = {
                "status": "available",
                "rows": int(meta.num_rows),
                "date_min": pd.Timestamp(minimum).isoformat() if minimum is not None else None,
                "date_max": pd.Timestamp(maximum).isoformat() if maximum is not None else None,
                "modified_at": _iso_mtime(path),
                "size_bytes": path.stat().st_size,
            }
            code_column = DATASETS[dataset][2]
            if code_column and code_column in parquet.schema.names:
                # 仅显式查询数据状态时扫描单个字典友好列；结果按文件签名缓存。
                codes = pq.read_table(path, columns=[code_column]).column(0).combine_chunks()
                value["instrument_count"] = len(codes.unique())
        except Exception as exc:
            value = {
                "status": "corrupt", "rows": None, "modified_at": _iso_mtime(path),
                "error": type(exc).__name__,
            }
        self._metadata_cache[dataset] = (signature, value)
        return value

    def data_status(self) -> dict:
        datasets = {key: self._parquet_metadata(key) for key in DATASETS}
        status_path = self.cache_dir / "daily_update_status.json"
        update_status = None
        if status_path.exists():
            try:
                update_status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                update_status = {"status": "unreadable"}
        return {"datasets": datasets, "daily_update": json_safe(update_status)}

    def daily_bars(
        self, instrument: Instrument, *, start=None, end=None, limit=120,
    ) -> tuple[pd.DataFrame, Optional[str]]:
        limit = min(max(int(limit), 1), 500)
        dataset = {
            "stock": "stock_daily", "etf": "etf_daily", "index": "index_daily"
        }[instrument.asset_type]
        path = self.path_for(dataset)
        filters: list[tuple] = [("代码", "==", instrument.storage_code)]
        requested_end = _parse_date(end, "end") if end is not None else None
        requested_start = _parse_date(start, "start") if start is not None else None
        if requested_start is not None:
            filters.append(("日期", ">=", requested_start))
        if requested_end is not None:
            filters.append(("日期", "<=", requested_end))
        frame = _read_parquet(path, filters=filters)
        if frame.empty:
            return frame, None
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.normalize()
        frame = frame.dropna(subset=["日期"]).sort_values("日期").tail(limit).copy()
        resolved = frame["日期"].max().strftime("%Y-%m-%d") if len(frame) else None
        if instrument.asset_type == "etf":
            frame["代码"] = instrument.code
        return frame, resolved

    def minute_bars(
        self, instrument: Instrument, *, market_date, as_of=None,
    ) -> pd.DataFrame:
        day = _parse_date(market_date, "market_date")
        if instrument.asset_type == "index":
            path = self.path_for("index_minute")
            storage_code = instrument.storage_code
        else:
            path = self.path_for("minute")
            storage_code = instrument.code
        start = day
        end = day + pd.Timedelta(days=1)
        filters = [
            ("代码", "==", storage_code), ("时间", ">=", start), ("时间", "<", end)
        ]
        frame = _read_parquet(path, filters=filters)
        if frame.empty:
            return frame
        frame["时间"] = pd.to_datetime(frame["时间"], errors="coerce")
        frame = frame.dropna(subset=["时间"]).sort_values("时间")
        if as_of:
            clock = str(as_of).strip()
            if re.fullmatch(r"\d{2}:\d{2}", clock):
                clock += ":00"
            try:
                cutoff = pd.Timestamp(f"{day.strftime('%Y-%m-%d')} {clock}")
            except ValueError as exc:
                raise ValueError("as_of 必须是 HH:MM 或 HH:MM:SS") from exc
            frame = frame[frame["时间"] <= cutoff]
        return frame.copy()

    @staticmethod
    def public_daily(frame: pd.DataFrame) -> list[dict]:
        out = frame.rename(columns=DAILY_COLUMN_MAP).copy()
        out = out[[column for column in DAILY_COLUMN_MAP.values() if column in out.columns]]
        if "date" in out:
            out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
        for column in ("open", "high", "low", "close", "previous_close"):
            if column in out:
                out[column] = pd.to_numeric(out[column], errors="coerce").round(4)
        return json_safe(out.to_dict("records"))

    @staticmethod
    def public_minute(frame: pd.DataFrame) -> list[dict]:
        out = frame.rename(columns=MINUTE_COLUMN_MAP).copy()
        out = out[[column for column in MINUTE_COLUMN_MAP.values() if column in out.columns]]
        if "time" in out:
            out["time"] = pd.to_datetime(out["time"]).dt.strftime("%Y-%m-%dT%H:%M:%S")
        for column in ("open", "high", "low", "close"):
            if column in out:
                out[column] = pd.to_numeric(out[column], errors="coerce").round(4)
        return json_safe(out.to_dict("records"))

    def latest_daily_date(self) -> Optional[str]:
        dates = []
        for dataset in ("stock_daily", "etf_daily"):
            value = self._parquet_metadata(dataset).get("date_max")
            if value:
                dates.append(pd.Timestamp(value))
        return max(dates).strftime("%Y-%m-%d") if dates else None

    def feature_inputs(self, *, as_of=None, instrument_codes=None) -> tuple[pd.DataFrame, pd.DataFrame, str]:
        target = _parse_date(as_of or self.latest_daily_date(), "as_of")
        start = target - pd.Timedelta(days=110)
        daily_frames = []
        for dataset in ("stock_daily", "etf_daily"):
            path = self.path_for(dataset)
            filters = [("日期", ">=", start), ("日期", "<=", target)]
            frame = _read_parquet(path, filters=filters)
            if dataset == "etf_daily" and len(frame):
                raw = frame["代码"].astype(str).str.zfill(6)
                frame["代码"] = raw.map(lambda code: ("sh" if code.startswith("5") else "sz") + code)
            daily_frames.append(frame)
        daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
        if instrument_codes:
            daily = daily[daily["代码"].isin(instrument_codes)]
        basic_path = self.path_for("daily_basic")
        try:
            basic = _read_parquet(
                basic_path, filters=[("日期", "<=", target)]
            )
        except RepositoryError:
            basic = pd.DataFrame()
        actual = daily["日期"].max() if len(daily) else None
        return daily, basic, pd.Timestamp(actual).strftime("%Y-%m-%d") if actual is not None else target.strftime("%Y-%m-%d")

    def screener_data(self, *, as_of=None, universe="all") -> tuple[ReadOnlyScreenData, str]:
        if universe not in {"all", "stock", "etf"}:
            raise ValueError("universe 只能是 all、stock 或 etf")
        target = _parse_date(as_of or self.latest_daily_date(), "as_of")
        start = target - pd.Timedelta(days=430)
        frames = []
        if universe in {"all", "stock"}:
            stock = _read_parquet(
                self.path_for("stock_daily"),
                filters=[("日期", ">=", start), ("日期", "<=", target)],
            )
            main_board = ("sh600", "sh601", "sh603", "sh605", "sz000", "sz001", "sz002", "sz003")
            stock = stock[stock["代码"].astype(str).str.startswith(main_board)]
            frames.append(stock)
        if universe in {"all", "etf"}:
            etf = _read_parquet(
                self.path_for("etf_daily"),
                filters=[("日期", ">=", start), ("日期", "<=", target)],
            )
            raw = etf["代码"].astype(str).str.zfill(6)
            etf["代码"] = raw.map(lambda code: ("sh" if code.startswith("5") else "sz") + code)
            frames.append(etf)
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
        actual = frame["日期"].max()
        names = {item.code: item.name for item in self.resolver.search("", limit=50)}
        # search() 有返回上限；直接使用内部目录以确保全量名称，但仍不扫行情。
        names.update({item.code: item.name for item in self.resolver.all()})
        return ReadOnlyScreenData(frame, names), pd.Timestamp(actual).strftime("%Y-%m-%d")

    @staticmethod
    def _read_only_db(path: Path) -> sqlite3.Connection:
        if not path.exists():
            raise RepositoryError(f"本地研究库缺失：{path.name}")
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def symbol_context(self, code: str) -> dict:
        def query(db_name: str, sql: str, params: tuple) -> list[dict]:
            path = self.state_dir / db_name
            if not path.exists():
                return []
            with self._read_only_db(path) as connection:
                return [dict(row) for row in connection.execute(sql, params).fetchall()]

        watch = query(
            "watchlist.sqlite3",
            "SELECT * FROM watch_item WHERE code=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 20",
            (code,),
        )
        tracks = query(
            "watchlist.sqlite3",
            "SELECT * FROM track_result WHERE code=? ORDER BY track_date DESC,id DESC LIMIT 20",
            (code,),
        )
        training = query(
            "training_sessions.sqlite3",
            """SELECT r.*,(SELECT COUNT(*) FROM decision_snapshot d
               WHERE d.run_id=r.id AND d.deleted_at IS NULL) AS decision_count
               FROM training_run r WHERE r.code=? AND r.deleted_at IS NULL
               ORDER BY r.created_at DESC LIMIT 10""",
            (code,),
        )
        journal = query(
            "stock_journal.sqlite3",
            "SELECT * FROM journal_case WHERE code=? ORDER BY updated_at DESC LIMIT 20",
            (code,),
        )
        hypotheses = query(
            "hypotheses.sqlite3",
            "SELECT * FROM hypothesis WHERE code=? AND deleted_at IS NULL ORDER BY updated_at DESC LIMIT 20",
            (code,),
        )
        return json_safe({
            "code": code,
            "watchlist": {"count": len(watch), "items": watch},
            "tracks": {"count": len(tracks), "items": tracks},
            "training": {"count": len(training), "runs": training},
            "journal": {"count": len(journal), "cases": journal},
            "hypotheses": {"count": len(hypotheses), "items": hypotheses},
        })

    def artifacts(self, artifact_id: Optional[str] = None) -> list[dict]:
        if artifact_id is not None and artifact_id not in ARTIFACT_ALLOWLIST:
            raise ValueError("报告不在允许访问的白名单中")
        names = [artifact_id] if artifact_id else sorted(ARTIFACT_ALLOWLIST)
        values = []
        for name in names:
            path = self.output_dir / name
            if not path.is_file():
                continue
            item = {
                "artifact_id": name,
                "kind": path.suffix.lstrip("."),
                "size_bytes": path.stat().st_size,
                "modified_at": _iso_mtime(path),
                "safe_url": f"http://127.0.0.1:8000/{quote(name)}",
            }
            if path.suffix == ".json":
                if path.stat().st_size > 2_000_000:
                    item["summary"] = {"status": "too_large", "max_bytes": 2_000_000}
                else:
                    try:
                        item["summary"] = json_safe(json.loads(path.read_text(encoding="utf-8")))
                    except (OSError, json.JSONDecodeError, TypeError):
                        item["summary"] = {"status": "unreadable"}
            values.append(item)
        return values
