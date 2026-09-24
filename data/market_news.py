"""多来源市场资讯缓存、事件聚合与决策影响记录。

仅保存公开资讯的标题、摘要、发布时间和原文链接，不复制全文。所有查询都支持
``as_of`` 截止时刻；事件的来源数量和验证等级也只根据截止时刻前的记录计算，供
无剧透交易训练使用。
"""

from __future__ import annotations

from data.users import ensure_user_id_column, require_user_id
import json
import hashlib
from difflib import SequenceMatcher
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
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

SOURCE_CATALOG = {
    "official": {"name": "官方与交易所", "tier": "official", "mode": "导入/引用", "url": "https://www.cninfo.com.cn/"},
    "tonghuashun": {"name": "同花顺", "tier": "financial_media", "mode": "自动刷新", "url": THS_PUBLIC_PAGE_URL},
    "eastmoney": {"name": "东方财富", "tier": "financial_media", "mode": "公开API低频抓取", "url": "https://kuaixun.eastmoney.com/"},
    "wallstreetcn": {"name": "华尔街见闻", "tier": "financial_media", "mode": "公开API低频抓取", "url": "https://wallstreetcn.com/live/global"},
    "kaipanla": {"name": "开盘啦", "tier": "financial_media", "mode": "公开来源导入", "url": "https://www.kaipanla.com/"},
    "jiuyan": {"name": "韭研公社", "tier": "community", "mode": "研究线索导入", "url": "https://www.jiuyangongshe.com/"},
}
SOURCE_TIERS = {"official", "authoritative", "financial_media", "community", "unverified"}
VERIFICATION_LABELS = {
    "verified": "官方确认",
    "corroborated": "权威印证",
    "cross_reported": "多源报道",
    "reported": "单源报道",
    "research": "社区线索",
}


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
        return dt_time.fromisoformat(text).replace(microsecond=0).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 HH:MM 或 HH:MM:SS") from exc


def classify_phase(published_at: str) -> str:
    """按 A 股看盘习惯将资讯分为早盘、午间、收盘三个时段。"""
    moment = datetime.fromisoformat(published_at).time()
    if moment < dt_time(9, 30):
        return "pre_open"
    if moment < dt_time(13, 0):
        return "midday"
    return "close"


def _bounded_text(value, limit: int, field: str, required=False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field}不能为空")
    if len(text) > limit:
        raise ValueError(f"{field}不能超过 {limit} 个字符")
    return text


def _safe_url(value, field="原文链接") -> str:
    url = _bounded_text(value, 1000, field)
    if url and urlparse(url).scheme not in {"http", "https"}:
        raise ValueError(f"{field}仅允许 http/https")
    return url


def _event_text(value: str) -> str:
    text = re.sub(r"[【】\[\]（）()《》<>“”‘’：:，,。.!！?？·|｜—_\-\s]", "", str(value or "").lower())
    return re.sub(r"^(快讯|突发|重磅|盘中播报|午间公告集锦)", "", text)[:160]


