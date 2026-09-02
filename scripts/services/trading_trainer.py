#!/usr/bin/env python3
"""T+1 无剧透手动交易训练页与服务端会话管理。"""

import math
import os
import re
import secrets
import threading
import time
from typing import Dict

import pandas as pd

from backtest.execution import ExecutionConfig
from backtest.trading_trainer import TrainerConfig, TradingTrainerSession
from data.minute import CACHE_FILE as MINUTE_CACHE_FILE
from data.schema import derive_previous_close


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_HTML = os.path.join(PROJECT_DIR, "output", "trading_trainer.html")
TEMPLATE_HTML = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "templates", "trading_trainer.html"
)


def _normalize_code(code: str) -> str:
    value = str(code or "").strip().lower().replace(".", "")
    if re.fullmatch(r"(sh|sz)\d{6}", value):
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) != 6:
        raise ValueError("请输入 6 位证券代码")
    return ("sh" if digits.startswith(("5", "6", "9")) else "sz") + digits


def load_daily_history(code: str, before_date: str, days: int) -> list:
    """只读取模拟日之前的日 K；当前日由已播放分钟实时合成。"""
    from data.etf import CACHE_FILE as ETF_CACHE_FILE
    from data.kline import CACHE_FILE as STOCK_CACHE_FILE

    target = pd.Timestamp(before_date)
    raw = code[2:]
    sources = (
        (ETF_CACHE_FILE, raw) if raw.startswith(("1", "5")) else
        (STOCK_CACHE_FILE, code)
    )
    path, stored_code = sources
    if not os.path.exists(path):
        return []
    frame = pd.read_parquet(
        path,
        columns=[
            "日期", "开盘", "最高", "最低", "收盘", "前收",
            "成交量", "成交额", "换手率%",
        ],
        filters=[("代码", "==", stored_code), ("日期", "<", target)],
    )
    if frame.empty:
        return []
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce")
    frame = frame.dropna(subset=["日期", "开盘", "最高", "最低", "收盘"])
    frame = frame.drop_duplicates("日期", keep="last").sort_values("日期")
    # 先基于完整可见历史推导前收，再截取展示窗口，避免第一根展示 K 线
    # 因原始“前收”为空而被错误渲染成 +0.00%。
    frame["_previous_close"] = derive_previous_close(frame)
    frame = frame.tail(days)
    return [
        {
            "date": row["日期"].strftime("%Y-%m-%d"),
            "open": round(float(row["开盘"]), 6),
            "high": round(float(row["最高"]), 6),
            "low": round(float(row["最低"]), 6),
            "close": round(float(row["收盘"]), 6),
            "pre_close": (
                round(float(row["_previous_close"]), 6)
                if pd.notna(row["_previous_close"]) else None
            ),
            "change_pct": (
                round(
                    (float(row["收盘"]) / float(row["_previous_close"]) - 1) * 100,
                    4,
                )
                if pd.notna(row["_previous_close"])
                and float(row["_previous_close"]) > 0 else None
            ),
            "volume": (
                round(float(row["成交量"]), 2) if pd.notna(row["成交量"]) else None
            ),
            "amount": round(float(row["成交额"]), 2),
            "turnover_rate": (
                round(float(row["换手率%"]), 4) if pd.notna(row["换手率%"]) else None
            ),
            "partial": False,
        }
        for _, row in frame.iterrows()
    ]


