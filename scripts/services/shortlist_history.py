"""每日精选历史页面的只读装配层。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from data.shortlist import DEFAULT_DB_PATH, ShortlistRepository
from scripts.reports.shortlist_cards import render_shortlist_html


def render_saved_shortlist(
    market_date: Optional[str] = None,
    *,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> str:
    """读取指定交易日快照并渲染；不重新计算历史候选。"""
    repository = ShortlistRepository(db_path)
    payload = repository.get(market_date)
    return render_shortlist_html(payload, history_dates=repository.dates())
