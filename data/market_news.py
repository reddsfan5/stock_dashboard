"""市场级资讯缓存与决策影响记录。

只保存同花顺公开 7x24 重要资讯的标题、摘要、发布时间和原文链接，不复制全文。
所有查询都支持 ``as_of`` 截止时刻，供无剧透交易训练使用。
"""

from __future__ import annotations

from data.users import ensure_user_id_column, require_user_id
import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "state" / "market_news.sqlite3"
THS_IMPORTANT_NEWS_URL = "https://news.10jqka.com.cn/tapp/news/push/stock"
THS_PUBLIC_PAGE_URL = "https://news.10jqka.com.cn/realtimenews.html"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

PHASES = ("pre_open", "midday", "close")
PHASE_LABELS = {
    "pre_open": "早盘",
    "midday": "午间",
    "close": "收盘",
}
ACTIONS = {"watch", "select", "buy", "hold", "sell", "avoid"}
STANCES = {"bullish", "bearish", "neutral", "uncertain"}
CODE_PATTERN = re.compile(r"^(?:sh|sz)\d{6}$")


class NewsSourceError(RuntimeError):
    """公开资讯源暂时不可用。"""


def _iso_date(value, field_name="日期") -> str:
    try:
        return date.fromisoformat(str(value or "").strip()).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 YYYY-MM-DD") from exc


def _iso_time(value, field_name="时刻", allow_empty=False) -> Optional[str]:
    text = str(value or "").strip()
    if not text and allow_empty:
        return None
    if re.fullmatch(r"\d{2}:\d{2}", text):
        text += ":00"
    try:
        return time.fromisoformat(text).replace(microsecond=0).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 HH:MM 或 HH:MM:SS") from exc


def classify_phase(published_at: str) -> str:
    """按 A 股看盘习惯将资讯分为早盘、午间、收盘三个时段。"""
    moment = datetime.fromisoformat(published_at).time()
    if moment < time(9, 30):
        return "pre_open"
    if moment < time(13, 0):
        return "midday"
    return "close"


def _bounded_text(value, limit: int, field: str, required=False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field}不能为空")
    if len(text) > limit:
        raise ValueError(f"{field}不能超过 {limit} 个字符")
    return text


@dataclass(frozen=True)
class FetchResult:
    items: list[dict]
    source_newest_at: Optional[str]
    source_oldest_at: Optional[str]
    complete: bool
    message: str = ""


