#!/usr/bin/env python3
"""观察池 / 次日跟踪 / 板块强度服务与页面。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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
        return self.repo.add(
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

    def list_items(self, **kwargs) -> dict:
        if "user_id" not in kwargs:
            raise ValueError("user_id 必填")
        items = self.repo.list_items(**kwargs)
        return {"items": items, "count": len(items)}

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
