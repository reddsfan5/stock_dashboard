"""每日候选池历史快照。

行情和指标继续保存在 Parquet/缓存中；本库只保存每日候选卡片的完整快照，
保证以后回看某个交易日时，排名、理由、风险和板块信息不会被新数据改写。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "shortlist.sqlite3"


def _iso_date(value, field_name: str = "候选日期") -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD") from exc


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class ShortlistRepository:
    """按交易日保存和读取候选池；同一天重跑时用最新完整快照替换。"""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS shortlist_run (
                    market_date TEXT PRIMARY KEY,
                    generated_at TEXT NOT NULL,
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    limit_count INTEGER NOT NULL DEFAULT 0,
                    meta_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS shortlist_item (
                    market_date TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    sector TEXT NOT NULL DEFAULT '',
                    score REAL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (market_date, code),
                    FOREIGN KEY (market_date) REFERENCES shortlist_run(market_date)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_shortlist_item_date_rank
                    ON shortlist_item(market_date, rank);
                """
            )
            connection.commit()

    def save(self, payload: dict) -> dict:
        """原子保存一个交易日的完整候选池快照。"""
        if not isinstance(payload, dict):
            raise TypeError("候选池必须是字典")
        market_date = _iso_date(payload.get("market_date"))
        generated_at = str(payload.get("generated_at") or _now()).strip()
        cards = list(payload.get("cards") or [])
        if not all(isinstance(card, dict) for card in cards):
            raise ValueError("候选卡片格式不正确")

        # 卡片按页面顺序写入；rank 缺失时补成稳定序号。
        normalized_cards = []
        seen_codes = set()
        for index, source in enumerate(cards, 1):
            card = dict(source)
            code = str(card.get("code") or "").strip().lower()
            if not code:
                raise ValueError(f"第 {index} 个候选缺少证券代码")
            if code in seen_codes:
                raise ValueError(f"候选池存在重复代码: {code}")
            seen_codes.add(code)
            try:
                rank = int(card.get("rank") or index)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"候选 {code} 的排名格式不正确") from exc
            card["code"] = code
            card["rank"] = rank
            normalized_cards.append(card)

        meta = dict(payload)
        meta.pop("cards", None)
        meta["market_date"] = market_date
        meta["generated_at"] = generated_at
        candidate_count = max(0, int(meta.get("candidate_count") or 0))
        limit_count = max(0, int(meta.get("limit") or len(normalized_cards)))
        now = _now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO shortlist_run (
                    market_date, generated_at, candidate_count, selected_count,
                    limit_count, meta_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_date) DO UPDATE SET
                    generated_at=excluded.generated_at,
                    candidate_count=excluded.candidate_count,
                    selected_count=excluded.selected_count,
                    limit_count=excluded.limit_count,
                    meta_json=excluded.meta_json,
                    updated_at=excluded.updated_at
                """,
                (
                    market_date,
                    generated_at,
                    candidate_count,
                    len(normalized_cards),
                    limit_count,
                    _json(meta),
                    now,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM shortlist_item WHERE market_date = ?", (market_date,)
            )
            connection.executemany(
                """
                INSERT INTO shortlist_item (
                    market_date, rank, code, name, sector, score, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        market_date,
                        int(card["rank"]),
                        card["code"],
                        str(card.get("name") or ""),
                        str(card.get("sector") or ""),
                        float(card["score"]) if card.get("score") is not None else None,
                        _json(card),
                    )
                    for card in normalized_cards
                ],
            )
            connection.commit()

        return {
            "market_date": market_date,
            "generated_at": generated_at,
            "selected_count": len(normalized_cards),
            "candidate_count": candidate_count,
        }

    def dates(self, limit: int = 250) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT market_date, generated_at, selected_count, candidate_count
                FROM shortlist_run
                ORDER BY market_date DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, market_date: Optional[str] = None) -> dict:
        requested = _iso_date(market_date) if market_date else None
        with self._connect() as connection:
            if requested:
                run = connection.execute(
                    "SELECT * FROM shortlist_run WHERE market_date = ?", (requested,)
                ).fetchone()
            else:
                run = connection.execute(
                    "SELECT * FROM shortlist_run ORDER BY market_date DESC LIMIT 1"
                ).fetchone()
            if run is None:
                if requested:
                    raise LookupError(f"{requested} 没有候选池记录")
                raise LookupError("候选池历史库为空，请先生成每日候选")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM shortlist_item
                WHERE market_date = ?
                ORDER BY rank ASC, code ASC
                """,
                (run["market_date"],),
            ).fetchall()

        try:
            payload = json.loads(run["meta_json"] or "{}")
            cards = [json.loads(row["payload_json"]) for row in rows]
        except json.JSONDecodeError as exc:
            raise RuntimeError("候选池历史记录损坏") from exc
        payload["market_date"] = run["market_date"]
        payload["generated_at"] = run["generated_at"]
        payload["candidate_count"] = int(run["candidate_count"])
        payload["limit"] = int(run["limit_count"])
        payload["cards"] = cards
        return payload
