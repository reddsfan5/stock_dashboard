"""选股日记的 SQLite 持久化层。

行情仍保存在 Parquet；这里仅保存用户不可再生的研究案例与决策事件。
日记采用追加式事件，避免后来直接覆盖当时的判断。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "stock_journal.sqlite3"

CASE_STATUSES = {
    "watching", "planned", "holding", "exit_planned", "closed", "invalidated",
}
EVENT_TYPES = {
    "watch", "entry_plan", "entry", "hold", "exit_plan", "exit",
    "invalidated", "review", "amendment",
}
EVENT_STATUS = {
    "watch": "watching",
    "entry_plan": "planned",
    "entry": "holding",
    "hold": "holding",
    "exit_plan": "exit_planned",
    "exit": "closed",
    "invalidated": "invalidated",
}
CODE_PATTERN = re.compile(r"^(sh|sz)\d{6}$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso_date(value, field_name: str, *, allow_empty: bool = False) -> Optional[str]:
    text = str(value or "").strip()
    if not text and allow_empty:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD")


def _text(value, limit: int, field_name: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field_name}不能为空")
    if len(text) > limit:
        raise ValueError(f"{field_name}不能超过 {limit} 个字符")
    return text


def _optional_number(value, field_name: str, minimum=None, maximum=None):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name}必须是数字")
    if not math.isfinite(number):
        raise ValueError(f"{field_name}必须是有限数字")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name}不能小于 {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name}不能大于 {maximum:g}")
    return number


def _tags(value) -> list[str]:
    if isinstance(value, str):
        values: Iterable = re.split(r"[,，\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = value
    elif value in (None, ""):
        values = []
    else:
        raise ValueError("标签必须是字符串或列表")
    result = []
    for item in values:
        tag = str(item).strip()
        if not tag or tag in result:
            continue
        if len(tag) > 30:
            raise ValueError("单个标签不能超过30个字符")
        result.append(tag)
    if len(result) > 20:
        raise ValueError("单条记录最多20个标签")
    return result


def _decode_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ("tags_json", "context_json"):
        if key in result:
            target = "tags" if key == "tags_json" else "context"
            try:
                result[target] = json.loads(result.pop(key) or ("[]" if target == "tags" else "{}"))
            except json.JSONDecodeError:
                result[target] = [] if target == "tags" else {}
    return result


class JournalRepository:
    """线程安全用法的轻量仓储；每次操作使用独立 SQLite 连接。"""

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
                CREATE TABLE IF NOT EXISTS journal_case (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    thesis TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'manual',
                    opened_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_journal_case_code_status
                    ON journal_case(code, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_journal_case_updated
                    ON journal_case(updated_at DESC);

                CREATE TABLE IF NOT EXISTS journal_entry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id INTEGER NOT NULL REFERENCES journal_case(id),
                    market_date TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    price REAL,
                    reason TEXT NOT NULL,
                    trigger_condition TEXT NOT NULL DEFAULT '',
                    invalidation_condition TEXT NOT NULL DEFAULT '',
                    target_price REAL,
                    stop_price REAL,
                    planned_position_pct REAL,
                    planned_holding_days INTEGER,
                    next_review_date TEXT,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT NOT NULL DEFAULT 'manual',
                    context_json TEXT NOT NULL DEFAULT '{}',
                    supersedes_entry_id INTEGER REFERENCES journal_entry(id),
                    deleted_at TEXT,
                    deleted_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_journal_entry_case_date
                    ON journal_entry(case_id, market_date, id);
                CREATE INDEX IF NOT EXISTS idx_journal_entry_review
                    ON journal_entry(next_review_date, event_type);
                """
            )
            # 旧版个人数据库原地升级，不需要重建日记。
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(journal_entry)")
            }
            if "deleted_at" not in columns:
                connection.execute("ALTER TABLE journal_entry ADD COLUMN deleted_at TEXT")
            if "deleted_reason" not in columns:
                connection.execute(
                    "ALTER TABLE journal_entry ADD COLUMN deleted_reason TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def validate_code(code: str) -> str:
        value = str(code or "").strip().lower()
        if not CODE_PATTERN.fullmatch(value):
            raise ValueError("证券代码格式必须类似 sh600519 或 sz000001")
        return value

    def create_case(self, *, code, title, thesis="", tags=None, source="manual") -> dict:
        code = self.validate_code(code)
        title = _text(title, 120, "案例标题", required=True)
        thesis = _text(thesis, 2000, "核心逻辑")
        source = _text(source, 40, "来源") or "manual"
        tag_values = _tags(tags)
        timestamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO journal_case
                   (code,title,thesis,status,tags_json,source,opened_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (code, title, thesis, "watching", json.dumps(tag_values, ensure_ascii=False),
                 source, timestamp, timestamp),
            )
            case_id = cursor.lastrowid
        return self.get_case(case_id, include_entries=False)

    def add_entry(self, *, case_id, event_type, market_date, reason, **values) -> dict:
        try:
            case_id = int(case_id)
        except (TypeError, ValueError):
            raise ValueError("case_id 必须是整数")
        event_type = str(event_type or "").strip()
        if event_type not in EVENT_TYPES:
            raise ValueError("未知的日记事件类型")
        market_date = _iso_date(market_date, "行情日期")
        reason = _text(reason, 4000, "决策原因", required=True)
        trigger = _text(values.get("trigger_condition"), 1000, "触发条件")
        invalidation = _text(values.get("invalidation_condition"), 1000, "失效条件")
        source = _text(values.get("source", "manual"), 40, "来源") or "manual"
        price = _optional_number(values.get("price"), "当时价格", minimum=0)
        target = _optional_number(values.get("target_price"), "目标价格", minimum=0)
        stop = _optional_number(values.get("stop_price"), "止损价格", minimum=0)
        position = _optional_number(
            values.get("planned_position_pct"), "计划仓位", minimum=0, maximum=100
        )
        holding = _optional_number(
            values.get("planned_holding_days"), "计划持有天数", minimum=1, maximum=3650
        )
        if holding is not None and not holding.is_integer():
            raise ValueError("计划持有天数必须是整数")
        holding = int(holding) if holding is not None else None
        review_date = _iso_date(
            values.get("next_review_date"), "下次复查日期", allow_empty=True
        )
        tag_values = _tags(values.get("tags"))
        context = values.get("context") or {}
        if not isinstance(context, dict):
            raise ValueError("行情上下文必须是对象")
        context_text = json.dumps(
            context, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        if len(context_text) > 20_000:
            raise ValueError("行情上下文过大")
        supersedes = values.get("supersedes_entry_id")
        if supersedes not in (None, ""):
            try:
                supersedes = int(supersedes)
            except (TypeError, ValueError):
                raise ValueError("被补充记录编号必须是整数")
        else:
            supersedes = None
        timestamp = _now()
        with self._connect() as connection:
            case = connection.execute(
                "SELECT id,status FROM journal_case WHERE id=?", (case_id,)
            ).fetchone()
            if case is None:
                raise LookupError("研究案例不存在")
            if supersedes is not None:
                target_row = connection.execute(
                    "SELECT id FROM journal_entry WHERE id=? AND case_id=?",
                    (supersedes, case_id),
                ).fetchone()
                if target_row is None:
                    raise ValueError("被补充记录不属于当前案例")
            cursor = connection.execute(
                """INSERT INTO journal_entry
                   (case_id,market_date,recorded_at,event_type,price,reason,
                    trigger_condition,invalidation_condition,target_price,stop_price,
                    planned_position_pct,planned_holding_days,next_review_date,
                    tags_json,source,context_json,supersedes_entry_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    case_id, market_date, timestamp, event_type, price, reason,
                    trigger, invalidation, target, stop, position, holding, review_date,
                    json.dumps(tag_values, ensure_ascii=False), source, context_text, supersedes,
                ),
            )
            new_status = EVENT_STATUS.get(event_type, case["status"])
            closed_at = timestamp if new_status in {"closed", "invalidated"} else None
            connection.execute(
                "UPDATE journal_case SET status=?,updated_at=?,closed_at=? WHERE id=?",
                (new_status, timestamp, closed_at, case_id),
            )
            entry_id = cursor.lastrowid
        return self.get_entry(entry_id)

    def get_entry(self, entry_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM journal_entry WHERE id=?", (int(entry_id),)
            ).fetchone()
        if row is None:
            raise LookupError("日记记录不存在")
        return _decode_row(row)

    def get_case(
        self, case_id: int, *, include_entries: bool = True, include_deleted: bool = False
    ) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM journal_case WHERE id=?", (int(case_id),)
            ).fetchone()
            if row is None:
                raise LookupError("研究案例不存在")
            result = _decode_row(row)
            if include_entries:
                deleted_clause = "" if include_deleted else " AND deleted_at IS NULL"
                entries = connection.execute(
                    "SELECT * FROM journal_entry WHERE case_id=?" + deleted_clause
                    + " ORDER BY market_date,id",
                    (int(case_id),),
                ).fetchall()
                result["entries"] = [_decode_row(entry) for entry in entries]
        return result

    def list_cases(self, *, code=None, status=None, query=None, limit=200) -> list[dict]:
        clauses = []
        params = []
        if code:
            clauses.append("c.code=?")
            params.append(self.validate_code(code))
        if status:
            status = str(status).strip()
            if status not in CASE_STATUSES:
                raise ValueError("未知的案例状态")
            clauses.append("c.status=?")
            params.append(status)
        query = str(query or "").strip()
        if query:
            clauses.append(
                "(c.code LIKE ? OR c.title LIKE ? OR c.thesis LIKE ? OR "
                "EXISTS (SELECT 1 FROM journal_entry e2 WHERE e2.case_id=c.id "
                "AND e2.deleted_at IS NULL "
                "AND (e2.reason LIKE ? OR e2.tags_json LIKE ?)))"
            )
            token = f"%{query}%"
            params.extend([token] * 5)
        try:
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            limit = 200
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"""
            SELECT c.*,
                   COUNT(e.id) AS entry_count,
                   MAX(e.market_date) AS latest_market_date,
                   (SELECT e3.event_type FROM journal_entry e3
                    WHERE e3.case_id=c.id AND e3.deleted_at IS NULL
                    ORDER BY e3.market_date DESC,e3.id DESC LIMIT 1)
                    AS latest_event_type,
                   (SELECT e4.reason FROM journal_entry e4
                    WHERE e4.case_id=c.id AND e4.deleted_at IS NULL
                    ORDER BY e4.market_date DESC,e4.id DESC LIMIT 1)
                    AS latest_reason
            FROM journal_case c
            LEFT JOIN journal_entry e ON e.case_id=c.id AND e.deleted_at IS NULL
            {where}
            GROUP BY c.id
            ORDER BY c.updated_at DESC,c.id DESC
            LIMIT ?
        """
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_decode_row(row) for row in rows]

    def list_entries(
        self, *, code=None, case_id=None, include_deleted=False, limit=2000
    ) -> list[dict]:
        clauses = []
        params = []
        if code:
            clauses.append("c.code=?")
            params.append(self.validate_code(code))
        if case_id not in (None, ""):
            try:
                case_id = int(case_id)
            except (TypeError, ValueError):
                raise ValueError("case_id 必须是整数")
            clauses.append("e.case_id=?")
            params.append(case_id)
        if not include_deleted:
            clauses.append("e.deleted_at IS NULL")
        try:
            limit = max(1, min(int(limit), 5000))
        except (TypeError, ValueError):
            limit = 2000
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT e.*,c.code,c.title AS case_title,c.status AS case_status
                    FROM journal_entry e JOIN journal_case c ON c.id=e.case_id
                    {where}
                    ORDER BY e.market_date,e.id LIMIT ?""",
                params,
            ).fetchall()
        return [_decode_row(row) for row in rows]

    @staticmethod
    def _refresh_case_status(connection, case_id: int, timestamp: str):
        """删除/恢复后按最后一条有效决策重算案例状态。"""
        latest = connection.execute(
            """SELECT event_type,recorded_at FROM journal_entry
               WHERE case_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 1""",
            (case_id,),
        ).fetchone()
        status = EVENT_STATUS.get(latest["event_type"], "watching") if latest else "watching"
        closed_at = (
            latest["recorded_at"] if latest and status in {"closed", "invalidated"} else None
        )
        connection.execute(
            "UPDATE journal_case SET status=?,updated_at=?,closed_at=? WHERE id=?",
            (status, timestamp, closed_at, case_id),
        )

    def soft_delete_entry(self, entry_id, reason="") -> dict:
        """移入回收站；保留原文、行情快照和操作时间。"""
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            raise ValueError("日记记录编号必须是整数")
        reason = _text(reason, 500, "删除原因")
        timestamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,case_id,deleted_at FROM journal_entry WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None:
                raise LookupError("日记记录不存在")
            if row["deleted_at"] is None:
                connection.execute(
                    "UPDATE journal_entry SET deleted_at=?,deleted_reason=? WHERE id=?",
                    (timestamp, reason, entry_id),
                )
                self._refresh_case_status(connection, row["case_id"], timestamp)
        return self.get_entry(entry_id)

    def restore_entry(self, entry_id) -> dict:
        """从回收站恢复记录，并重算案例状态。"""
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            raise ValueError("日记记录编号必须是整数")
        timestamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,case_id,deleted_at FROM journal_entry WHERE id=?", (entry_id,)
            ).fetchone()
            if row is None:
                raise LookupError("日记记录不存在")
            if row["deleted_at"] is not None:
                connection.execute(
                    "UPDATE journal_entry SET deleted_at=NULL,deleted_reason='' WHERE id=?",
                    (entry_id,),
                )
                self._refresh_case_status(connection, row["case_id"], timestamp)
        return self.get_entry(entry_id)

    def due_reviews(self, *, as_of=None, limit=100) -> list[dict]:
        as_of = _iso_date(as_of or date.today().isoformat(), "复查截止日期")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.*,c.code,c.title,c.status
                   FROM journal_entry e JOIN journal_case c ON c.id=e.case_id
                   WHERE e.next_review_date IS NOT NULL AND e.next_review_date<=?
                     AND e.deleted_at IS NULL
                     AND c.status NOT IN ('closed','invalidated')
                     AND e.id=(SELECT MAX(e2.id) FROM journal_entry e2
                              WHERE e2.case_id=e.case_id AND e2.deleted_at IS NULL)
                   ORDER BY e.next_review_date,e.id LIMIT ?""",
                (as_of, max(1, min(int(limit), 500))),
            ).fetchall()
        return [_decode_row(row) for row in rows]

    def backup(self, target_dir=None) -> Path:
        target_dir = Path(target_dir or self.path.parent / "backups")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"stock_journal_{datetime.now():%Y%m%d_%H%M%S}.sqlite3"
        # SQLite backup API 能在 WAL 写入期间生成一致快照。
        source = sqlite3.connect(self.path, timeout=10)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return target
