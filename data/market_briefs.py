"""Persistent, revisioned market briefs for the local research workstation."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "market_briefs.sqlite3"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
KINDS = {"morning", "close_style"}
STATUSES = {"generating", "complete", "partial", "failed"}
MAX_PAYLOAD_BYTES = 512 * 1024


def _iso_date(value: Any, field: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text and optional:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field}必须是 YYYY-MM-DD") from exc


def _clean_text(value: Any, limit: int = 8000) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _clean_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("简报结构嵌套过深")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else None
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        return [_clean_json(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, dict):
        clean = {}
        for key, item in list(value.items())[:200]:
            name = _clean_text(key, 80)
            if name:
                clean[name] = _clean_json(item, depth=depth + 1)
        return clean
    return _clean_text(value)


def _validate_sources(sources: Any) -> list[dict]:
    rows = _clean_json(sources or [])
    if not isinstance(rows, list):
        raise ValueError("sources 必须是数组")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _clean_text(row.get("url"), 1200)
        if url and urlparse(url).scheme not in {"http", "https"}:
            raise ValueError("资讯来源 URL 只允许 http/https")
        result.append({
            "title": _clean_text(row.get("title"), 500),
            "url": url,
            "publisher": _clean_text(row.get("publisher"), 120),
            "published_at": _clean_text(row.get("published_at"), 40),
        })
    return result[:100]


class MarketBriefRepository:
    """Small SQLite repository; page reads never scan market Parquet files."""

    def __init__(self, path: str | Path = DEFAULT_DB_PATH, *, read_only: bool = False):
        self.path = Path(path)
        self.read_only = bool(read_only)
        if not self.read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init_schema()

    @contextmanager
    def _connect(self):
        if self.read_only:
            if not self.path.exists():
                raise LookupError("市场简报库尚未建立")
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            if not self.read_only:
                connection.commit()
        finally:
            connection.close()

    def _init_schema(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_brief (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    brief_date TEXT NOT NULL,
                    market_date TEXT,
                    kind TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    data_as_of TEXT,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE (brief_date, kind, revision),
                    UNIQUE (brief_date, kind, content_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_market_brief_latest
                    ON market_brief (brief_date DESC, kind, revision DESC);
                """
            )

    @staticmethod
    def validate(payload: dict, *, kind: str | None = None) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("简报必须是 JSON 对象")
        result = _clean_json(payload)
        resolved_kind = _clean_text(kind or result.get("kind"), 30)
        if resolved_kind not in KINDS:
            raise ValueError("kind 只允许 morning 或 close_style")
        status = _clean_text(result.get("status") or "complete", 20)
        if status not in STATUSES:
            raise ValueError("简报状态不合法")
        brief_date = _iso_date(result.get("brief_date"), "brief_date")
        market_date = _iso_date(result.get("market_date"), "market_date", optional=True)
        if resolved_kind == "close_style" and not market_date:
            market_date = brief_date
        if market_date and market_date > brief_date:
            raise ValueError("market_date 不能晚于 brief_date")
        result.update({
            "schema_version": "1.0",
            "kind": resolved_kind,
            "brief_date": brief_date,
            "market_date": market_date,
            "status": status,
            "summary": result.get("summary") if isinstance(result.get("summary"), dict) else {},
            "metrics": result.get("metrics") if isinstance(result.get("metrics"), list) else [],
            "sections": result.get("sections") if isinstance(result.get("sections"), dict) else {},
            "charts": result.get("charts") if isinstance(result.get("charts"), dict) else {},
            "sources": _validate_sources(result.get("sources")),
            "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        })
        news = result["sections"].get("news", [])
        if not isinstance(news, list):
            raise ValueError("sections.news 必须是数组")
        if len(news) > 8:
            raise ValueError("sections.news 最多且应为 8 条重要资讯")
        if status == "complete" and len(news) != 8:
            raise ValueError("完整简报必须包含 8 条重要资讯；不足时请标记 partial 并说明原因")
        if resolved_kind == "close_style":
            attribution = result["sections"].get("news_attribution", [])
            review = result["sections"].get("morning_review", [])
            if not isinstance(attribution, list):
                raise ValueError("sections.news_attribution 必须是数组")
            if not isinstance(review, list):
                raise ValueError("sections.morning_review 必须是数组")
            if status == "complete" and not attribution:
                raise ValueError("完整收盘简报必须包含资讯驱动与行情归因")
            if status == "complete" and not review:
                raise ValueError("完整收盘简报必须包含晨间判断复盘")
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise ValueError("简报内容超过 512KB")
        return result

    def publish(self, payload: dict, *, kind: str | None = None) -> dict:
        clean = self.validate(payload, kind=kind)
        canonical = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM market_brief
                   WHERE brief_date=? AND kind=? AND content_hash=?""",
                (clean["brief_date"], clean["kind"], content_hash),
            ).fetchone()
            if existing:
                return self._decode(existing, idempotent=True)
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM market_brief WHERE brief_date=? AND kind=?",
                (clean["brief_date"], clean["kind"]),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO market_brief
                   (brief_date, market_date, kind, revision, status, generated_at,
                    data_as_of, content_hash, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (clean["brief_date"], clean["market_date"], clean["kind"], revision,
                 clean["status"], now, _clean_text(clean.get("data_as_of"), 80),
                 content_hash, canonical),
            )
            row = connection.execute(
                "SELECT * FROM market_brief WHERE brief_date=? AND kind=? AND revision=?",
                (clean["brief_date"], clean["kind"], revision),
            ).fetchone()
        return self._decode(row, idempotent=False)

    @staticmethod
    def _decode(row: sqlite3.Row, *, idempotent: bool = False) -> dict:
        payload = json.loads(row["payload_json"])
        payload.update({
            "revision": row["revision"],
            "generated_at": row["generated_at"],
            "data_as_of": row["data_as_of"] or payload.get("data_as_of"),
            "idempotent": idempotent,
        })
        return payload

    def dates(self, *, limit: int = 180) -> dict:
        limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT brief_date,
                          MAX(CASE WHEN kind='morning' THEN 1 ELSE 0 END) morning,
                          MAX(CASE WHEN kind='close_style' THEN 1 ELSE 0 END) close_style,
                          MAX(generated_at) generated_at
                   FROM market_brief GROUP BY brief_date
                   ORDER BY brief_date DESC LIMIT ?""", (limit,),
            ).fetchall()
        return {"schema_version": "1.0", "dates": [dict(row) for row in rows]}

    def day(self, brief_date: str, *, revision: int | None = None, kind: str | None = None) -> dict:
        target = _iso_date(brief_date, "date")
        if kind is not None and kind not in KINDS:
            raise ValueError("kind 只允许 morning 或 close_style")
        with self._connect() as connection:
            if revision is None:
                kind_clause = " AND kind=?" if kind else ""
                arguments = (target, kind, target) if kind else (target, target)
                rows = connection.execute(
                    f"""SELECT b.* FROM market_brief b JOIN (
                         SELECT kind, MAX(revision) revision FROM market_brief
                         WHERE brief_date=?{kind_clause} GROUP BY kind
                       ) latest ON latest.kind=b.kind AND latest.revision=b.revision
                       WHERE b.brief_date=? ORDER BY b.kind""", arguments,
                ).fetchall()
            else:
                if kind:
                    rows = connection.execute(
                        "SELECT * FROM market_brief WHERE brief_date=? AND kind=? AND revision=?",
                        (target, kind, int(revision)),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM market_brief WHERE brief_date=? AND revision=? ORDER BY kind",
                        (target, int(revision)),
                    ).fetchall()
            revisions = connection.execute(
                """SELECT kind, revision, status, generated_at FROM market_brief
                   WHERE brief_date=? ORDER BY kind, revision DESC""", (target,),
            ).fetchall()
        items = [self._decode(row) for row in rows]
        if not items:
            raise LookupError(f"{target} 暂无市场简报")
        return {
            "schema_version": "1.0", "brief_date": target,
            "items": items, "revisions": [dict(row) for row in revisions],
        }

    def latest(self, kind: str) -> dict:
        if kind not in KINDS:
            raise ValueError("kind 只允许 morning 或 close_style")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM market_brief WHERE kind=?
                   ORDER BY brief_date DESC, revision DESC LIMIT 1""", (kind,),
            ).fetchone()
        if not row:
            raise LookupError("暂无该类型的市场简报")
        return self._decode(row)
