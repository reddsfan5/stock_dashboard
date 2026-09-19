#!/usr/bin/env python3
"""观察池 / 次日跟踪 / 板块强度服务与页面。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from data.watchlist import (
    WatchlistRepository,
    normalize_code,
    rank_sector_strength,
)


PROJECT_DIR = Path(__file__).resolve().parents[2]
OUT_HTML = PROJECT_DIR / "output" / "watchlist.html"
TEMPLATE_HTML = Path(__file__).resolve().parent / "templates" / "watchlist.html"


class WatchlistService:
    def __init__(self, repository: Optional[WatchlistRepository] = None, name_map=None):
        self.repo = repository or WatchlistRepository()
        self.name_map = dict(name_map or {})

    def _name(self, code: str) -> str:
        return self.name_map.get(code) or self.name_map.get(normalize_code(code)) or ""

    def add(self, payload: dict, *, user_id) -> dict:
        code = normalize_code(payload.get("code", ""))
        name = payload.get("name") or self._name(code)
        item = self.repo.add(
            user_id=user_id,
            code=code,
            name=name,
            status=payload.get("status") or "watching",
            source_module=payload.get("source_module") or payload.get("module") or "",
            screen_date=payload.get("screen_date") or payload.get("date"),
            thesis=payload.get("thesis") or "",
            note=payload.get("note") or "",
            meta=payload.get("meta") or {},
        )
        # 主动加入后立即补算该条目的收益结果，避免监控页要等到下一次
        # 每日更新才出现新标的。监控是旁路功能，失败不能阻断加入观察池。
        try:
            self.refresh_monitor(user_id=user_id, item_ids=[item["id"]])
        except Exception as exc:  # noqa: BLE001
            try:
                from data.watchlist_monitor import WatchlistMonitorRepository

                WatchlistMonitorRepository(self.repo.path).record_failure(user_id, exc)
            except Exception:
                pass
        return item

    def list_items(self, **kwargs) -> dict:
        if "user_id" not in kwargs:
            raise ValueError("user_id 必填")
        items = self.repo.list_items(**kwargs)
        return {"items": items, "count": len(items)}

    def search_symbols(self, query: str, *, limit: int = 20) -> list[dict]:
        """在本地证券名称目录中做轻量模糊匹配。

        观察池录入不要求用户先知道完整名称；代码、简称或名称片段都可以。
        目录来自服务启动时加载的本地名称缓存，不会触发行情更新或网络请求。
        """
        needle = re.sub(r"\s+", "", str(query or "").strip().lower())
        if not needle:
            return []
        limit = max(1, min(int(limit), 50))
        digits = re.sub(r"\D", "", needle)
        rows = []
        for raw_code, raw_name in self.name_map.items():
            code = str(raw_code or "").strip().lower()
            if not re.fullmatch(r"(?:sh|sz)\d{6}", code):
                continue
            name = str(raw_name or "").strip()
            compact_name = re.sub(r"\s+", "", name.lower())
            raw_digits = code[2:]
            if needle not in code and needle not in raw_digits and needle not in compact_name:
                continue
            if needle in {code, raw_digits} or (digits and digits == raw_digits):
                score = 0
            elif needle == compact_name:
                score = 1
            elif code.startswith(needle) or raw_digits.startswith(needle) or compact_name.startswith(needle):
                score = 2
            else:
                score = 3
            rows.append((score, name, code, {"code": code, "name": name}))

        # 直接输入完整代码时，即使名称缓存暂时缺失，也允许继续录入。
        if not rows and re.fullmatch(r"(?:(?:sh|sz)\d{6}|\d{6})", needle):
            try:
                code = normalize_code(needle)
            except ValueError:
                code = ""
            if code:
                rows.append((0, self._name(code), code, {"code": code, "name": self._name(code)}))

        rows.sort(key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in rows[:limit]]

    def set_status(self, payload: dict, *, user_id) -> dict:
        return self.repo.set_status(
            int(payload.get("id") or payload.get("item_id")),
            payload.get("status", ""),
            user_id=user_id,
            note=payload.get("note"),
        )

    def delete(self, payload: dict, *, user_id) -> dict:
        return self.repo.soft_delete(
            int(payload.get("id") or payload.get("item_id")), user_id=user_id
        )

    def tracks(self, **kwargs) -> dict:
        if "user_id" not in kwargs:
            raise ValueError("user_id 必填")
        rows = self.repo.list_tracks(**kwargs)
        return {"items": rows, "count": len(rows)}

    def refresh_tracking(self, *, user_id, as_of: str = None, fail_threshold_pct: float = -3.0) -> dict:
        from data.kline import StockData

        data = StockData()

        def loader(code: str):
            return data.get_kline(code, days=30)

        return self.repo.refresh_tracking(
            loader, user_id=user_id, as_of=as_of, fail_threshold_pct=fail_threshold_pct
        )

    def monitor(self, *, user_id, **kwargs) -> dict:
        """读取观察池按加入批次的收益监控结果。"""
        from data.watchlist_monitor import WatchlistMonitorRepository, WatchlistMonitorService

        repository = WatchlistMonitorRepository(self.repo.path)
        return WatchlistMonitorService(repository=repository, watchlist=self.repo).query(
            user_id=user_id, **kwargs
        )

    def refresh_monitor(
        self, *, user_id, as_of: str = None, rebuild: bool = False,
        item_ids: Optional[Iterable[int]] = None,
    ) -> dict:
        """刷新观察池收益监控；每日管线和主动加入后的旁路任务都会调用。"""
        from data.watchlist_monitor import WatchlistMonitorRepository, WatchlistMonitorService

        repository = WatchlistMonitorRepository(self.repo.path)
        return WatchlistMonitorService(repository=repository, watchlist=self.repo).refresh(
            user_id=user_id, as_of=as_of, rebuild=rebuild, item_ids=item_ids
        )

    def sector_strength(self, market_date: str = None, top_n: int = 15) -> dict:
        from data.industry import StockInfo
        from data.kline import StockData

        stock = StockData()
        cache = stock.cache
        if market_date is None:
            if cache is None or len(cache) == 0:
                return {"market_date": None, "sectors": [], "message": "无日K缓存"}
            market_date = pd.to_datetime(cache["日期"]).max().strftime("%Y-%m-%d")
        day = pd.Timestamp(market_date).normalize()
        # 只取当日附近两天，避免全表拷贝过大——仍可能较大，用布尔索引
        day_mask = pd.to_datetime(cache["日期"]).dt.normalize() == day
        daily = cache.loc[day_mask, ["代码", "日期", "收盘", "前收"]].copy()
        if "前收" not in daily.columns or daily["前收"].isna().all():
            # 回退：拉前后各一日算前收太贵；用 pct 若有
            if "涨跌幅%" in cache.columns:
                daily = cache.loc[day_mask, ["代码", "日期", "收盘", "涨跌幅%"]].copy()
        info = StockInfo().df
        return rank_sector_strength(daily, info, market_date=market_date, top_n=top_n)


def build_html() -> str:
    return TEMPLATE_HTML.read_text(encoding="utf-8")


def write_app(path: Path = OUT_HTML):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(), encoding="utf-8")
    print(f"✓ 观察池页面: {path}")
