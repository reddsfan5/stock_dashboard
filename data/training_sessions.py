"""T+1 训练闭环持久化：日计划、决策快照、心态标记、会话复盘。

行情仍在 Parquet；本库只保存训练过程中的用户决策与复盘元数据。
快照的 context_json 只能包含 as_of 时刻可见信息（由调用方保证）。
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

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "training_sessions.sqlite3"

EMOTION_TAGS = ("自信", "恐惧", "追涨", "犹豫", "后悔", "FOMO", "其他")
MINDSET_TAGS = ("犹豫", "FOMO", "后悔", "自信", "恐惧", "追涨", "其他")
EVENT_TYPES = ("buy", "sell", "cancel", "mindset", "note")
ENTRY_STYLES = ("等待回踩", "突破跟进", "不追涨", "趋势持有", "反弹短做", "其他")
CODE_PATTERN = re.compile(r"^(sh|sz)\d{6}$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso_date(value, field_name: str, *, allow_empty: bool = False) -> Optional[str]:
    text = str(value or "").strip()
    if not text and allow_empty:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是 YYYY-MM-DD") from exc


def _iso_time(value, field_name: str = "时刻") -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name}不能为空")
    if len(text) == 5:
        text += ":00"
    try:
        return datetime.strptime(text, "%H:%M:%S").strftime("%H:%M")
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 HH:MM") from exc


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
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}必须是数字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name}必须是有限数字")
    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name}不能小于 {minimum:g}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name}不能大于 {maximum:g}")
    return number


def truncate_visible_context(
    *,
    market: Optional[dict] = None,
    account: Optional[dict] = None,
    news_items: Optional[list] = None,
    market_context: Optional[dict] = None,
    max_news: int = 8,
) -> dict:
    """组装可持久化的防剧透上下文；只保留摘要字段。"""
    market = market or {}
    account = account or {}
    indicators = market.get("indicators") or {}
    news_summary = []
    for item in (news_items or [])[-max_news:]:
        news_summary.append({
            "time": str(item.get("time") or "")[:8],
            "title": str(item.get("title") or "")[:120],
            "tags": list(item.get("tags") or [])[:3],
        })
    a_share = []
    for card in (market_context or {}).get("a_share") or []:
        a_share.append({
            "code": card.get("code"),
            "name": card.get("name"),
            "change_pct": card.get("change_pct"),
            "price": card.get("price"),
            "source": card.get("source"),
        })
    overseas = []
    for card in ((market_context or {}).get("overseas") or [])[:6]:
        overseas.append({
            "code": card.get("code"),
            "name": card.get("name"),
            "change_pct": card.get("change_pct"),
            "status": card.get("status"),
            "bar_date": card.get("bar_date"),
        })
    payload = {
        "price": market.get("price"),
        "change_pct": market.get("change_pct"),
        "indicators": {
            key: indicators.get(key)
            for key in (
                "intraday_volume_ratio", "vwap_deviation_pct", "momentum_20_pct",
                "atr14_pct", "volatility_20_ann_pct", "drawdown_from_high_60_pct",
            )
            if key in indicators
        },
        "account": {
            key: account.get(key)
            for key in (
                "available_cash", "total_shares", "available_shares",
                "equity", "total_pnl", "return_pct", "average_cost",
            )
            if key in account
        },
        "news_summary": news_summary,
        "a_share": a_share,
        "overseas": overseas,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(text) > 12_000:
        payload["news_summary"] = payload["news_summary"][-4:]
        payload["overseas"] = payload["overseas"][:3]
    return payload


def check_plan_violations(plan: Optional[dict], decisions: list, *, day_return_pct=None) -> list:
    """检查当日决策是否违反事先日计划。返回结构化违规列表。"""
    if not plan:
        return []
    violations = []
    max_loss_pct = plan.get("max_loss_pct")
    if (
        max_loss_pct is not None
        and day_return_pct is not None
        and math.isfinite(float(day_return_pct))
        and float(day_return_pct) < -abs(float(max_loss_pct))
    ):
        violations.append({
            "code": "max_loss_pct",
            "label": "超过计划最大亏损",
            "detail": (
                f"当日收益 {float(day_return_pct):.2f}% "
                f"低于计划最大亏损 {float(max_loss_pct):.2f}%"
            ),
        })
    style = str(plan.get("entry_style") or "")
    chase_emotions = {"追涨", "FOMO"}
    if "不追涨" in style:
        for item in decisions:
            if item.get("event_type") == "buy" and item.get("emotion") in chase_emotions:
                violations.append({
                    "code": "no_chase",
                    "label": "违反「不追涨」入场风格",
                    "detail": (
                        f"{item.get('market_date')} {item.get('as_of')} "
                        f"买入情绪为 {item.get('emotion')}"
                    ),
                    "decision_id": item.get("id"),
                })
    if "等待回踩" in style:
        for item in decisions:
            if item.get("event_type") == "buy" and item.get("emotion") in chase_emotions:
                violations.append({
                    "code": "wait_pullback",
                    "label": "计划等待回踩却追涨买入",
                    "detail": (
                        f"{item.get('market_date')} {item.get('as_of')} "
                        f"情绪 {item.get('emotion')}"
                    ),
                    "decision_id": item.get("id"),
                })
    return violations


def _decode_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ("meta_json", "context_json"):
        if key in result:
            target = "meta" if key == "meta_json" else "context"
            try:
                result[target] = json.loads(result.pop(key) or "{}")
            except json.JSONDecodeError:
                result[target] = {}
    return result


class TrainingSessionRepository:
    """训练闭环 SQLite 仓储。"""

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
                CREATE TABLE IF NOT EXISTS training_run (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_token TEXT NOT NULL UNIQUE,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL,
                    capital REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ended_at TEXT,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_training_run_code
                    ON training_run(code, created_at DESC);

                CREATE TABLE IF NOT EXISTS day_plan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES training_run(id),
                    market_date TEXT NOT NULL,
                    thesis TEXT NOT NULL DEFAULT '',
                    max_loss_pct REAL,
                    max_loss_amount REAL,
                    entry_style TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_day_plan_run_date
                    ON day_plan(run_id, market_date);

                CREATE TABLE IF NOT EXISTS decision_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES training_run(id),
                    event_type TEXT NOT NULL,
                    market_date TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    side TEXT,
                    shares INTEGER,
                    price REAL,
                    order_ref TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    emotion TEXT NOT NULL DEFAULT '',
                    planned_stop REAL,
                    planned_target REAL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_decision_run_date
                    ON decision_snapshot(run_id, market_date, as_of, id);

                CREATE TABLE IF NOT EXISTS mindset_marker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL REFERENCES training_run(id),
                    market_date TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mindset_run_date
                    ON mindset_marker(run_id, market_date, as_of);
                """
            )

    @staticmethod
    def validate_code(code: str) -> str:
        value = str(code or "").strip().lower()
        if not CODE_PATTERN.fullmatch(value):
            raise ValueError("证券代码格式必须类似 sh600519 或 sz000001")
        return value

    def create_run(
        self,
        *,
        session_token: str,
        code: str,
        name: str = "",
        start_date: str,
        capital: float,
        meta: Optional[dict] = None,
    ) -> dict:
        token = _text(session_token, 80, "会话令牌", required=True)
        code = self.validate_code(code)
        name = _text(name, 80, "名称")
        start_date = _iso_date(start_date, "起始交易日")
        capital = _optional_number(capital, "初始资金", minimum=1)
        meta = meta or {}
        if not isinstance(meta, dict):
            raise ValueError("meta 必须是对象")
        stamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO training_run
                   (session_token,code,name,start_date,capital,status,meta_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    token, code, name, start_date, capital, "active",
                    json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                    stamp, stamp,
                ),
            )
            run_id = cursor.lastrowid
        return self.get_run(run_id)

    def get_run(self, run_id: int = None, *, session_token: str = None) -> dict:
        with self._connect() as connection:
            if session_token:
                row = connection.execute(
                    "SELECT * FROM training_run WHERE session_token=? AND deleted_at IS NULL",
                    (_text(session_token, 80, "会话令牌", required=True),),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM training_run WHERE id=? AND deleted_at IS NULL",
                    (int(run_id),),
                ).fetchone()
        if row is None:
            raise LookupError("训练记录不存在")
        return _decode_row(row)

    def list_runs_for_code(self, code: str, *, limit: int = 20) -> list:
        """按代码返回近期训练会话摘要（含决策数）。"""
        code = self.validate_code(code)
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit 必须是整数") from exc
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.*,
                          (SELECT COUNT(*) FROM decision_snapshot d
                           WHERE d.run_id=r.id AND d.deleted_at IS NULL) AS decision_count,
                          (SELECT COUNT(*) FROM day_plan p
                           WHERE p.run_id=r.id AND p.deleted_at IS NULL) AS plan_count
                   FROM training_run r
                   WHERE r.code=? AND r.deleted_at IS NULL
                   ORDER BY r.updated_at DESC, r.id DESC
                   LIMIT ?""",
                (code, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = _decode_row(row)
            item["decision_count"] = int(item.get("decision_count") or 0)
            item["plan_count"] = int(item.get("plan_count") or 0)
            out.append(item)
        return out

    def touch_run(self, run_id: int, *, status: str = None, ended: bool = False) -> dict:
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,status FROM training_run WHERE id=? AND deleted_at IS NULL",
                (int(run_id),),
            ).fetchone()
            if row is None:
                raise LookupError("训练记录不存在")
            new_status = status or row["status"]
            ended_at = stamp if ended else None
            if ended and not status:
                new_status = "ended"
            connection.execute(
                "UPDATE training_run SET status=?,updated_at=?,ended_at=COALESCE(?,ended_at) WHERE id=?",
                (new_status, stamp, ended_at, int(run_id)),
            )
        return self.get_run(run_id)

    def upsert_day_plan(
        self,
        *,
        run_id: int,
        market_date: str,
        thesis: str = "",
        max_loss_pct=None,
        max_loss_amount=None,
        entry_style: str = "",
        notes: str = "",
    ) -> dict:
        run_id = int(run_id)
        market_date = _iso_date(market_date, "交易日")
        thesis = _text(thesis, 2000, "当日论点")
        notes = _text(notes, 2000, "备注")
        entry_style = _text(entry_style, 40, "入场风格")
        max_loss_pct = _optional_number(max_loss_pct, "最大亏损%", minimum=0, maximum=100)
        max_loss_amount = _optional_number(max_loss_amount, "最大亏损金额", minimum=0)
        stamp = _now()
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT id FROM day_plan
                   WHERE run_id=? AND market_date=? AND deleted_at IS NULL
                   ORDER BY id DESC LIMIT 1""",
                (run_id, market_date),
            ).fetchone()
            if existing:
                connection.execute(
                    """UPDATE day_plan SET thesis=?,max_loss_pct=?,max_loss_amount=?,
                       entry_style=?,notes=?,updated_at=? WHERE id=?""",
                    (
                        thesis, max_loss_pct, max_loss_amount, entry_style,
                        notes, stamp, existing["id"],
                    ),
                )
                plan_id = existing["id"]
            else:
                cursor = connection.execute(
                    """INSERT INTO day_plan
                       (run_id,market_date,thesis,max_loss_pct,max_loss_amount,
                        entry_style,notes,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id, market_date, thesis, max_loss_pct, max_loss_amount,
                        entry_style, notes, stamp, stamp,
                    ),
                )
                plan_id = cursor.lastrowid
            connection.execute(
                "UPDATE training_run SET updated_at=? WHERE id=?", (stamp, run_id)
            )
        return self.get_day_plan(plan_id)

    def get_day_plan(self, plan_id: int = None, *, run_id: int = None, market_date: str = None) -> Optional[dict]:
        with self._connect() as connection:
            if plan_id is not None:
                row = connection.execute(
                    "SELECT * FROM day_plan WHERE id=? AND deleted_at IS NULL",
                    (int(plan_id),),
                ).fetchone()
            else:
                market_date = _iso_date(market_date, "交易日")
                row = connection.execute(
                    """SELECT * FROM day_plan
                       WHERE run_id=? AND market_date=? AND deleted_at IS NULL
                       ORDER BY id DESC LIMIT 1""",
                    (int(run_id), market_date),
                ).fetchone()
        return _decode_row(row) if row else None

    def list_day_plans(self, run_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM day_plan WHERE run_id=? AND deleted_at IS NULL
                   ORDER BY market_date,id""",
                (int(run_id),),
            ).fetchall()
        return [_decode_row(row) for row in rows]

    def add_decision(
        self,
        *,
        run_id: int,
        event_type: str,
        market_date: str,
        as_of: str,
        reason: str = "",
        emotion: str = "",
        planned_stop=None,
        planned_target=None,
        side: str = None,
        shares=None,
        price=None,
        order_ref: str = "",
        context: Optional[dict] = None,
        require_reason: bool = True,
    ) -> dict:
        run_id = int(run_id)
        event_type = str(event_type or "").strip()
        if event_type not in EVENT_TYPES:
            raise ValueError("未知的决策事件类型")
        market_date = _iso_date(market_date, "交易日")
        as_of = _iso_time(as_of)
        reason = _text(reason, 4000, "交易理由", required=require_reason and event_type in {"buy", "sell"})
        emotion = _text(emotion, 20, "情绪标签")
        if emotion and emotion not in EMOTION_TAGS:
            raise ValueError("情绪标签不在允许列表中")
        if event_type in {"buy", "sell"} and not emotion:
            # 强烈提示：允许空，但调用方 UI 应优先要求
            emotion = ""
        planned_stop = _optional_number(planned_stop, "计划止损", minimum=0)
        planned_target = _optional_number(planned_target, "计划目标", minimum=0)
        if shares not in (None, ""):
            try:
                shares = int(shares)
            except (TypeError, ValueError) as exc:
                raise ValueError("数量必须是整数") from exc
        else:
            shares = None
        price = _optional_number(price, "价格", minimum=0)
        order_ref = _text(order_ref, 40, "委托引用")
        side = _text(side, 10, "方向") if side else None
        context = context or {}
        if not isinstance(context, dict):
            raise ValueError("上下文必须是对象")
        context_text = json.dumps(
            context, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        if len(context_text) > 20_000:
            raise ValueError("决策上下文过大")
        stamp = _now()
        with self._connect() as connection:
            if connection.execute(
                "SELECT id FROM training_run WHERE id=? AND deleted_at IS NULL", (run_id,)
            ).fetchone() is None:
                raise LookupError("训练记录不存在")
            cursor = connection.execute(
                """INSERT INTO decision_snapshot
                   (run_id,event_type,market_date,as_of,side,shares,price,order_ref,
                    reason,emotion,planned_stop,planned_target,context_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, event_type, market_date, as_of, side, shares, price, order_ref,
                    reason, emotion, planned_stop, planned_target, context_text, stamp,
                ),
            )
            connection.execute(
                "UPDATE training_run SET updated_at=? WHERE id=?", (stamp, run_id)
            )
            decision_id = cursor.lastrowid
        return self.get_decision(decision_id)

    def get_decision(self, decision_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decision_snapshot WHERE id=?", (int(decision_id),)
            ).fetchone()
        if row is None:
            raise LookupError("决策快照不存在")
        return _decode_row(row)

    def list_decisions(self, run_id: int, *, market_date: str = None, include_deleted=False) -> list:
        clauses = ["run_id=?"]
        params = [int(run_id)]
        if market_date:
            clauses.append("market_date=?")
            params.append(_iso_date(market_date, "交易日"))
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        where = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM decision_snapshot WHERE {where}
                    ORDER BY market_date,as_of,id""",
                params,
            ).fetchall()
        return [_decode_row(row) for row in rows]

    def soft_delete_decision(self, decision_id: int) -> dict:
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,deleted_at FROM decision_snapshot WHERE id=?",
                (int(decision_id),),
            ).fetchone()
            if row is None:
                raise LookupError("决策快照不存在")
            if row["deleted_at"] is None:
                connection.execute(
                    "UPDATE decision_snapshot SET deleted_at=? WHERE id=?",
                    (stamp, int(decision_id)),
                )
        return self.get_decision(decision_id)

    def add_mindset_marker(
        self, *, run_id: int, market_date: str, as_of: str, tag: str, note: str = ""
    ) -> dict:
        run_id = int(run_id)
        market_date = _iso_date(market_date, "交易日")
        as_of = _iso_time(as_of)
        tag = _text(tag, 20, "心态标签", required=True)
        if tag not in MINDSET_TAGS:
            raise ValueError("心态标签不在允许列表中")
        note = _text(note, 500, "备注")
        stamp = _now()
        with self._connect() as connection:
            if connection.execute(
                "SELECT id FROM training_run WHERE id=? AND deleted_at IS NULL", (run_id,)
            ).fetchone() is None:
                raise LookupError("训练记录不存在")
            cursor = connection.execute(
                """INSERT INTO mindset_marker
                   (run_id,market_date,as_of,tag,note,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (run_id, market_date, as_of, tag, note, stamp),
            )
            marker_id = cursor.lastrowid
        return self.get_mindset_marker(marker_id)

    def get_mindset_marker(self, marker_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mindset_marker WHERE id=?", (int(marker_id),)
            ).fetchone()
        if row is None:
            raise LookupError("心态标记不存在")
        return dict(row)

    def list_mindset_markers(self, run_id: int, *, market_date: str = None) -> list:
        clauses = ["run_id=?", "deleted_at IS NULL"]
        params = [int(run_id)]
        if market_date:
            clauses.append("market_date=?")
            params.append(_iso_date(market_date, "交易日"))
        where = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM mindset_marker WHERE {where}
                    ORDER BY market_date,as_of,id""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def soft_delete_mindset_marker(self, marker_id: int) -> dict:
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id,deleted_at FROM mindset_marker WHERE id=?",
                (int(marker_id),),
            ).fetchone()
            if row is None:
                raise LookupError("心态标记不存在")
            if row["deleted_at"] is None:
                connection.execute(
                    "UPDATE mindset_marker SET deleted_at=? WHERE id=?",
                    (stamp, int(marker_id)),
                )
        return self.get_mindset_marker(marker_id)

    def build_review(
        self,
        run_id: int,
        *,
        market_date: str = None,
        account: Optional[dict] = None,
        orders: Optional[list] = None,
    ) -> dict:
        """组装会话/当日复盘：盈亏归因、计划遵守、情绪时间线。"""
        run = self.get_run(run_id)
        decisions = self.list_decisions(run_id, market_date=market_date)
        markers = self.list_mindset_markers(run_id, market_date=market_date)
        plans = self.list_day_plans(run_id)
        if market_date:
            plans = [plan for plan in plans if plan["market_date"] == market_date]
        account = account or {}
        orders = orders or []
        if market_date:
            orders = [order for order in orders if order.get("date") == market_date]

        realized = sum(
            float(order.get("realized_pnl") or 0)
            for order in orders
            if order.get("side") == "sell" and order.get("filled_shares")
        )
        fees = sum(
            float(order.get("commission") or 0) + float(order.get("stamp_tax") or 0)
            for order in orders
            if order.get("filled_shares")
        )
        buy_notional = sum(
            float(order.get("fill_price") or 0) * int(order.get("filled_shares") or 0)
            for order in orders
            if order.get("side") == "buy" and order.get("filled_shares")
        )
        sell_notional = sum(
            float(order.get("fill_price") or 0) * int(order.get("filled_shares") or 0)
            for order in orders
            if order.get("side") == "sell" and order.get("filled_shares")
        )
        day_return_pct = account.get("return_pct")
        plan = plans[-1] if plans else None
        violations = check_plan_violations(plan, decisions, day_return_pct=day_return_pct)
        emotion_timeline = [
            {
                "id": item["id"],
                "market_date": item["market_date"],
                "as_of": item["as_of"],
                "event_type": item["event_type"],
                "emotion": item.get("emotion") or "",
                "reason": (item.get("reason") or "")[:120],
                "side": item.get("side"),
                "price": item.get("price"),
            }
            for item in decisions
            if item.get("emotion") or item.get("event_type") == "mindset"
        ]
        for marker in markers:
            emotion_timeline.append({
                "id": f"m{marker['id']}",
                "market_date": marker["market_date"],
                "as_of": marker["as_of"],
                "event_type": "mindset",
                "emotion": marker["tag"],
                "reason": marker.get("note") or "",
                "side": None,
                "price": None,
            })
        emotion_timeline.sort(key=lambda row: (row["market_date"], row["as_of"], str(row["id"])))

        linked_news = []
        linked_market = []
        for item in decisions:
            context = item.get("context") or {}
            for news in context.get("news_summary") or []:
                linked_news.append({
                    "decision_id": item["id"],
                    "as_of": item["as_of"],
                    **news,
                })
            if context.get("a_share"):
                linked_market.append({
                    "decision_id": item["id"],
                    "as_of": item["as_of"],
                    "a_share": context.get("a_share"),
                    "overseas": context.get("overseas") or [],
                })

        return {
            "run": run,
            "market_date": market_date,
            "plan": plan,
            "plans": plans,
            "pnl_attribution": {
                "realized_pnl": round(realized, 2),
                "unrealized_pnl": account.get("unrealized_pnl"),
                "total_pnl": account.get("total_pnl"),
                "return_pct": account.get("return_pct"),
                "fees": round(fees, 2),
                "buy_notional": round(buy_notional, 2),
                "sell_notional": round(sell_notional, 2),
                "fills": sum(1 for order in orders if order.get("filled_shares")),
            },
            "plan_adherence": {
                "has_plan": plan is not None,
                "violations": violations,
                "ok": not violations,
            },
            "emotion_timeline": emotion_timeline,
            "decisions": decisions,
            "mindset_markers": markers,
            "linked_news": linked_news[-40:],
            "linked_market": linked_market[-20:],
        }
