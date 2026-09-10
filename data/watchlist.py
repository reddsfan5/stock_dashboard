"""选股观察池 / 待买池与次日跟踪。

行情仍在 Parquet；本库保存筛出标的的观察状态、次日收益复核，以及可选的板块强度快照。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from data.users import ensure_user_id_column, require_user_id

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "watchlist.sqlite3"

STATUSES = ("watching", "planned", "bought", "dropped")
STATUS_LABELS = {
    "watching": "观察中",
    "planned": "待买",
    "bought": "已买",
    "dropped": "已放弃",
}
CODE_PATTERN = re.compile(r"^(sh|sz)\d{6}$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso_date(value, field_name: str = "日期", *, allow_empty: bool = False) -> Optional[str]:
    text = str(value or "").strip()
    if not text and allow_empty:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD") from exc


def _text(value, limit: int, field_name: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field_name}不能为空")
    if len(text) > limit:
        raise ValueError(f"{field_name}不能超过 {limit} 个字符")
    return text


def normalize_code(code: str) -> str:
    value = str(code or "").strip().lower().replace(".", "")
    if CODE_PATTERN.fullmatch(value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) != 6:
        raise ValueError("证券代码格式必须类似 sh600519 或 sz000001")
    return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits


def _decode_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    if "meta_json" in result:
        try:
            result["meta"] = json.loads(result.pop("meta_json") or "{}")
        except json.JSONDecodeError:
            result["meta"] = {}
    result["status_label"] = STATUS_LABELS.get(result.get("status"), result.get("status"))
    return result


def compute_next_day_return(
    kline: pd.DataFrame,
    *,
    code: str,
    screen_date: str,
    fail_threshold_pct: float = -3.0,
) -> dict:
    """给定筛出日，计算次日收益并判断形态是否失效（次日跌幅超过阈值）。"""
    empty = {
        "code": code,
        "screen_date": screen_date,
        "track_date": None,
        "screen_close": None,
        "track_close": None,
        "return_pct": None,
        "pattern_failed": None,
        "message": "无行情",
    }
    if kline is None or len(kline) == 0:
        return empty
    frame = kline.copy()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    frame = frame.sort_values("日期")
    day = pd.Timestamp(screen_date).normalize()
    screen_rows = frame[frame["日期"] == day]
    if screen_rows.empty:
        return {**empty, "message": "筛出日无K线"}
    later = frame[frame["日期"] > day]
    if later.empty:
        return {
            **empty,
            "screen_close": float(screen_rows.iloc[-1]["收盘"]),
            "message": "尚无次日K线",
        }
    track = later.iloc[0]
    screen_close = float(screen_rows.iloc[-1]["收盘"])
    track_close = float(track["收盘"])
    if screen_close <= 0 or not math.isfinite(screen_close):
        return {**empty, "message": "筛出日收盘无效"}
    ret = (track_close / screen_close - 1.0) * 100.0
    failed = ret <= float(fail_threshold_pct)
    return {
        "code": code,
        "screen_date": screen_date,
        "track_date": pd.Timestamp(track["日期"]).strftime("%Y-%m-%d"),
        "screen_close": round(screen_close, 4),
        "track_close": round(track_close, 4),
        "return_pct": round(ret, 3),
        "pattern_failed": failed,
        "fail_threshold_pct": float(fail_threshold_pct),
        "message": "已跟踪",
    }


def rank_sector_strength(
    daily: pd.DataFrame,
    info: pd.DataFrame,
    *,
    market_date: str,
    sector_col: str = "申万1级",
    top_n: int = 15,
) -> dict:
    """用申万一级 + 当日个股涨跌幅等权平均，排出板块强度。"""
    market_date = _iso_date(market_date, "交易日")
    if daily is None or len(daily) == 0 or info is None or len(info) == 0:
        return {"market_date": market_date, "sectors": [], "message": "无数据"}
    day = pd.Timestamp(market_date).normalize()
    frame = daily.copy()
    frame["日期"] = pd.to_datetime(frame["日期"]).dt.normalize()
    day_rows = frame[frame["日期"] == day].copy()
    if day_rows.empty:
        return {"market_date": market_date, "sectors": [], "message": "当日无K线"}
    if "涨跌幅%" not in day_rows.columns:
        # 用前收推导
        if "前收" in day_rows.columns:
            day_rows["涨跌幅%"] = (day_rows["收盘"] / day_rows["前收"] - 1.0) * 100.0
        else:
            return {"market_date": market_date, "sectors": [], "message": "缺少涨跌幅"}
    cols = ["代码", sector_col] if sector_col in info.columns else None
    if cols is None:
        return {"market_date": market_date, "sectors": [], "message": f"缺少列 {sector_col}"}
    merged = day_rows.merge(info[cols].drop_duplicates("代码"), on="代码", how="left")
    merged[sector_col] = merged[sector_col].fillna("未分类")
    grouped = (
        merged.groupby(sector_col, dropna=False)
        .agg(avg_change_pct=("涨跌幅%", "mean"), count=("代码", "count"))
        .reset_index()
        .sort_values("avg_change_pct", ascending=False)
    )
    sectors = []
    for _, row in grouped.head(int(top_n)).iterrows():
        sectors.append({
            "sector": row[sector_col],
            "avg_change_pct": round(float(row["avg_change_pct"]), 3),
            "count": int(row["count"]),
        })
    weak = []
    for _, row in grouped.tail(min(10, len(grouped))).iloc[::-1].iterrows():
        weak.append({
            "sector": row[sector_col],
            "avg_change_pct": round(float(row["avg_change_pct"]), 3),
            "count": int(row["count"]),
        })
    return {
        "market_date": market_date,
        "sectors": sectors,
        "weak_sectors": weak,
        "message": f"{len(grouped)} 个板块",
    }


class WatchlistRepository:
    """观察池 SQLite 仓储。"""

    def __init__(self, path=DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS watch_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'watching',
                    source_module TEXT NOT NULL DEFAULT '',
                    screen_date TEXT,
                    thesis TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_watch_code_status
                    ON watch_item(code, status, deleted_at);
                CREATE INDEX IF NOT EXISTS idx_watch_screen_date
                    ON watch_item(screen_date);
                CREATE INDEX IF NOT EXISTS idx_watch_item_user_id
                    ON watch_item(user_id);

                CREATE TABLE IF NOT EXISTS track_result (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    item_id INTEGER REFERENCES watch_item(id),
                    code TEXT NOT NULL,
                    screen_date TEXT NOT NULL,
                    track_date TEXT NOT NULL,
                    screen_close REAL,
                    track_close REAL,
                    return_pct REAL,
                    pattern_failed INTEGER,
                    source_module TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, code, screen_date, track_date)
                );
                CREATE INDEX IF NOT EXISTS idx_track_date
                    ON track_result(track_date, screen_date);
                CREATE INDEX IF NOT EXISTS idx_track_result_user_id
                    ON track_result(user_id);
                """
            )
            admin_id = 1
            users_db = PROJECT_DIR / "state" / "users.sqlite3"
            if users_db.exists():
                from data.users import UserRepository
                found = UserRepository().get_first_admin_id()
                if found:
                    admin_id = found
            ensure_user_id_column(connection, "watch_item", admin_id)
            ensure_user_id_column(connection, "track_result", admin_id)

    def add(
        self,
        *,
        user_id,
        code: str,
        name: str = "",
        status: str = "watching",
        source_module: str = "",
        screen_date: str = None,
        thesis: str = "",
        note: str = "",
        meta: Optional[dict] = None,
    ) -> dict:
        user_id = require_user_id(user_id)
        code = normalize_code(code)
        name = _text(name, 80, "名称")
        status = str(status or "watching").strip()
        if status not in STATUSES:
            raise ValueError(f"状态必须是 {', '.join(STATUSES)}")
        source_module = _text(source_module, 40, "来源模块")
        screen_date = _iso_date(screen_date, "筛出日", allow_empty=True)
        thesis = _text(thesis, 2000, "论点")
        note = _text(note, 2000, "备注")
        meta = meta or {}
        if not isinstance(meta, dict):
            raise ValueError("meta 必须是对象")
        stamp = _now()
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT id FROM watch_item
                   WHERE user_id=? AND code=? AND deleted_at IS NULL
                     AND status IN ('watching','planned')
                   ORDER BY id DESC LIMIT 1""",
                (user_id, code),
            ).fetchone()
            if existing and status in {"watching", "planned"}:
                connection.execute(
                    """UPDATE watch_item SET name=?, status=?, source_module=COALESCE(NULLIF(?,''),source_module),
                       screen_date=COALESCE(?,screen_date), thesis=COALESCE(NULLIF(?,''),thesis),
                       note=COALESCE(NULLIF(?,''),note), meta_json=?, updated_at=?
                       WHERE id=?""",
                    (
                        name or "",
                        status,
                        source_module,
                        screen_date,
                        thesis,
                        note,
                        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                        stamp,
                        existing["id"],
                    ),
                )
                item_id = existing["id"]
            else:
                cursor = connection.execute(
                    """INSERT INTO watch_item
                       (user_id,code,name,status,source_module,screen_date,thesis,note,meta_json,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, code, name, status, source_module, screen_date, thesis, note,
                        json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                        stamp, stamp,
                    ),
                )
                item_id = cursor.lastrowid
        return self.get(item_id, user_id=user_id)

    def get(self, item_id: int, *, user_id) -> dict:
        user_id = require_user_id(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watch_item WHERE id=? AND user_id=? AND deleted_at IS NULL",
                (int(item_id), user_id),
            ).fetchone()
        if row is None:
            raise LookupError("观察池条目不存在")
        return _decode_row(row)

    def list_items(
        self,
        *,
        user_id,
        status: str = None,
        code: str = None,
        limit: int = 200,
    ) -> list:
        user_id = require_user_id(user_id)
        clauses = ["deleted_at IS NULL", "user_id=?"]
        params: list = [user_id]
        if status:
            if status not in STATUSES:
                raise ValueError("未知状态")
            clauses.append("status=?")
            params.append(status)
        if code:
            clauses.append("code=?")
            params.append(normalize_code(code))
        try:
            limit = max(1, min(int(limit), 2000))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit 必须是整数") from exc
        where = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM watch_item WHERE {where}
                    ORDER BY updated_at DESC, id DESC LIMIT ?""",
                (*params, limit),
            ).fetchall()
        return [_decode_row(row) for row in rows]

    def set_status(self, item_id: int, status: str, *, user_id, note: str = None) -> dict:
        user_id = require_user_id(user_id)
        status = str(status or "").strip()
        if status not in STATUSES:
            raise ValueError(f"状态必须是 {', '.join(STATUSES)}")
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM watch_item WHERE id=? AND user_id=? AND deleted_at IS NULL",
                (int(item_id), user_id),
            ).fetchone()
            if row is None:
                raise LookupError("观察池条目不存在")
            if note is not None:
                connection.execute(
                    "UPDATE watch_item SET status=?, note=?, updated_at=? WHERE id=? AND user_id=?",
                    (status, _text(note, 2000, "备注"), stamp, int(item_id), user_id),
                )
            else:
                connection.execute(
                    "UPDATE watch_item SET status=?, updated_at=? WHERE id=? AND user_id=?",
                    (status, stamp, int(item_id), user_id),
                )
        return self.get(item_id, user_id=user_id)

    def soft_delete(self, item_id: int, *, user_id) -> dict:
        user_id = require_user_id(user_id)
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watch_item WHERE id=? AND user_id=?",
                (int(item_id), user_id),
            ).fetchone()
            if row is None:
                raise LookupError("观察池条目不存在")
            if row["deleted_at"] is None:
                connection.execute(
                    "UPDATE watch_item SET deleted_at=?, updated_at=?, status='dropped' WHERE id=? AND user_id=?",
                    (stamp, stamp, int(item_id), user_id),
                )
            row = connection.execute(
                "SELECT * FROM watch_item WHERE id=? AND user_id=?",
                (int(item_id), user_id),
            ).fetchone()
        return _decode_row(row)

    def save_track_result(self, payload: dict, *, user_id, item_id: int = None) -> dict:
        user_id = require_user_id(user_id)
        code = normalize_code(payload["code"])
        screen_date = _iso_date(payload["screen_date"], "筛出日")
        track_date = _iso_date(payload.get("track_date"), "跟踪日")
        if not track_date:
            raise ValueError("跟踪日不能为空")
        stamp = _now()
        meta = payload.get("meta") or {}
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO track_result
                   (user_id,item_id,code,screen_date,track_date,screen_close,track_close,
                    return_pct,pattern_failed,source_module,meta_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,code,screen_date,track_date) DO UPDATE SET
                     item_id=excluded.item_id,
                     screen_close=excluded.screen_close,
                     track_close=excluded.track_close,
                     return_pct=excluded.return_pct,
                     pattern_failed=excluded.pattern_failed,
                     source_module=excluded.source_module,
                     meta_json=excluded.meta_json""",
                (
                    user_id,
                    item_id,
                    code,
                    screen_date,
                    track_date,
                    payload.get("screen_close"),
                    payload.get("track_close"),
                    payload.get("return_pct"),
                    None if payload.get("pattern_failed") is None else int(bool(payload["pattern_failed"])),
                    _text(payload.get("source_module") or "", 40, "来源模块"),
                    json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                    stamp,
                ),
            )
            row = connection.execute(
                """SELECT * FROM track_result
                   WHERE user_id=? AND code=? AND screen_date=? AND track_date=?""",
                (user_id, code, screen_date, track_date),
            ).fetchone()
        result = dict(row)
        if "meta_json" in result:
            try:
                result["meta"] = json.loads(result.pop("meta_json") or "{}")
            except json.JSONDecodeError:
                result["meta"] = {}
        if result.get("pattern_failed") is not None:
            result["pattern_failed"] = bool(result["pattern_failed"])
        return result

    def list_tracks(
        self,
        *,
        user_id,
        track_date: str = None,
        screen_date: str = None,
        code: str = None,
        limit: int = 200,
    ) -> list:
        user_id = require_user_id(user_id)
        clauses = ["user_id=?"]
        params: list = [user_id]
        if track_date:
            clauses.append("track_date=?")
            params.append(_iso_date(track_date, "跟踪日"))
        if screen_date:
            clauses.append("screen_date=?")
            params.append(_iso_date(screen_date, "筛出日"))
        if code:
            clauses.append("code=?")
            params.append(normalize_code(code))
        try:
            limit = max(1, min(int(limit), 2000))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit 必须是整数") from exc
        where = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM track_result WHERE {where}
                    ORDER BY track_date DESC, return_pct DESC, id DESC LIMIT ?""",
                (*params, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            if "meta_json" in item:
                try:
                    item["meta"] = json.loads(item.pop("meta_json") or "{}")
                except json.JSONDecodeError:
                    item["meta"] = {}
            if item.get("pattern_failed") is not None:
                item["pattern_failed"] = bool(item["pattern_failed"])
            out.append(item)
        return out

    def refresh_tracking(
        self,
        kline_loader,
        *,
        user_id,
        as_of: str = None,
        fail_threshold_pct: float = -3.0,
        active_only: bool = True,
    ) -> dict:
        """对观察池（及可选筛出日）条目计算次日跟踪并落库。

        kline_loader(code) -> DataFrame with 日期/收盘
        """
        user_id = require_user_id(user_id)
        as_of = _iso_date(as_of, "截止日期", allow_empty=True)
        items = self.list_items(user_id=user_id, limit=2000)
        if active_only:
            items = [item for item in items if item["status"] in {"watching", "planned", "bought"}]
        tracked = []
        skipped = 0
        for item in items:
            screen_date = item.get("screen_date")
            if not screen_date:
                skipped += 1
                continue
            try:
                kline = kline_loader(item["code"])
            except Exception:  # noqa: BLE001 — 单票失败不阻断
                skipped += 1
                continue
            result = compute_next_day_return(
                kline,
                code=item["code"],
                screen_date=screen_date,
                fail_threshold_pct=fail_threshold_pct,
            )
            if not result.get("track_date"):
                skipped += 1
                continue
            if as_of and result["track_date"] > as_of:
                skipped += 1
                continue
            saved = self.save_track_result(
                {**result, "source_module": item.get("source_module") or ""},
                user_id=user_id,
                item_id=item["id"],
            )
            tracked.append(saved)
        return {
            "ok": True,
            "tracked": len(tracked),
            "skipped": skipped,
            "items": tracked[:50],
            "message": f"跟踪 {len(tracked)} 条，跳过 {skipped}",
        }
