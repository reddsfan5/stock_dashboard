#!/usr/bin/env python3
"""选股日记工作台：日K查询、SQLite 日记服务和页面生成。"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from data.journal import JournalRepository
from data.schema import derive_previous_close
from scripts.services.intraday_replay import inject_intraday_replay


PROJECT_DIR = Path(__file__).resolve().parents[2]
OUT_HTML = PROJECT_DIR / "output" / "stock_journal.html"
TEMPLATE_HTML = Path(__file__).resolve().parent / "templates" / "stock_journal.html"


def normalize_code(code: str) -> str:
    value = str(code or "").strip().lower().replace(".", "")
    if re.fullmatch(r"(sh|sz)\d{6}", value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) != 6:
        raise ValueError("请输入6位证券代码")
    return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits


def _finite(value, digits=4):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


class DailyKlineRepository:
    """按证券代码过滤读取日K，避免为单次查看加载整个行情缓存。"""

    def __init__(self, name_map=None):
        self.name_map = dict(name_map or {})

    @staticmethod
    def _source(code: str):
        from data.etf import CACHE_FILE as ETF_CACHE_FILE
        from data.kline import CACHE_FILE as STOCK_CACHE_FILE

        raw = code[2:]
        if raw.startswith(("1", "5")):
            return Path(ETF_CACHE_FILE), raw
        return Path(STOCK_CACHE_FILE), code

    def payload(self, code: str, days=180) -> dict:
        code = normalize_code(code)
        try:
            days = max(30, min(int(days), 1000))
        except (TypeError, ValueError):
            raise ValueError("日K天数必须是整数")
        path, stored_code = self._source(code)
        if not path.exists():
            raise LookupError("本地日K缓存不存在")
        columns = [
            "日期", "开盘", "最高", "最低", "收盘", "前收",
            "成交量", "成交额", "换手率%",
        ]
        try:
            frame = pd.read_parquet(
                path, columns=columns, filters=[("代码", "==", stored_code)]
            )
        except Exception as exc:
            raise RuntimeError(f"读取日K缓存失败: {exc}")
        if frame.empty:
            raise LookupError(f"{code} 在本地日K缓存中没有数据")
        frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
        frame = frame.dropna(subset=["日期", "开盘", "最高", "最低", "收盘"])
        frame = frame.drop_duplicates("日期", keep="last").sort_values("日期")
        # 旧缓存的“前收”可能为空；必须在 tail 之前回退计算，才能让返回窗口
        # 第一根 K 线也尽量获得上一交易日收盘价。
        frame["_previous_close"] = derive_previous_close(frame)
        for window in (5, 10, 20, 60):
            frame[f"ma{window}"] = frame["收盘"].rolling(window).mean()
        frame = frame.tail(days)
        bars = []
        for _, row in frame.iterrows():
            previous = _finite(row.get("_previous_close"))
            close = _finite(row["收盘"])
            change = (
                round((close / previous - 1) * 100, 3)
                if close is not None and previous not in (None, 0) else None
            )
            bars.append({
                "date": row["日期"].strftime("%Y-%m-%d"),
                "open": _finite(row["开盘"]),
                "high": _finite(row["最高"]),
                "low": _finite(row["最低"]),
                "close": close,
                "pre_close": previous,
                "change_pct": change,
                "volume": _finite(row.get("成交量"), 2),
                "amount": _finite(row.get("成交额"), 2),
                "turnover_rate": _finite(row.get("换手率%"), 3),
                "ma5": _finite(row.get("ma5")),
                "ma10": _finite(row.get("ma10")),
                "ma20": _finite(row.get("ma20")),
                "ma60": _finite(row.get("ma60")),
            })
        latest = bars[-1]
        return {
            "code": code,
            "name": self.name_map.get(code, code),
            "days": days,
            "bars": bars,
            "latest": latest,
        }

    def search(self, query: str, limit=20) -> list:
        q = str(query or "").strip().lower()
        if not q:
            return []
        try:
            normalized = normalize_code(q)
        except ValueError:
            normalized = ""
        digits = re.sub(r"\D", "", q)
        ranked = []
        for code, display_name in self.name_map.items():
            code = str(code).lower()
            if not re.fullmatch(r"(sh|sz)\d{6}", code):
                continue
            raw = code[2:]
            name = str(display_name or code)
            lowered_name = name.lower()
            if q not in code and q not in raw and q not in lowered_name and normalized != code:
                continue
            score = 0 if normalized == code or (digits and digits == raw) else 1 if lowered_name == q else 2
            ranked.append((score, code, name))
        # 即使档案缓存缺少代码，仍允许用户通过精确代码打开日K。
        if normalized and not any(code == normalized for _, code, _ in ranked):
            ranked.append((0, normalized, normalized))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [
            {"code": code, "name": name}
            for _, code, name in ranked[: max(1, min(int(limit), 50))]
        ]


class StockJournalService:
    """把行情查询与日记仓储组合成供 HTTP Handler 调用的门面。"""

    def __init__(self, name_map=None, db_path=None):
        self.market = DailyKlineRepository(name_map)
        self.repository = JournalRepository(db_path) if db_path else JournalRepository()

    def search(self, query, limit=20):
        return self.market.search(query, limit)

    def kline(self, code, days=180):
        return self.market.payload(code, days)

    def list_cases(self, **filters):
        if "user_id" not in filters:
            raise ValueError("user_id 必填")
        return self.repository.list_cases(**filters)

    def get_case(self, case_id, *, user_id):
        return self.repository.get_case(case_id, user_id=user_id)

    def list_entries(self, **filters):
        if "user_id" not in filters:
            raise ValueError("user_id 必填")
        return self.repository.list_entries(**filters)

    def delete_entry(self, payload, *, user_id):
        return self.repository.soft_delete_entry(
            payload.get("entry_id"), payload.get("reason", ""), user_id=user_id
        )

    def restore_entry(self, payload, *, user_id):
        return self.repository.restore_entry(payload.get("entry_id"), user_id=user_id)

    def create_case(self, payload, *, user_id):
        return self.repository.create_case(
            user_id=user_id,
            code=normalize_code(payload.get("code", "")),
            title=payload.get("title", ""),
            thesis=payload.get("thesis", ""),
            tags=payload.get("tags"),
            source=payload.get("source", "manual"),
        )

    def add_entry(self, payload, *, user_id):
        allowed = {
            "price", "trigger_condition", "invalidation_condition", "target_price",
            "stop_price", "planned_position_pct", "planned_holding_days",
            "next_review_date", "tags", "source", "context", "supersedes_entry_id",
        }
        extra = {key: payload.get(key) for key in allowed if key in payload}
        entry = self.repository.add_entry(
            user_id=user_id,
            case_id=payload.get("case_id"),
            event_type=payload.get("event_type"),
            market_date=payload.get("market_date"),
            reason=payload.get("reason"),
            **extra,
        )
        return {
            "entry": entry,
            "case": self.repository.get_case(entry["case_id"], user_id=user_id),
        }

    def due_reviews(self, as_of=None, limit=100, *, user_id):
        return self.repository.due_reviews(user_id=user_id, as_of=as_of, limit=limit)

    def backup(self):
        path = self.repository.backup()
        return {"path": str(path), "created": True}


def build_html() -> str:
    return inject_intraday_replay(TEMPLATE_HTML.read_text(encoding="utf-8"))


def write_app(path=OUT_HTML):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(), encoding="utf-8")
    print(f"✓ 选股日记: {path}")


if __name__ == "__main__":
    write_app()
    print("  交互查询与记录需要服务：python -m scripts.serve start journal")
