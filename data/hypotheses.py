"""轻量假设生命周期（hypothesis → backtested → training → noted）。

不做成重型项目管理：只按标的记录一条主假设及其状态流转，供标的上下文页读写。
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from data.users import ensure_user_id_column, require_user_id

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "hypotheses.sqlite3"

STATUSES = ("hypothesis", "backtested", "training", "noted")
STATUS_LABELS = {
    "hypothesis": "假设",
    "backtested": "已回测",
    "training": "训练中",
    "noted": "已笔记",
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
        raise ValueError(f"{field_name}不能超过 {limit} 字")
    return text


def normalize_code(code: str) -> str:
    value = str(code or "").strip().lower()
    if not CODE_PATTERN.fullmatch(value):
        raise ValueError("证券代码格式必须类似 sh600519 或 sz000001")
    return value


def _decode_row(row: sqlite3.Row) -> dict:
    item = dict(row)
    if "meta_json" in item:
        try:
            item["meta"] = json.loads(item.pop("meta_json") or "{}")
        except json.JSONDecodeError:
            item["meta"] = {}
    item["status_label"] = STATUS_LABELS.get(item.get("status"), item.get("status"))
    return item


class HypothesisRepository:
    """标的假设状态仓储。同一代码可有多条；列表默认按更新时间倒序。"""

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
                CREATE TABLE IF NOT EXISTS hypothesis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'hypothesis',
                    thesis TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_hypothesis_code
                    ON hypothesis(code, status, deleted_at, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_hypothesis_user_id
                    ON hypothesis(user_id);
                """
            )
            admin_id = 1
            users_db = PROJECT_DIR / "state" / "users.sqlite3"
            if users_db.exists():
                from data.users import UserRepository
                found = UserRepository().get_first_admin_id()
                if found:
                    admin_id = found
            ensure_user_id_column(connection, "hypothesis", admin_id)

    def create(
        self,
        *,
        user_id,
        code: str,
        title: str = "",
        status: str = "hypothesis",
        thesis: str = "",
        note: str = "",
        source: str = "",
        meta: Optional[dict] = None,
    ) -> dict:
        user_id = require_user_id(user_id)
        code = normalize_code(code)
        status = str(status or "hypothesis").strip()
        if status not in STATUSES:
            raise ValueError(f"状态必须是 {', '.join(STATUSES)}")
        title = _text(title or thesis[:40] or f"{code} 假设", 120, "标题", required=True)
        thesis = _text(thesis, 4000, "论点")
        note = _text(note, 4000, "备注")
        source = _text(source, 80, "来源")
        meta = meta or {}
        if not isinstance(meta, dict):
            raise ValueError("meta 必须是对象")
        stamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO hypothesis
                   (user_id,code,title,status,thesis,note,source,meta_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    code,
                    title,
                    status,
                    thesis,
                    note,
                    source,
                    json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                    stamp,
                    stamp,
                ),
            )
            hid = cursor.lastrowid
        return self.get(hid, user_id=user_id)

    def get(self, hypothesis_id: int, *, user_id) -> dict:
        user_id = require_user_id(user_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM hypothesis WHERE id=? AND user_id=? AND deleted_at IS NULL",
                (int(hypothesis_id), user_id),
            ).fetchone()
        if row is None:
            raise LookupError("假设不存在")
        return _decode_row(row)

    def list_for_code(self, code: str, *, user_id, limit: int = 50) -> list:
        user_id = require_user_id(user_id)
        code = normalize_code(code)
        try:
            limit = max(1, min(int(limit), 200))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit 必须是整数") from exc
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM hypothesis
                   WHERE user_id=? AND code=? AND deleted_at IS NULL
                   ORDER BY updated_at DESC, id DESC LIMIT ?""",
                (user_id, code, limit),
            ).fetchall()
        return [_decode_row(row) for row in rows]

    def set_status(
        self,
        hypothesis_id: int,
        status: str,
        *,
        user_id,
        note: str = None,
        thesis: str = None,
        title: str = None,
    ) -> dict:
        user_id = require_user_id(user_id)
        status = str(status or "").strip()
        if status not in STATUSES:
            raise ValueError(f"状态必须是 {', '.join(STATUSES)}")
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM hypothesis WHERE id=? AND user_id=? AND deleted_at IS NULL",
                (int(hypothesis_id), user_id),
            ).fetchone()
            if row is None:
                raise LookupError("假设不存在")
            fields = ["status=?", "updated_at=?"]
            params: list = [status, stamp]
            if note is not None:
                fields.append("note=?")
                params.append(_text(note, 4000, "备注"))
            if thesis is not None:
                fields.append("thesis=?")
                params.append(_text(thesis, 4000, "论点"))
            if title is not None:
                fields.append("title=?")
                params.append(_text(title, 120, "标题", required=True))
            params.extend([int(hypothesis_id), user_id])
            connection.execute(
                f"UPDATE hypothesis SET {', '.join(fields)} WHERE id=? AND user_id=?",
                params,
            )
        return self.get(hypothesis_id, user_id=user_id)

    def soft_delete(self, hypothesis_id: int, *, user_id) -> dict:
        user_id = require_user_id(user_id)
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM hypothesis WHERE id=? AND user_id=? AND deleted_at IS NULL",
                (int(hypothesis_id), user_id),
            ).fetchone()
            if row is None:
                raise LookupError("假设不存在")
            connection.execute(
                "UPDATE hypothesis SET deleted_at=?, updated_at=? WHERE id=? AND user_id=?",
                (stamp, stamp, int(hypothesis_id), user_id),
            )
        return {"id": int(hypothesis_id), "deleted_at": stamp}