class TradingTrainerService:
    """线程安全的本地训练会话仓库。"""

    def __init__(self, minute_repository, max_sessions: int = 100):
        self.repository = minute_repository
        self.max_sessions = max_sessions
        self.sessions: Dict[str, Dict] = {}
        self.lock = threading.RLock()

    def dates(self, code: str) -> dict:
        normalized = _normalize_code(code)
        dates = self._complete_dates(normalized)
        if not dates:
            raise LookupError(f"{normalized} 在分钟缓存中没有完整交易日数据")
        return {
            "code": normalized,
            "name": self.repository.name_map.get(normalized, normalized),
            "dates": dates,
        }

    @staticmethod
    def _complete_dates(code: str) -> list:
        """训练只使用覆盖开盘至收盘且至少 200 根的分钟日。"""
        if not os.path.exists(MINUTE_CACHE_FILE):
            return []
        frame = pd.read_parquet(
            MINUTE_CACHE_FILE, columns=["时间"], filters=[("代码", "==", code)]
        )
        if frame.empty:
            return []
        frame["时间"] = pd.to_datetime(frame["时间"])
        frame["日期"] = frame["时间"].dt.strftime("%Y-%m-%d")
        quality = frame.groupby("日期")["时间"].agg(["count", "min", "max"])
        usable = quality[
            (quality["count"] >= 200)
            & (quality["min"].dt.strftime("%H:%M") <= "09:35")
            & (quality["max"].dt.strftime("%H:%M") >= "14:55")
        ]
        return usable.index.sort_values().tolist()

    def create(self, payload: dict) -> dict:
        meta = self.dates(payload.get("code", ""))
        requested_date = str(payload.get("start_date") or "random")
        if requested_date == "random":
            candidates = meta["dates"][:-1] or meta["dates"]
            start_date = secrets.choice(candidates)
        elif requested_date in meta["dates"]:
            start_date = requested_date
        else:
            raise ValueError("起始交易日不在该标的分钟缓存中")

        capital = _number(payload, "capital", 100_000, minimum=1)
        commission_bps = _number(payload, "commission_bps", 3, minimum=0)
        min_commission = _number(payload, "min_commission", 5, minimum=0)
        is_etf = meta["code"][2:].startswith(("1", "5"))
        sell_tax_bps = _number(
            payload, "sell_tax_bps", 0 if is_etf else 5, minimum=0
        )
        slippage_bps = _number(payload, "slippage_bps", 0, minimum=0)
        session = TradingTrainerSession(
            code=meta["code"], name=meta["name"], dates=meta["dates"],
            start_date=start_date,
            day_loader=self.repository.payload,
            history_loader=load_daily_history,
            config=TrainerConfig(
                initial_capital=capital,
                execution=ExecutionConfig(
                    commission_rate=commission_bps / 10_000,
                    min_commission=min_commission,
                    stamp_tax_rate=sell_tax_bps / 10_000,
                    slippage_bps=slippage_bps,
                    lot_size=100,
                    price_tick=0.001 if is_etf else 0.01,
                ),
            ),
        )
        session_id = secrets.token_urlsafe(24)
        with self.lock:
            self._prune()
            self.sessions[session_id] = {
                "session": session, "last_access": time.time(),
            }
        state = session.state()
        state["session_id"] = session_id
        return state

    def state(self, session_id: str) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(session_id, session.state())

    def advance(self, session_id: str, steps: int) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(session_id, session.advance(steps))

    def next_day(self, session_id: str) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(session_id, session.next_day())

    def order(
        self,
        session_id: str,
        side: str,
        shares,
        note: str = "",
        order_type: str = "market",
        limit_price=None,
        validity: str = "day",
    ) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(
                session_id,
                session.place_order(
                    side, shares, note, order_type, limit_price, validity
                ),
            )

    def cancel_pending_order(self, session_id: str, order_id: str) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(
                session_id, session.cancel_pending_order(order_id)
            )

    def conditional_order(
        self,
        session_id: str,
        side: str,
        condition_type: str,
        trigger_value,
        shares,
        note: str = "",
        validity: str = "day",
        secondary_trigger_value=None,
    ) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(
                session_id,
                session.create_conditional_order(
                    side, condition_type, trigger_value, shares, note, validity,
                    secondary_trigger_value,
                ),
            )

    def cancel_conditional_order(self, session_id: str, condition_id: str) -> dict:
        with self.lock:
            session = self._get(session_id)
            return self._with_id(
                session_id, session.cancel_conditional_order(condition_id)
            )

    def _get(self, session_id: str) -> TradingTrainerSession:
        item = self.sessions.get(str(session_id or ""))
        if item is None:
            raise LookupError("训练会话不存在或服务已经重启，请重新开始")
        item["last_access"] = time.time()
        return item["session"]

    def _prune(self) -> None:
        expired_before = time.time() - 6 * 60 * 60
        expired = [
            key for key, item in self.sessions.items()
            if item["last_access"] < expired_before
        ]
        for key in expired:
            del self.sessions[key]
        while len(self.sessions) >= self.max_sessions:
            oldest = min(
                self.sessions, key=lambda key: self.sessions[key]["last_access"]
            )
            del self.sessions[oldest]

    @staticmethod
    def _with_id(session_id: str, state: dict) -> dict:
        state["session_id"] = session_id
        return state


def _number(payload: dict, key: str, default: float, minimum: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须是数字")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{key} 不能小于 {minimum:g}")
    return value


def build_html() -> str:
    """读取独立页面模板。"""
    with open(TEMPLATE_HTML, "r", encoding="utf-8") as file:
        return file.read()


def write_app(path: str = OUT_HTML):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(build_html())
    print(f"✓ T+1 训练页: {path}")


if __name__ == "__main__":
    write_app()
    print("  交互训练需要服务端状态，请运行: python -m scripts.serve start trainer")
