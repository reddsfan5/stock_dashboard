"""公开资讯源的低频、礼貌抓取适配器。

设计原则：
- 只请求各站公开 JSON 列表，不登录、不并发轰炸、不绕过付费墙。
- 默认请求间隔 ≥ 1.5s；遇 429/5xx 指数退避；单次运行有硬上限页数/请求数。
- 进程内按主机互斥等待，避免短时间连打。
- 输出字段兼容 MarketNewsRepository.ingest（title/digest/published_at/url/...）。
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timezone
from typing import Callable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; stock-research-bot/1.0; "
    "+local-research; polite; low-frequency)"
)

_host_last_request: dict[str, float] = {}
_host_lock = threading.Lock()


@dataclass(frozen=True)
class SourceFetchResult:
    source_key: str
    items: list[dict]
    complete: bool
    message: str
    request_count: int


class PoliteHttpSession:
    """带最小间隔、重试退避和每轮请求上限的 HTTP 会话。"""

    def __init__(
        self,
        *,
        min_interval: float = 1.5,
        timeout: float = 12.0,
        max_retries: int = 3,
        max_requests: int = 20,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if min_interval < 1.0:
            raise ValueError("min_interval 不得低于 1.0 秒")
        if max_requests < 1 or max_requests > 40:
            raise ValueError("单次运行 max_requests 须在 1～40")
        self.min_interval = float(min_interval)
        self.timeout = float(timeout)
        self.max_retries = max(0, min(int(max_retries), 5))
        self.max_requests = int(max_requests)
        self.request_count = 0
        self.sleeper = sleeper
        self.clock = clock
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json,text/plain,*/*",
            }
        )

    def get_json(
        self,
        url: str,
        *,
        params: Optional[dict] = None,
        referer: str = "",
    ) -> dict:
        if self.request_count >= self.max_requests:
            raise RuntimeError(
                f"已达本轮请求上限 {self.max_requests}，停止继续抓取（礼貌限流）"
            )
        host = urlparse(url).netloc
        headers = {"Referer": referer} if referer else None
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot(host)
            try:
                response = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
                self.request_count += 1
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}", response=response
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("响应不是 JSON 对象")
                return payload
            except (requests.RequestException, ValueError, TypeError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleeper(min(16.0, 2.0 ** (attempt + 1)))
        raise RuntimeError(f"公开接口暂时不可用: {last_error}") from last_error

    def _wait_for_slot(self, host: str) -> None:
        with _host_lock:
            now = self.clock()
            last = _host_last_request.get(host, 0.0)
            wait = self.min_interval - (now - last)
            if wait > 0:
                self.sleeper(wait)
            _host_last_request[host] = self.clock()


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _shanghai_naive(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(SHANGHAI_TZ).replace(tzinfo=None)


def _day_bounds(market_date: str) -> tuple[datetime, datetime, date]:
    target = date.fromisoformat(str(market_date).strip())
    start = datetime.combine(target, dt_time.min)
    end = datetime.combine(target, dt_time.max.replace(microsecond=0))
    return start, end, target


class EastmoneyFlashNewsClient:
    """东方财富快讯公开列表（column=102）。"""

    API_URL = "https://newsapi.eastmoney.com/kuaixun/v2/api/list"
    PAGE_URL = "https://kuaixun.eastmoney.com/"

    def __init__(
        self,
        http: Optional[PoliteHttpSession] = None,
        *,
        page_size: int = 20,
        max_pages: int = 8,
        column: str = "102",
    ):
        self.http = http or PoliteHttpSession()
        self.page_size = min(max(int(page_size), 5), 40)
        self.max_pages = min(max(int(max_pages), 1), 15)
        self.column = str(column)

    def fetch_day(self, market_date: str) -> SourceFetchResult:
        start, end, target = _day_bounds(market_date)
        today = datetime.now(SHANGHAI_TZ).date()
        items: dict[str, dict] = {}
        reached = False
        passed_day = False
        exhausted = False

        for page in range(1, self.max_pages + 1):
            try:
                payload = self.http.get_json(
                    self.API_URL,
                    params={
                        "column": self.column,
                        "pageSize": self.page_size,
                        "pageIndex": page,
                    },
                    referer=self.PAGE_URL,
                )
            except RuntimeError as exc:
                if items:
                    return SourceFetchResult(
                        source_key="eastmoney",
                        items=sorted(items.values(), key=lambda x: x["published_at"]),
                        complete=False,
                        message=f"部分抓取后中止: {exc}",
                        request_count=self.http.request_count,
                    )
                raise

            rows = payload.get("news") or []
            if not rows:
                exhausted = True
                break

            oldest_on_page: Optional[datetime] = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                moment = self._parse_time(row.get("showtime") or row.get("showTime"))
                if moment is None:
                    continue
                oldest_on_page = (
                    moment if oldest_on_page is None else min(oldest_on_page, moment)
                )
                if start <= moment <= end:
                    item = self._normalize(row, moment)
                    items[item["id"]] = item
                    reached = True
                elif moment < start:
                    passed_day = True

            try:
                page_count = int(payload.get("PageCount") or 0)
            except (TypeError, ValueError):
                page_count = 0
            if page_count and page >= page_count:
                exhausted = True
                break
            if passed_day and oldest_on_page is not None and oldest_on_page < start:
                exhausted = True
                break

        complete = exhausted and target < today
        if target == today:
            message = "当日东财快讯仍在更新；本轮已按礼貌上限抓取"
        elif reached:
            message = "已读取该日东方财富公开快讯"
        elif exhausted:
            message = "公开列表窗口未覆盖该日期"
        else:
            message = "达到页数/请求上限，尚未完整覆盖该日期"

        return SourceFetchResult(
            source_key="eastmoney",
            items=sorted(items.values(), key=lambda x: x["published_at"]),
            complete=complete,
            message=message,
            request_count=self.http.request_count,
        )

    @staticmethod
    def _parse_time(value) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _normalize(row: dict, moment: datetime) -> dict:
        news_id = str(row.get("newsid") or row.get("id") or "").strip()
        title = str(row.get("title") or row.get("simtitle") or "").strip()
        digest = str(row.get("digest") or row.get("simdigest") or "").strip()
        url = str(
            row.get("url_unique") or row.get("url_w") or row.get("url_m") or ""
        ).strip()
        if url.startswith("http://"):
            url = "https://" + url[len("http://") :]
        if not news_id:
            news_id = hashlib.sha1(
                f"eastmoney|{moment.isoformat()}|{title}".encode("utf-8")
            ).hexdigest()[:16]
        return {
            "id": news_id,
            "seq": str(row.get("sort") or "").strip(),
            "title": (title or digest[:80] or f"东财快讯 {news_id}")[:500],
            "digest": digest[:5000],
            "url": url[:1000],
            "source": "东方财富快讯",
            "original_source": "东方财富",
            "published_at": moment.isoformat(timespec="seconds"),
            "market_date": moment.date().isoformat(),
            "tags": [],
            "importance": 0,
        }


class WallstreetcnLiveClient:
    """华尔街见闻 7×24 公开直播流。"""

    API_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
    PAGE_URL = "https://wallstreetcn.com/live/global"

    def __init__(
        self,
        http: Optional[PoliteHttpSession] = None,
        *,
        limit: int = 20,
        max_pages: int = 8,
        channel: str = "global-channel",
    ):
        self.http = http or PoliteHttpSession()
        self.limit = min(max(int(limit), 5), 40)
        self.max_pages = min(max(int(max_pages), 1), 15)
        self.channel = channel

    def fetch_day(self, market_date: str) -> SourceFetchResult:
        start, end, target = _day_bounds(market_date)
        today = datetime.now(SHANGHAI_TZ).date()
        items: dict[str, dict] = {}
        reached = False
        exhausted = False
        cursor: Optional[str] = None

        for _ in range(self.max_pages):
            params: dict = {"channel": self.channel, "limit": self.limit}
            if cursor:
                params["cursor"] = cursor
            try:
                payload = self.http.get_json(
                    self.API_URL, params=params, referer=self.PAGE_URL
                )
            except RuntimeError as exc:
                if items:
                    return SourceFetchResult(
                        source_key="wallstreetcn",
                        items=sorted(items.values(), key=lambda x: x["published_at"]),
                        complete=False,
                        message=f"部分抓取后中止: {exc}",
                        request_count=self.http.request_count,
                    )
                raise

            data = payload.get("data") or {}
            rows = data.get("items") or []
            if not rows:
                exhausted = True
                break

            oldest_on_page: Optional[datetime] = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                moment = self._parse_time(row.get("display_time"))
                if moment is None:
                    continue
                oldest_on_page = (
                    moment if oldest_on_page is None else min(oldest_on_page, moment)
                )
                if start <= moment <= end:
                    item = self._normalize(row, moment)
                    items[item["id"]] = item
                    reached = True

            next_cursor = data.get("next_cursor")
            if oldest_on_page is not None and oldest_on_page < start:
                exhausted = True
                break
            if not next_cursor or str(next_cursor) == str(cursor or ""):
                exhausted = True
                break
            cursor = str(next_cursor)

        complete = exhausted and target < today
        if target == today:
            message = "当日见闻快讯仍在更新；本轮已按礼貌上限抓取"
        elif reached:
            message = "已读取该日华尔街见闻公开快讯"
        elif exhausted:
            message = "公开列表窗口未覆盖该日期"
        else:
            message = "达到页数/请求上限，尚未完整覆盖该日期"

        return SourceFetchResult(
            source_key="wallstreetcn",
            items=sorted(items.values(), key=lambda x: x["published_at"]),
            complete=complete,
            message=message,
            request_count=self.http.request_count,
        )

    @staticmethod
    def _parse_time(value) -> Optional[datetime]:
        try:
            ts = int(value)
        except (TypeError, ValueError):
            return None
        if ts > 10_000_000_000:
            ts //= 1000
        moment = datetime.fromtimestamp(ts, tz=timezone.utc)
        return _shanghai_naive(moment)

    @staticmethod
    def _normalize(row: dict, moment: datetime) -> dict:
        news_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or row.get("highlight_title") or "").strip()
        digest = _strip_html(
            str(row.get("content_text") or row.get("content") or "")
        )
        if not title:
            title = digest[:80] or f"华尔街见闻快讯 {news_id}"
        url = str(row.get("uri") or "").strip()
        if url and not url.startswith("http"):
            url = f"https://wallstreetcn.com/livenews/{news_id}" if news_id else ""
        elif not url and news_id:
            url = f"https://wallstreetcn.com/livenews/{news_id}"
        tags: list[str] = []
        for tag in row.get("tags") or []:
            name = str(tag.get("name") if isinstance(tag, dict) else tag or "").strip()
            if name and name not in tags:
                tags.append(name)
        for channel in row.get("channels") or []:
            name = str(channel or "").strip()
            if name and name not in tags:
                tags.append(name)
        try:
            importance = min(max(int(row.get("score") or 0), 0), 10)
        except (TypeError, ValueError):
            importance = 0
        if not news_id:
            news_id = hashlib.sha1(
                f"wallstreetcn|{moment.isoformat()}|{title}".encode("utf-8")
            ).hexdigest()[:16]
        return {
            "id": news_id,
            "seq": news_id,
            "title": title[:500],
            "digest": digest[:5000],
            "url": url[:1000],
            "source": "华尔街见闻",
            "original_source": "华尔街见闻",
            "published_at": moment.isoformat(timespec="seconds"),
            "market_date": moment.date().isoformat(),
            "tags": tags[:20],
            "importance": importance,
        }


FETCHERS = {
    "eastmoney": EastmoneyFlashNewsClient,
    "wallstreetcn": WallstreetcnLiveClient,
}


def fetch_source(
    source_key: str,
    market_date: str,
    *,
    min_interval: float = 1.5,
    max_pages: int = 8,
    max_requests: int = 20,
) -> SourceFetchResult:
    """按来源抓取某一交易日的公开资讯。"""
    key = str(source_key or "").strip().lower()
    if key not in FETCHERS:
        raise ValueError(
            f"不支持的适配器: {source_key}（可选: {', '.join(sorted(FETCHERS))}）"
        )
    http = PoliteHttpSession(min_interval=min_interval, max_requests=max_requests)
    client = FETCHERS[key](http=http, max_pages=max_pages)
    return client.fetch_day(market_date)
