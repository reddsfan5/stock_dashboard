#!/usr/bin/env python3
"""标的统一上下文：选股命中、观察池、训练、日记、假设、选股→交易笔记。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from data.hypotheses import (
    HypothesisRepository,
    STATUS_LABELS as HYPOTHESIS_STATUS_LABELS,
    STATUSES as HYPOTHESIS_STATUSES,
    normalize_code,
)
from data.journal import JournalRepository
from data.training_sessions import TrainingSessionRepository
from data.watchlist import WatchlistRepository


PROJECT_DIR = Path(__file__).resolve().parents[2]
OUT_HTML = PROJECT_DIR / "output" / "symbol.html"
TEMPLATE_HTML = Path(__file__).resolve().parent / "templates" / "symbol.html"
DASHBOARD_HTML = PROJECT_DIR / "output" / "dashboard.html"
SCREEN_TO_TRADE_JSON = PROJECT_DIR / "output" / "screen_to_trade_report.json"
SCREEN_TO_TRADE_CSV = PROJECT_DIR / "output" / "screen_to_trade_trades.csv"

MODULE_TITLES = {
    "continuity": "K线连续性",
    "hammer": "金针探底",
    "sideways": "横盘震荡",
    "trend-up": "连续上涨",
    "trend-down": "连续下跌",
    "upward_gap": "持续推高",
    "long_shadow": "长下影线",
}


def _mtime_iso(path: Path) -> Optional[str]:
    try:
        return pd.Timestamp(path.stat().st_mtime, unit="s").tz_localize("UTC").tz_convert(
            "Asia/Shanghai"
        ).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return None


def load_screen_hits(code: str, dashboard_path: Path = DASHBOARD_HTML) -> dict:
    """从最新 dashboard.html 的 TAB_CODES 解析该代码命中的选股模块。"""
    empty = {
        "code": code,
        "modules": [],
        "dashboard_mtime": None,
        "message": "暂无选股仪表盘或未命中",
    }
    if not dashboard_path.exists():
        return empty
    try:
        text = dashboard_path.read_text(encoding="utf-8")
    except OSError:
        return empty
    match = re.search(r"var TAB_CODES\s*=\s*(\{.*?\});", text, re.S)
    if not match:
        empty["dashboard_mtime"] = _mtime_iso(dashboard_path)
        return empty
    try:
        tab_codes = json.loads(match.group(1))
    except json.JSONDecodeError:
        empty["dashboard_mtime"] = _mtime_iso(dashboard_path)
        empty["message"] = "仪表盘 TAB_CODES 解析失败"
        return empty
    hits = []
    for module_id, codes in tab_codes.items():
        if code in (codes or []):
            hits.append(
                {
                    "module": module_id,
                    "title": MODULE_TITLES.get(module_id, module_id),
                }
            )
    return {
        "code": code,
        "modules": hits,
        "dashboard_mtime": _mtime_iso(dashboard_path),
        "message": None if hits else "最新选股结果未命中该代码",
    }


def load_screen_to_trade_note(code: str) -> dict:
    """可选：最近选股→交易回测中该代码的样本摘要。"""
    note = {
        "available": False,
        "generated_at": None,
        "module": None,
        "module_title": None,
        "trade_count": 0,
        "avg_net_return_pct": None,
        "recent_trades": [],
        "message": "暂无选股→交易报告",
    }
    if SCREEN_TO_TRADE_JSON.exists():
        try:
            payload = json.loads(SCREEN_TO_TRADE_JSON.read_text(encoding="utf-8"))
            note["generated_at"] = payload.get("generated_at")
            card = payload.get("report_card") or {}
            note["module"] = card.get("module")
            note["module_title"] = card.get("module_title")
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    if not SCREEN_TO_TRADE_CSV.exists():
        return note
    try:
        frame = pd.read_csv(SCREEN_TO_TRADE_CSV)
    except (OSError, ValueError):
        return note
    if "代码" not in frame.columns:
        return note
    subset = frame[frame["代码"].astype(str).str.lower() == code].copy()
    if subset.empty:
        note["message"] = "该代码未出现在最近选股→交易样本中"
        note["available"] = bool(note.get("generated_at"))
        return note
    returns = pd.to_numeric(subset.get("净收益%"), errors="coerce")
    recent = subset.sort_values("信号日", ascending=False).head(5)
    trades = []
    for _, row in recent.iterrows():
        trades.append(
            {
                "signal_date": str(row.get("信号日") or ""),
                "entry_date": str(row.get("入场日") or ""),
                "exit_date": str(row.get("退出日") or ""),
                "net_return_pct": None
                if pd.isna(row.get("净收益%"))
                else round(float(row.get("净收益%")), 3),
                "exit_reason": str(row.get("退出原因") or ""),
            }
        )
    note.update(
        {
            "available": True,
            "trade_count": int(len(subset)),
            "avg_net_return_pct": None
            if returns.dropna().empty
            else round(float(returns.mean()), 3),
            "recent_trades": trades,
            "message": None,
        }
    )
    return note


class SymbolContextService:
    """聚合各既有仓储，供 /api/symbol/context 与标的页使用。"""

    def __init__(
        self,
        *,
        name_map=None,
        watchlist: Optional[WatchlistRepository] = None,
        training: Optional[TrainingSessionRepository] = None,
        journal: Optional[JournalRepository] = None,
        hypotheses: Optional[HypothesisRepository] = None,
    ):
        self.name_map = dict(name_map or {})
        self.watchlist = watchlist or WatchlistRepository()
        self.training = training or TrainingSessionRepository()
        self.journal = journal or JournalRepository()
        self.hypotheses = hypotheses or HypothesisRepository()

    def _name(self, code: str) -> str:
        return self.name_map.get(code) or self.name_map.get(normalize_code(code)) or ""

    def context(self, code: str, *, user_id) -> dict:
        code = normalize_code(code)
        name = self._name(code)
        if not name:
            try:
                from data.industry import StockInfo

                info = StockInfo().df
                row = info.loc[info["代码"] == code]
                if len(row):
                    name = str(row.iloc[0].get("名称") or "")
            except Exception:  # noqa: BLE001
                name = ""

        watch_items = self.watchlist.list_items(user_id=user_id, code=code, limit=20)
        tracks = []
        try:
            tracks = self.watchlist.list_tracks(user_id=user_id, code=code, limit=20)
        except Exception:  # noqa: BLE001
            tracks = []

        training_runs = []
        try:
            training_runs = self.training.list_runs_for_code(code, user_id=user_id, limit=10)
        except Exception:  # noqa: BLE001
            training_runs = []

        cases = []
        try:
            cases = self.journal.list_cases(user_id=user_id, code=code, limit=20)
        except Exception:  # noqa: BLE001
            cases = []

        hypotheses = self.hypotheses.list_for_code(code, user_id=user_id, limit=20)
        screen_hits = load_screen_hits(code)
        screen_to_trade = load_screen_to_trade_note(code)

        return {
            "code": code,
            "name": name or code,
            "screen_hits": screen_hits,
            "watchlist": {"items": watch_items, "count": len(watch_items)},
            "tracks": {"items": tracks, "count": len(tracks)},
            "training": {
                "runs": training_runs,
                "count": len(training_runs),
                "decision_count": sum(int(r.get("decision_count") or 0) for r in training_runs),
            },
            "journal": {"cases": cases, "count": len(cases)},
            "hypotheses": {
                "items": hypotheses,
                "count": len(hypotheses),
                "statuses": list(HYPOTHESIS_STATUSES),
                "status_labels": dict(HYPOTHESIS_STATUS_LABELS),
            },
            "screen_to_trade": screen_to_trade,
            "links": {
                "minute": f"/minute_view.html?code={code}",
                "journal": f"/stock_journal.html?code={code}",
                "trainer": f"/trading_trainer.html?code={code}",
                "watchlist": "/watchlist.html",
                "dashboard": "/dashboard.html",
                "symbol": f"/symbol.html?code={code}",
            },
        }

    def create_hypothesis(self, payload: dict, *, user_id) -> dict:
        code = normalize_code(payload.get("code", ""))
        return self.hypotheses.create(
            user_id=user_id,
            code=code,
            title=payload.get("title") or "",
            status=payload.get("status") or "hypothesis",
            thesis=payload.get("thesis") or "",
            note=payload.get("note") or "",
            source=payload.get("source") or "symbol_page",
            meta=payload.get("meta") or {},
        )

    def update_hypothesis(self, payload: dict, *, user_id) -> dict:
        return self.hypotheses.set_status(
            int(payload.get("id") or payload.get("hypothesis_id")),
            payload.get("status", ""),
            user_id=user_id,
            note=payload.get("note"),
            thesis=payload.get("thesis"),
            title=payload.get("title"),
        )


def build_html() -> str:
    return TEMPLATE_HTML.read_text(encoding="utf-8")


def write_app(path: Path = OUT_HTML):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(), encoding="utf-8")
    print(f"✓ {path}")