class TonghuashunImportantNewsClient:
    """同花顺 7x24 公开“重要”资讯适配器。"""

    def __init__(self, timeout=12, page_size=200, max_pages=30, session=None):
        self.timeout = timeout
        self.page_size = min(max(int(page_size), 20), 200)
        self.max_pages = min(max(int(max_pages), 1), 100)
        self.session = session or requests.Session()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/114.0.0.0 Safari/537.36"
            ),
            "Referer": THS_PUBLIC_PAGE_URL,
        }

    def fetch_day(self, market_date: str) -> FetchResult:
        target = date.fromisoformat(_iso_date(market_date))
        start = datetime.combine(target, time.min, tzinfo=SHANGHAI_TZ)
        end = datetime.combine(target, time.max, tzinfo=SHANGHAI_TZ)
        today = datetime.now(SHANGHAI_TZ).date()
        items = {}
        source_newest = None
        source_oldest = None
        reached_target = False
        exhausted = False

        for page in range(1, self.max_pages + 1):
            try:
                response = self.session.get(
                    THS_IMPORTANT_NEWS_URL,
                    params={
                        "page": page,
                        "pagesize": self.page_size,
                        "tag": "-21101",
                        "track": "website",
                    },
                    headers=self.headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("data", {}).get("list") or []
            except (requests.RequestException, ValueError, TypeError) as exc:
                raise NewsSourceError(f"同花顺重要资讯暂时不可用: {exc}") from exc

            if not rows:
                exhausted = True
                break

            page_times = []
            for row in rows:
                try:
                    moment = datetime.fromtimestamp(int(row["rtime"]), SHANGHAI_TZ)
                except (KeyError, TypeError, ValueError, OSError):
                    continue
                page_times.append(moment)
                source_newest = max(source_newest or moment, moment)
                source_oldest = min(source_oldest or moment, moment)
                if start <= moment <= end:
                    item = self._normalize(row, moment)
                    items[item["id"]] = item
                    reached_target = True

            if page_times and min(page_times) < start:
                exhausted = True
                break

            total = payload.get("data", {}).get("total")
            try:
                if page * self.page_size >= int(total):
                    exhausted = True
                    break
            except (TypeError, ValueError):
                pass

        complete = exhausted and target < today
        if target == today:
            message = "当日资讯仍在持续发布，页面会按缓存时效刷新"
        elif reached_target:
            message = "已读取该日同花顺重要资讯"
        elif exhausted:
            message = "同花顺公开重要资讯窗口未覆盖该日期"
        else:
            message = "达到抓取页数上限，尚未完整覆盖该日期"
        return FetchResult(
            items=sorted(items.values(), key=lambda item: item["published_at"]),
            source_newest_at=(source_newest.replace(tzinfo=None).isoformat(timespec="seconds")
                              if source_newest else None),
            source_oldest_at=(source_oldest.replace(tzinfo=None).isoformat(timespec="seconds")
                              if source_oldest else None),
            complete=complete,
            message=message,
        )

    @staticmethod
    def _normalize(row: dict, moment: datetime) -> dict:
        published_at = moment.replace(tzinfo=None).isoformat(timespec="seconds")
        tags = []
        for value in row.get("tags") or []:
            name = str(value.get("name") or "").strip() if isinstance(value, dict) else ""
            if name and name not in tags:
                tags.append(name)
        source = str(row.get("source") or "同花顺7×24").strip()
        return {
            "id": str(row.get("id") or row.get("seq") or "").strip(),
            "seq": str(row.get("seq") or "").strip(),
            "title": str(row.get("title") or "").strip()[:500],
            "digest": str(row.get("digest") or row.get("short") or "").strip()[:5000],
            "url": str(row.get("url") or row.get("shareUrl") or "").strip()[:1000],
            "source": source[:120],
            "published_at": published_at,
            "market_date": moment.date().isoformat(),
            "phase": classify_phase(published_at),
            "tags": tags[:20],
            "importance": int(row.get("import") or 0),
        }


class MarketNewsRepository:
    """资讯本地缓存和用户影响记录仓储。"""

    def __init__(
        self, path=DEFAULT_DB_PATH, client=None, refresh_seconds=180,
        *, read_only=False,
    ):
        self.path = Path(path)
        self.read_only = bool(read_only)
        if not self.read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.client = client if self.read_only else (client or TonghuashunImportantNewsClient())
        self.refresh_seconds = max(int(refresh_seconds), 30)
        if not self.read_only:
            self._initialize()

    @contextmanager
    def _connect(self):
        if self.read_only:
            if not self.path.exists():
                raise FileNotFoundError(f"资讯缓存不存在: {self.path.name}")
            connection = sqlite3.connect(
                f"file:{self.path.resolve()}?mode=ro", uri=True, timeout=20
            )
        else:
            connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        if self.read_only:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=20000")
        try:
            yield connection
            if not self.read_only:
                connection.commit()
        except Exception:
            if not self.read_only:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS market_news (
                    id TEXT PRIMARY KEY,
                    seq TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    digest TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL,
                    market_date TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    importance INTEGER NOT NULL DEFAULT 0,
                    cached_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_news_date_time
                    ON market_news(market_date,published_at);

                CREATE TABLE IF NOT EXISTS market_news_fetch_log (
                    market_date TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    complete INTEGER NOT NULL DEFAULT 0,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    source_newest_at TEXT,
                    source_oldest_at TEXT,
                    message TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS market_news_impact (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    news_id TEXT NOT NULL REFERENCES market_news(id),
                    market_date TEXT NOT NULL,
                    decision_time TEXT,
                    code TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    stance TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_market_news_impact_date
                    ON market_news_impact(market_date,decision_time,id);
                CREATE INDEX IF NOT EXISTS idx_market_news_impact_user_id
                    ON market_news_impact(user_id);
                """
            )
            admin_id = 1
            users_db = PROJECT_DIR / "state" / "users.sqlite3"
            if users_db.exists():
                from data.users import UserRepository
                found = UserRepository().get_first_admin_id()
                if found:
                    admin_id = found
            ensure_user_id_column(connection, "market_news_impact", admin_id)

    def day(
        self, market_date, *, as_of=None, refresh=False,
        include_announcements=False,
    ) -> dict:
        if self.read_only:
            if refresh:
                raise PermissionError("只读资讯仓储禁止刷新")
            return self.cached_day(
                market_date,
                as_of=as_of,
                include_announcements=include_announcements,
            )
        market_date = _iso_date(market_date)
        target_date = date.fromisoformat(market_date)
        if target_date > datetime.now(SHANGHAI_TZ).date():
            raise ValueError("不能查询未来资讯")
        as_of_time = _iso_time(as_of, "截止时刻", allow_empty=True)
        warning = ""
        if refresh or self._needs_refresh(market_date):
            try:
                self._fetch_and_store(market_date)
            except NewsSourceError as exc:
                if not self._has_items(market_date):
                    raise
                warning = f"实时刷新失败，已显示本地缓存：{exc}"

        return self._query_cached_day(
            market_date,
            as_of_time=as_of_time,
            include_announcements=include_announcements,
            warning=warning,
        )

    def cached_day(
        self, market_date, *, as_of=None, phase=None,
        include_announcements=False,
    ) -> dict:
        """仅查询已有 SQLite 缓存，绝不抓取、建表或更新抓取日志。"""
        market_date = _iso_date(market_date)
        target_date = date.fromisoformat(market_date)
        if target_date > datetime.now(SHANGHAI_TZ).date():
            raise ValueError("不能查询未来资讯")
        as_of_time = _iso_time(as_of, "截止时刻", allow_empty=True)
        if phase not in (None, *PHASES):
            raise ValueError(f"phase 只能是 {', '.join(PHASES)}")
        return self._query_cached_day(
            market_date,
            as_of_time=as_of_time,
            phase=phase,
            include_announcements=include_announcements,
        )

    def _query_cached_day(
        self, market_date: str, *, as_of_time=None, phase=None,
        include_announcements=False, warning="",
    ) -> dict:
        params = [market_date]
        clauses = ["market_date=?"]
        if not include_announcements:
            clauses.append("tags_json NOT LIKE '%\"公告\"%'")
        if as_of_time:
            clauses.append("published_at<=?")
            params.append(f"{market_date}T{as_of_time}")
        if phase:
            clauses.append("phase=?")
            params.append(phase)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM market_news WHERE " + " AND ".join(clauses)
                + " ORDER BY published_at,id",
                params,
            ).fetchall()
            log = connection.execute(
                "SELECT * FROM market_news_fetch_log WHERE market_date=?", (market_date,)
            ).fetchone()
        items = [self._decode_news(row) for row in rows]
        counts = {phase: 0 for phase in PHASES}
        for item in items:
            counts[item["phase"]] += 1
        log_payload = dict(log) if log else {}
        return {
            "market_date": market_date,
            "as_of": as_of_time,
            "items": items,
            "counts": counts,
            "phase_labels": PHASE_LABELS,
            "source": "同花顺7×24重要资讯",
            "source_url": THS_PUBLIC_PAGE_URL,
            "fetched_at": log_payload.get("fetched_at"),
            "complete": bool(log_payload.get("complete", 0)),
            "message": warning or log_payload.get("message", ""),
        }

    def _needs_refresh(self, market_date: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fetched_at,complete FROM market_news_fetch_log WHERE market_date=?",
                (market_date,),
            ).fetchone()
        if row is None:
            return True
        if row["complete"]:
            return False
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
        except ValueError:
            return True
        return datetime.now() - fetched >= timedelta(seconds=self.refresh_seconds)

    def _has_items(self, market_date: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM market_news WHERE market_date=? LIMIT 1", (market_date,)
            ).fetchone()
        return row is not None

    def _fetch_and_store(self, market_date: str):
        result = self.client.fetch_day(market_date)
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            for item in result.items:
                if not item.get("id") or not item.get("title"):
                    continue
                connection.execute(
                    """INSERT INTO market_news
                       (id,seq,title,digest,url,source,published_at,market_date,phase,
                        tags_json,importance,cached_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET
                         seq=excluded.seq,title=excluded.title,digest=excluded.digest,
                         url=excluded.url,source=excluded.source,
                         published_at=excluded.published_at,market_date=excluded.market_date,
                         phase=excluded.phase,tags_json=excluded.tags_json,
                         importance=excluded.importance,cached_at=excluded.cached_at""",
                    (
                        item["id"], item.get("seq", ""), item["title"],
                        item.get("digest", ""), item.get("url", ""),
                        item.get("source", ""), item["published_at"], market_date,
                        item["phase"], json.dumps(item.get("tags", []), ensure_ascii=False),
                        int(item.get("importance", 0)), timestamp,
                    ),
                )
            count = connection.execute(
                "SELECT COUNT(*) FROM market_news WHERE market_date=?", (market_date,)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO market_news_fetch_log
                   (market_date,fetched_at,complete,item_count,source_newest_at,
                    source_oldest_at,message) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(market_date) DO UPDATE SET
                     fetched_at=excluded.fetched_at,complete=excluded.complete,
                     item_count=excluded.item_count,
                     source_newest_at=excluded.source_newest_at,
                     source_oldest_at=excluded.source_oldest_at,message=excluded.message""",
                (
                    market_date, timestamp, int(result.complete), count,
                    result.source_newest_at, result.source_oldest_at, result.message,
                ),
            )

    @staticmethod
    def _decode_news(row: sqlite3.Row) -> dict:
        item = dict(row)
        try:
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
        except json.JSONDecodeError:
            item["tags"] = []
        item["time"] = item["published_at"][11:19]
        item.pop("cached_at", None)
        return item

    def impacts(self, *, user_id, market_date, code=None, include_deleted=False) -> list[dict]:
        user_id = require_user_id(user_id)
        market_date = _iso_date(market_date)
        clauses = ["i.user_id=?", "i.market_date=?"]
        params = [user_id, market_date]
        if code:
            normalized = self._code(code)
            clauses.append("(i.code=? OR i.code='')")
            params.append(normalized)
        if not include_deleted:
            clauses.append("i.deleted_at IS NULL")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT i.*,n.title AS news_title,n.published_at,n.url
                   FROM market_news_impact i JOIN market_news n ON n.id=i.news_id
                   WHERE """ + " AND ".join(clauses)
                + " ORDER BY COALESCE(i.decision_time,'23:59:59'),i.id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def add_impact(
        self, *, user_id, news_id, market_date, action, stance, note,
        decision_time=None, code="",
    ) -> dict:
        user_id = require_user_id(user_id)
        news_id = _bounded_text(news_id, 80, "资讯编号", required=True)
        market_date = _iso_date(market_date)
        action = str(action or "").strip()
        stance = str(stance or "").strip()
        if action not in ACTIONS:
            raise ValueError("未知的决策动作")
        if stance not in STANCES:
            raise ValueError("未知的消息倾向")
        note = _bounded_text(note, 2000, "影响记录", required=True)
        decision_time = _iso_time(decision_time, "决策时刻", allow_empty=True)
        code = self._code(code) if str(code or "").strip() else ""
        timestamp = datetime.now().isoformat(timespec="seconds")
        with self._connect() as connection:
            news = connection.execute(
                "SELECT id,market_date,published_at FROM market_news WHERE id=?", (news_id,)
            ).fetchone()
            if news is None:
                raise LookupError("资讯不存在，请先加载对应日期")
            if news["market_date"] != market_date:
                raise ValueError("影响记录日期必须与资讯发布日期一致")
            if decision_time and decision_time < news["published_at"][11:19]:
                raise ValueError("决策时刻不能早于资讯发布时间")
            cursor = connection.execute(
                """INSERT INTO market_news_impact
                   (user_id,news_id,market_date,decision_time,code,action,stance,note,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, news_id, market_date, decision_time, code, action, stance, note, timestamp),
            )
            impact_id = cursor.lastrowid
        return self.get_impact(impact_id, user_id=user_id)

    def get_impact(self, impact_id, *, user_id=None) -> dict:
        try:
            impact_id = int(impact_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("影响记录编号必须是整数") from exc
        if user_id is not None:
            user_id = require_user_id(user_id)
        with self._connect() as connection:
            if user_id is None:
                row = connection.execute(
                    """SELECT i.*,n.title AS news_title,n.published_at,n.url
                       FROM market_news_impact i JOIN market_news n ON n.id=i.news_id
                       WHERE i.id=?""",
                    (impact_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """SELECT i.*,n.title AS news_title,n.published_at,n.url
                       FROM market_news_impact i JOIN market_news n ON n.id=i.news_id
                       WHERE i.id=? AND i.user_id=?""",
                    (impact_id, user_id),
                ).fetchone()
        if row is None:
            raise LookupError("影响记录不存在")
        return dict(row)

    def delete_impact(self, impact_id, *, user_id=None) -> dict:
        impact = self.get_impact(impact_id, user_id=user_id)
        if impact["deleted_at"] is None:
            deleted_at = datetime.now().isoformat(timespec="seconds")
            with self._connect() as connection:
                connection.execute(
                    "UPDATE market_news_impact SET deleted_at=? WHERE id=?",
                    (deleted_at, impact["id"]),
                )
        return self.get_impact(impact["id"])

    @staticmethod
    def _code(value) -> str:
        text = str(value or "").strip().lower().replace(".", "")
        if CODE_PATTERN.fullmatch(text):
            return text
        digits = re.sub(r"\D", "", text)
        if len(digits) == 6:
            return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits
        raise ValueError("证券代码格式必须类似 sh600519")