def _event_id(title: str, market_date: str) -> str:
    raw = f"{market_date}|{_event_text(title)}".encode("utf-8")
    return "evt_" + hashlib.sha1(raw).hexdigest()[:16]


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
        start = datetime.combine(target, dt_time.min, tzinfo=SHANGHAI_TZ)
        end = datetime.combine(target, dt_time.max, tzinfo=SHANGHAI_TZ)
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

            # 页间礼貌停顿，避免连续翻页过快。
            if page > 1:
                time.sleep(1.2)

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
                    platform_key TEXT NOT NULL DEFAULT 'tonghuashun',
                    platform_name TEXT NOT NULL DEFAULT '同花顺',
                    source_tier TEXT NOT NULL DEFAULT 'financial_media',
                    original_source TEXT NOT NULL DEFAULT '',
                    event_id TEXT NOT NULL DEFAULT '',
                    verification_status TEXT NOT NULL DEFAULT 'reported',
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

                CREATE TABLE IF NOT EXISTS market_news_source_status (
                    market_date TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    complete INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(market_date,source_key)
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
            self._migrate_news_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_news_date_platform "
                "ON market_news(market_date,platform_key,published_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_news_event "
                "ON market_news(event_id,published_at)"
            )
            admin_id = 1
            users_db = PROJECT_DIR / "state" / "users.sqlite3"
            if users_db.exists():
                from data.users import UserRepository
                found = UserRepository().get_first_admin_id()
                if found:
                    admin_id = found
            ensure_user_id_column(connection, "market_news_impact", admin_id)

    @staticmethod
    def _migrate_news_columns(connection):
        """兼容已存在的单来源数据库，迁移只增加有默认值的列。"""
        existing = {row[1] for row in connection.execute("PRAGMA table_info(market_news)")}
        additions = {
            "platform_key": "TEXT NOT NULL DEFAULT 'tonghuashun'",
            "platform_name": "TEXT NOT NULL DEFAULT '同花顺'",
            "source_tier": "TEXT NOT NULL DEFAULT 'financial_media'",
            "original_source": "TEXT NOT NULL DEFAULT ''",
            "event_id": "TEXT NOT NULL DEFAULT ''",
            "verification_status": "TEXT NOT NULL DEFAULT 'reported'",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE market_news ADD COLUMN {name} {definition}")

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
        events = self._cluster_events(items)
        counts = {phase: 0 for phase in PHASES}
        for item in items:
            counts[item["phase"]] += 1
        log_payload = dict(log) if log else {}
        return {
            "market_date": market_date,
            "as_of": as_of_time,
            "items": items,
            "events": events,
            "counts": counts,
            "phase_labels": PHASE_LABELS,
            "source": "多来源市场资讯中心",
            "source_url": THS_PUBLIC_PAGE_URL,
            "source_summary": self._source_summary(items),
            "source_catalog": self.source_catalog(),
            "verification_counts": self._verification_counts(events),
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
                normalized = self._normalize_import_item(item, "tonghuashun", preserve_id=True)
                self._upsert_item(connection, normalized, timestamp)
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
            connection.execute(
                """INSERT INTO market_news_source_status
                   (market_date,source_key,source_name,fetched_at,item_count,complete,message)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(market_date,source_key) DO UPDATE SET
                     source_name=excluded.source_name,fetched_at=excluded.fetched_at,
                     item_count=excluded.item_count,complete=excluded.complete,
                     message=excluded.message""",
                (market_date, "tonghuashun", SOURCE_CATALOG["tonghuashun"]["name"],
                 timestamp, len(result.items), int(result.complete), result.message),
            )

    def ingest(self, items, *, source_key: str, complete=True, message="") -> dict:
        """导入经过公开渠道收集的标准化资讯，不在仓储内发起网络请求。"""
        if self.read_only:
            raise PermissionError("只读资讯仓储禁止导入")
        source_key = str(source_key or "").strip().lower()
        if source_key not in SOURCE_CATALOG:
            raise ValueError("未知资讯来源")
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError("items 必须是最多 500 条的数组")
        normalized = [self._normalize_import_item(item, source_key) for item in items]
        timestamp = datetime.now().isoformat(timespec="seconds")
        dates = sorted({item["market_date"] for item in normalized})
        with self._connect() as connection:
            for item in normalized:
                self._upsert_item(connection, item, timestamp)
            for market_date in dates:
                count = sum(item["market_date"] == market_date for item in normalized)
                spec = SOURCE_CATALOG[source_key]
                connection.execute(
                    """INSERT INTO market_news_source_status
                       (market_date,source_key,source_name,fetched_at,item_count,complete,message)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(market_date,source_key) DO UPDATE SET
                         source_name=excluded.source_name,fetched_at=excluded.fetched_at,
                         item_count=excluded.item_count,complete=excluded.complete,
                         message=excluded.message""",
                    (market_date, source_key, spec["name"], timestamp, count,
                     int(bool(complete)), _bounded_text(message, 500, "导入说明")),
                )
        return {"source_key": source_key, "imported": len(normalized), "market_dates": dates}

    @staticmethod
    def source_catalog() -> list[dict]:
        return [dict(key=key, **value) for key, value in SOURCE_CATALOG.items()]

    @staticmethod
    def _normalize_import_item(item, source_key: str, preserve_id=False) -> dict:
        if not isinstance(item, dict):
            raise ValueError("每条资讯必须是对象")
        spec = SOURCE_CATALOG[source_key]
        title = _bounded_text(item.get("title"), 500, "标题", required=True)
        digest = _bounded_text(item.get("digest") or item.get("summary"), 5000, "摘要")
        raw_time = _bounded_text(item.get("published_at"), 40, "发布时间", required=True)
        try:
            moment = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("发布时间必须是 ISO 8601") from exc
        if moment.tzinfo is not None:
            moment = moment.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
        published_at = moment.isoformat(timespec="seconds")
        market_date = moment.date().isoformat()
        supplied_date = str(item.get("market_date") or market_date).strip()
        if _iso_date(supplied_date) != market_date:
            raise ValueError("资讯日期必须与北京时间发布时间一致")
        source_tier = str(item.get("source_tier") or spec["tier"]).strip()
        if source_tier not in SOURCE_TIERS:
            raise ValueError("未知来源等级")
        raw_id = _bounded_text(item.get("id") or item.get("seq"), 120, "资讯编号")
        if not raw_id:
            raw_id = hashlib.sha1(f"{source_key}|{published_at}|{title}".encode("utf-8")).hexdigest()[:20]
        item_id = raw_id if preserve_id else f"{source_key}:{raw_id}"
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("tags 必须是数组")
        tags = [_bounded_text(value, 40, "标签") for value in tags if str(value or "").strip()][:20]
        original_source = _bounded_text(
            item.get("original_source") or item.get("source") or spec["name"], 120, "原始信源"
        )
        verification = str(item.get("verification_status") or "").strip()
        if verification not in VERIFICATION_LABELS:
            verification = "verified" if source_tier == "official" else (
                "corroborated" if source_tier == "authoritative" else
                "research" if source_tier == "community" else "reported"
            )
        return {
            "id": item_id,
            "seq": _bounded_text(item.get("seq"), 120, "序号"),
            "title": title,
            "digest": digest,
            "url": _safe_url(item.get("url")),
            "source": _bounded_text(item.get("source") or spec["name"], 120, "来源"),
            "published_at": published_at,
            "market_date": market_date,
            "phase": classify_phase(published_at),
            "tags": tags,
            "importance": min(max(int(item.get("importance") or 0), 0), 10),
            "platform_key": source_key,
            "platform_name": spec["name"],
            "source_tier": source_tier,
            "original_source": original_source,
            "event_id": _event_id(title, market_date),
            "verification_status": verification,
        }

    @staticmethod
    def _upsert_item(connection, item: dict, timestamp: str):
        connection.execute(
            """INSERT INTO market_news
               (id,seq,title,digest,url,source,published_at,market_date,phase,
                tags_json,importance,platform_key,platform_name,source_tier,
                original_source,event_id,verification_status,cached_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 seq=excluded.seq,title=excluded.title,digest=excluded.digest,
                 url=excluded.url,source=excluded.source,
                 published_at=excluded.published_at,market_date=excluded.market_date,
                 phase=excluded.phase,tags_json=excluded.tags_json,
                 importance=excluded.importance,platform_key=excluded.platform_key,
                 platform_name=excluded.platform_name,source_tier=excluded.source_tier,
                 original_source=excluded.original_source,event_id=excluded.event_id,
                 verification_status=excluded.verification_status,cached_at=excluded.cached_at""",
            (
                item["id"], item.get("seq", ""), item["title"], item.get("digest", ""),
                item.get("url", ""), item.get("source", ""), item["published_at"],
                item["market_date"], item["phase"],
                json.dumps(item.get("tags", []), ensure_ascii=False),
                int(item.get("importance", 0)), item["platform_key"], item["platform_name"],
                item["source_tier"], item.get("original_source", ""), item["event_id"],
                item["verification_status"], timestamp,
            ),
        )

    @staticmethod
    def _source_summary(items: list[dict]) -> list[dict]:
        grouped = {}
        for item in items:
            key = item.get("platform_key") or "tonghuashun"
            group = grouped.setdefault(key, {
                "key": key,
                "name": item.get("platform_name") or item.get("source") or key,
                "tier": item.get("source_tier") or "financial_media",
                "count": 0,
            })
            group["count"] += 1
        return sorted(grouped.values(), key=lambda row: (-row["count"], row["name"]))

    @staticmethod
    def _verification_counts(events: list[dict]) -> dict:
        counts = {key: 0 for key in VERIFICATION_LABELS}
        for event in events:
            status = event.get("verification_status", "reported")
            counts[status] = counts.get(status, 0) + 1
        return counts

    @classmethod
    def _cluster_events(cls, items: list[dict]) -> list[dict]:
        """基于当前可见项目做轻量事件聚合，避免未来来源数量泄露。"""
        clusters = []
        for item in sorted(items, key=lambda row: row["published_at"]):
            normalized = _event_text(item["title"])
            chosen = None
            for cluster in clusters:
                other = cluster["_normalized"]
                if normalized == other or (
                    min(len(normalized), len(other)) >= 8
                    and SequenceMatcher(None, normalized, other).ratio() >= 0.72
                ):
                    chosen = cluster
                    break
            if chosen is None:
                chosen = {"_normalized": normalized, "items": []}
                clusters.append(chosen)
            chosen["items"].append(item)

        result = []
        tier_rank = {"official": 0, "authoritative": 1, "financial_media": 2, "community": 3, "unverified": 4}
        for cluster in clusters:
            rows = cluster["items"]
            platforms = sorted({row.get("platform_key") or "tonghuashun" for row in rows})
            best = min(rows, key=lambda row: (tier_rank.get(row.get("source_tier"), 9), -int(row.get("importance") or 0)))
            tiers = {row.get("source_tier") for row in rows}
            if "official" in tiers:
                status = "verified"
            elif "authoritative" in tiers:
                status = "corroborated"
            elif len(platforms) >= 2:
                status = "cross_reported"
            elif tiers == {"community"}:
                status = "research"
            else:
                status = best.get("verification_status") or "reported"
            result.append({
                "event_id": best.get("event_id") or _event_id(best["title"], best["market_date"]),
                "title": best["title"],
                "digest": best.get("digest", ""),
                "market_date": best["market_date"],
                "phase": rows[0]["phase"],
                "first_published_at": rows[0]["published_at"],
                "last_published_at": rows[-1]["published_at"],
                "time": rows[0]["time"],
                "verification_status": status,
                "verification_label": VERIFICATION_LABELS[status],
                "source_count": len(platforms),
                "platforms": [{"key": key, "name": SOURCE_CATALOG.get(key, {}).get("name", key)} for key in platforms],
                "original_source": best.get("original_source") or best.get("source", ""),
                "tags": list(dict.fromkeys(tag for row in rows for tag in row.get("tags", [])))[:12],
                "importance": max(int(row.get("importance") or 0) for row in rows),
                "primary_news_id": best["id"],
                "items": rows,
            })
        return sorted(result, key=lambda row: (row["first_published_at"], row["event_id"]))

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
